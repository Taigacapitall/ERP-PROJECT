from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest
from sqlalchemy import Column, String
from sqlalchemy.orm import sessionmaker

from vorpal.core import (
    AppConfig,
    AppContext,
    AuditLog,
    AuthorizationService,
    Base,
    BaseModule,
    ConflictError,
    Currency,
    Event,
    EventBus,
    InMemoryEventStore,
    InMemoryRateProvider,
    ModuleManifest,
    ModuleRegistry,
    ModuleState,
    Money,
    NotFoundError,
    Partner,
    Role,
    SchemaMigrationManager,
    ServiceRegistry,
    SqlAlchemyRepository,
    UnitOfWork,
    User,
    ValidationError,
    convert,
    create_db_engine,
)
from vorpal.core.contracts.dto import (
    InvoiceDTO,
    InvoiceLineDTO,
    JournalEntryDTO,
    JournalLineDTO,
    PartyInfoDTO,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED, INVOICE_CERTIFIED


@pytest.fixture
def app_ctx() -> Iterator[AppContext]:
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield AppContext(
        event_bus=EventBus(store=InMemoryEventStore()),
        services=ServiceRegistry(),
        config=AppConfig(env="test", db_url="sqlite:///:memory:"),
        db=engine,
        audit=AuditLog(),
        authz=AuthorizationService(),
    )
    engine.dispose()


def test_money_float_rejected_and_currency_mismatch() -> None:
    with pytest.raises(ValidationError):
        Money(amount=10.5, currency=Currency.USD)  # type: ignore[arg-type]

    m1 = Money(Decimal("10.00"), Currency.USD)
    m2 = Money(Decimal("5.00"), Currency.USD)
    cdf = Money(Decimal("5000.00"), Currency.CDF)

    assert (m1 + m2).amount == Decimal("15.00")
    assert (m1 - m2).amount == Decimal("5.00")
    assert (m1 * Decimal(2)).amount == Decimal("20.00")
    assert m1 < (m1 + m2)
    assert m1 <= (m1 + m2)
    assert (m1 + m2) > m1
    assert (m1 + m2) >= m1

    with pytest.raises(ValidationError):
        _ = m1 + cdf

    with pytest.raises(ValidationError):
        _ = m1 - cdf

    with pytest.raises(ValidationError):
        _ = m1 < cdf


def test_money_rounding_and_fx_conversion() -> None:
    m = Money(Decimal("12.346"), Currency.USD)
    assert m.round().amount == Decimal("12.35")

    provider = InMemoryRateProvider()
    dt = date(2026, 11, 20)
    provider.set_rate(Currency.USD, Currency.CDF, dt, Decimal("2850.50"))
    converted = convert(Money(Decimal("2.00"), Currency.USD), Currency.CDF, dt, provider)
    assert converted == Money(Decimal("5701.00"), Currency.CDF)


def test_registry_topological_cycle_missing_and_isolated_failure(app_ctx: AppContext) -> None:
    class DummyMod(BaseModule):
        def __init__(self, manifest: ModuleManifest, fail_start: bool = False) -> None:
            self.manifest = manifest
            self._fail_start = fail_start
            self.stopped = False

        def install(self, ctx: AppContext) -> None:
            pass

        def start(self, ctx: AppContext) -> None:
            if self._fail_start:
                raise RuntimeError("boom")

        def stop(self) -> None:
            self.stopped = True

        def healthcheck(self) -> bool:
            return True

    reg_missing = ModuleRegistry()
    reg_missing.register(DummyMod(ModuleManifest(name="a", version="1.0", depends=["missing"])))
    with pytest.raises(NotFoundError):
        reg_missing.resolve_order()

    reg_cycle = ModuleRegistry()
    reg_cycle.register(DummyMod(ModuleManifest(name="a", version="1.0", depends=["b"])))
    reg_cycle.register(DummyMod(ModuleManifest(name="b", version="1.0", depends=["a"])))
    with pytest.raises(ConflictError):
        reg_cycle.resolve_order()

    reg = ModuleRegistry()
    m_base = DummyMod(ModuleManifest(name="base_m", version="1.0"))
    m_fail = DummyMod(ModuleManifest(name="fail_m", version="1.0", depends=["base_m"]), fail_start=True)
    m_dep_fail = DummyMod(ModuleManifest(name="child_fail", version="1.0", depends=["fail_m"]))
    m_ok = DummyMod(ModuleManifest(name="other_ok", version="1.0", depends=["base_m"]))

    for m in (m_dep_fail, m_ok, m_fail, m_base):
        reg.register(m)

    reg.start_all(app_ctx)
    assert reg.get_state("base_m") == ModuleState.RUNNING
    assert reg.get_state("fail_m") == ModuleState.FAILED
    assert reg.get_state("child_fail") == ModuleState.FAILED
    assert reg.get_state("other_ok") == ModuleState.RUNNING

    reg.stop_all()
    assert m_base.stopped is True
    assert m_ok.stopped is True


def test_registry_start_order_follows_dependencies_and_stop_is_reversed(
    app_ctx: AppContext,
) -> None:
    events: list[str] = []

    class OrderedMod(BaseModule):
        def __init__(self, name: str, depends: list[str]) -> None:
            self.manifest = ModuleManifest(name=name, version="1.0", depends=depends)

        def install(self, ctx: AppContext) -> None:
            pass

        def start(self, ctx: AppContext) -> None:
            events.append(f"start:{self.manifest.name}")

        def stop(self) -> None:
            events.append(f"stop:{self.manifest.name}")

        def healthcheck(self) -> bool:
            return True

    reg = ModuleRegistry()
    # enregistrés volontairement dans le désordre : c -> b -> a
    reg.register(OrderedMod("c", ["b"]))
    reg.register(OrderedMod("b", ["a"]))
    reg.register(OrderedMod("a", []))

    assert reg.resolve_order() == ["a", "b", "c"]

    reg.start_all(app_ctx)
    reg.stop_all()
    assert events == ["start:a", "start:b", "start:c", "stop:c", "stop:b", "stop:a"]


def test_event_bus_isolation_idempotence_wildcard_and_replay() -> None:
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    received: list[str] = []
    wildcard_seen: list[str] = []
    failed_seen: list[str] = []

    def bad_handler(ev: Event) -> None:
        raise ValueError("handler failure")

    def good_handler(ev: Event) -> None:
        received.append(ev.id)

    def any_handler(ev: Event) -> None:
        wildcard_seen.append(ev.type)

    def on_failed(ev: Event) -> None:
        failed_seen.append(str(ev.payload["failed_event_id"]))

    bus.subscribe("demo.ping", bad_handler)
    bus.subscribe("demo.ping", good_handler)
    bus.subscribe("*", any_handler)
    bus.subscribe(EVENT_HANDLER_FAILED, on_failed)

    ev = Event(id="evt-1", type="demo.ping", payload={"x": 1})
    bus.publish(ev)
    bus.publish(ev)

    assert received == ["evt-1"]
    assert failed_seen == ["evt-1", "evt-1"]  # un échec par tentative (handler en échec = réessayé)
    assert "demo.ping" in wildcard_seen
    assert EVENT_HANDLER_FAILED in wildcard_seen

    count = bus.replay("demo.ping")
    assert count == 1
    assert received == ["evt-1", "evt-1"]


@runtime_checkable
class GreetingService(Protocol):
    def greet(self, name: str) -> str: ...


def test_two_modules_communicate_via_event_and_service_without_importing(app_ctx: AppContext) -> None:
    @dataclass
    class GreeterImpl:
        def greet(self, name: str) -> str:
            return f"Bonjour {name}"

    class ProviderMod(BaseModule):
        manifest = ModuleManifest(name="provider_mod", version="1.0")

        def install(self, ctx: AppContext) -> None:
            ctx.services.register(GreetingService, GreeterImpl())

        def start(self, ctx: AppContext) -> None:
            ctx.event_bus.subscribe(
                "greet.requested",
                lambda ev: ctx.event_bus.publish(
                    Event(
                        type="greet.completed",
                        payload={"message": ctx.services.get(GreetingService).greet(str(ev.payload["target"]))},
                    )
                ),
            )

        def stop(self) -> None:
            pass

        def healthcheck(self) -> bool:
            return True

    completed: list[str] = []

    class ConsumerMod(BaseModule):
        manifest = ModuleManifest(name="consumer_mod", version="1.0", depends=["provider_mod"])

        def install(self, ctx: AppContext) -> None:
            pass

        def start(self, ctx: AppContext) -> None:
            ctx.event_bus.subscribe(
                "greet.completed",
                lambda ev: completed.append(str(ev.payload["message"])),
            )

        def stop(self) -> None:
            pass

        def healthcheck(self) -> bool:
            return True

    reg = ModuleRegistry()
    reg.register(ProviderMod())
    reg.register(ConsumerMod())
    reg.start_all(app_ctx)

    app_ctx.event_bus.publish(Event(type="greet.requested", payload={"target": "Vorpal"}))
    assert completed == ["Bonjour Vorpal"]
    assert app_ctx.services.get_optional(GreetingService) is not None


def test_audit_chain_tamper_detection() -> None:
    audit = AuditLog()
    ts = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
    audit.record("admin", "CREATE", "invoice", "INV-1", after={"ttc": "116.00"}, timestamp=ts)
    audit.record("admin", "CERTIFY", "invoice", "INV-1", before={"status": "DRAFT"}, after={"status": "CERTIFIED"}, timestamp=ts)
    assert audit.verify_chain() is True

    tampered = audit._entries[0]
    audit._entries[0] = tampered.__class__(
        seq=tampered.seq,
        timestamp=tampered.timestamp,
        user=tampered.user,
        action=tampered.action,
        entity=tampered.entity,
        entity_id=tampered.entity_id,
        before=tampered.before,
        after={"ttc": "999.00"},
        prev_hash=tampered.prev_hash,
        entry_hash=tampered.entry_hash,
    )
    assert audit.verify_chain() is False


def test_db_uow_migrations_authz_partners_config_and_dto(tmp_path: Path) -> None:
    class SampleItem(Base):
        __tablename__ = "sample_items"
        id = Column(String(64), primary_key=True)
        label = Column(String(128), nullable=False)

    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        repo = SqlAlchemyRepository(uow.session, SampleItem)
        repo.add(SampleItem(id="1", label="Item One"))

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        repo = SqlAlchemyRepository(uow.session, SampleItem)
        assert len(repo.list_all()) == 1

    mgr = SchemaMigrationManager(engine)
    assert mgr.get_version("mod_x") == 0
    ver = mgr.apply("mod_x", {1: lambda eng: None, 2: lambda eng: None})
    assert ver == 2

    authz = AuthorizationService()
    authz.register_module_permissions("mod_x", ["mod_x.read"])
    assert authz.is_permission_declared("mod_x.read")
    role = Role(name="reader", permissions=frozenset({"mod_x.read"}))
    user = User(id="u1", username="alice", roles=(role,))
    assert authz.check(user, "mod_x.read") is True
    assert authz.check(user, "mod_x.write") is False

    partner = Partner(id="p1", name="Client SA", partner_type="client", nif="A1234567X")
    assert partner.partner_type == "client"

    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text('{"env": "test", "default_currency": "CDF"}', encoding="utf-8")
    cfg = AppConfig.load(cfg_file)
    assert cfg.default_currency == "CDF"

    inv = InvoiceDTO(
        number="FAC-001",
        invoice_date=date(2026, 11, 20),
        seller=PartyInfoDTO(name="Vendeur", nif="NIF-V"),
        customer=PartyInfoDTO(name="Client", nif="NIF-C"),
        lines=[InvoiceLineDTO(designation="Service", quantity=Decimal(1), unit_price_ht=Decimal("100.00"), vat_rate=Decimal("0.16"))],
        currency=Currency.USD,
        total_ht=Decimal("100.00"),
        total_vat=Decimal("16.00"),
        total_ttc=Decimal("116.00"),
    )
    assert inv.number == "FAC-001"

    je = JournalEntryDTO(
        entry_date=date(2026, 11, 20),
        journal="VT",
        label="Vente FAC-001",
        source_ref="FAC-001",
        lines=[
            JournalLineDTO(account="411100", debit=Decimal("116.00"), currency=Currency.USD),
            JournalLineDTO(account="701100", credit=Decimal("100.00"), currency=Currency.USD),
            JournalLineDTO(account="443100", credit=Decimal("16.00"), currency=Currency.USD),
        ],
    )
    assert len(je.lines) == 3
    _ = INVOICE_CERTIFIED
    engine.dispose()
