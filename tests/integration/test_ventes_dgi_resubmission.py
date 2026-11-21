"""Rejet -> reouverture -> correction -> resoumission, avec outbox DGI persistante."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from vorpal.core import (
    AppConfig,
    AppContext,
    AuditLog,
    AuthorizationService,
    Currency,
    EventBus,
    InMemoryEventStore,
    ModuleRegistry,
    Money,
    Partner,
    ServiceRegistry,
    User,
    create_db_engine,
)
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.module import DgiModule
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.tax_groups import TaxGroupTable
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import InvoiceLine, InvoiceStatus
from vorpal.modules.ventes.module import VentesModule

from .test_ventes_dgi_circuit import Env

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
NUMBER = "FAC-2026-000001"


def build(clock: dict[str, datetime]) -> Env:
    engine = create_db_engine("sqlite:///:memory:")
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    ctx = AppContext(
        event_bus=bus, services=ServiceRegistry(), config=AppConfig(env="test", db_url="sqlite:///:memory:"),
        db=engine, audit=AuditLog(), authz=AuthorizationService(), clock=lambda: clock["now"],
    )
    base = TaxGroupTable.from_json_file()
    table = TaxGroupTable(groups=base.list_active_on(date(2026, 11, 20)), mappings={"A": "TG-01"})
    client = MockMCFClient(_device_id="MCF-TEST", clock=lambda: clock["now"])
    dgi = DgiModule(client, table, RetryPolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300))
    ventes = VentesModule()
    registry = ModuleRegistry()
    registry.register(dgi)
    registry.register(ventes)
    registry.start_all(ctx)
    assert ventes.uow_factory is not None
    with ventes.uow_factory() as uow:
        uow.customers.save(Customer(partner=Partner(
            id="CUST-001", name="Client Test", partner_type="client", nif="NIF-123", rccm="RCCM-123", address="Lubumbashi")))
        uow.commit()
    actor = User(id="U1", username="tester", direct_permissions=frozenset({"ventes.create", "ventes.confirm"}))
    return Env(ctx, bus, store, ventes, dgi, client, actor)


def corrected_line() -> InvoiceLine:
    return InvoiceLine(item_code="SRV-01", designation="Prestation corrigee", quantity=Decimal("1"),
                       unit_price_ht=Money(Decimal("150.00"), Currency.USD))


def test_rejected_invoice_is_reopened_corrected_and_certified_with_same_number():
    clock = {"now": NOW}
    env = build(clock)
    env.client.reject_serials.add(NUMBER)
    invoice_id = env.submit()
    assert env.reload(invoice_id).status == InvoiceStatus.REJECTED

    svc = env.ventes.invoice_service
    assert svc is not None
    clock["now"] = NOW + timedelta(hours=1)
    reopened = svc.reopen_rejected(env.actor, invoice_id, new_lines=(corrected_line(),))
    assert reopened.status == InvoiceStatus.DRAFT and reopened.number == NUMBER

    env.client.reject_serials.discard(NUMBER)
    svc.validate_and_submit(env.actor, invoice_id)
    done = env.reload(invoice_id)
    assert done.status == InvoiceStatus.CERTIFIED and done.number == NUMBER
    assert done.fiscal_stamp is not None
    assert done.total_ttc is not None and done.total_ttc.amount == Decimal("174.00")

    env.bus.replay()  # les anciens evenements rejoues ne doivent pas corrompre l'etat
    again = env.reload(invoice_id)
    assert again.status == InvoiceStatus.CERTIFIED and again.fiscal_stamp is not None
    assert again.fiscal_stamp.auth_code == done.fiscal_stamp.auth_code


def test_stale_rejection_replay_leaves_a_reopened_draft_untouched():
    clock = {"now": NOW}
    env = build(clock)
    env.client.reject_serials.add(NUMBER)
    invoice_id = env.submit()
    svc = env.ventes.invoice_service
    assert svc is not None
    clock["now"] = NOW + timedelta(hours=1)
    svc.reopen_rejected(env.actor, invoice_id)
    env.bus.replay()
    assert env.reload(invoice_id).status == InvoiceStatus.DRAFT
