"""Circuit Ventes -> DGI -> Ventes : EventBus reel, SQLite en memoire, MockMCFClient.

Les resultats du simulateur ne constituent PAS une homologation DGI.
La correspondance fiscale A -> TG-01 est declaree ici pour les TESTS uniquement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from vorpal.core import (
    AppConfig,
    AppContext,
    AuditLog,
    AuthorizationService,
    Currency,
    Event,
    EventBus,
    InMemoryEventStore,
    ModuleRegistry,
    ModuleState,
    Money,
    Partner,
    ServiceRegistry,
    User,
    create_db_engine,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.module import DgiModule
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.tax_groups import TaxGroupTable
from vorpal.modules.ventes.adapters.persistence.models import SalesInvoiceModel
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.errors import SalesComplianceError
from vorpal.modules.ventes.domain.invoices import InvoiceLine, InvoiceStatus, SalesInvoice
from vorpal.modules.ventes.module import VentesModule

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
NUMBER = "FAC-2026-000001"
READY = "invoice.ready_for_certification"


@dataclass
class Env:
    ctx: AppContext
    bus: EventBus
    store: InMemoryEventStore
    ventes: VentesModule
    dgi: DgiModule
    client: MockMCFClient
    actor: User

    def reload(self, invoice_id: str) -> SalesInvoice:
        assert self.ventes.uow_factory is not None
        with self.ventes.uow_factory() as uow:
            found = uow.invoices.get(invoice_id)
        assert found is not None
        return found

    def submit(self) -> str:
        assert self.ventes.invoice_service is not None
        svc = self.ventes.invoice_service
        draft = svc.create_draft(
            self.actor, customer_id="CUST-001", due_date=date(2026, 12, 20), invoice_date=date(2026, 11, 20),
            lines=(InvoiceLine(item_code="SRV-01", designation="Prestation", quantity=Decimal("2"),
                               unit_price_ht=Money(Decimal("100.00"), Currency.USD)),),
        )
        svc.validate_and_submit(self.actor, draft.id)
        return draft.id


def build(mapped: bool = True) -> Env:
    engine = create_db_engine("sqlite:///:memory:")
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    ctx = AppContext(
        event_bus=bus, services=ServiceRegistry(), config=AppConfig(env="test", db_url="sqlite:///:memory:"),
        db=engine, audit=AuditLog(), authz=AuthorizationService(), clock=lambda: NOW,
    )
    base = TaxGroupTable.from_json_file()
    table = TaxGroupTable(groups=base.list_active_on(date(2026, 11, 20)), mappings={"A": "TG-01"} if mapped else {})
    client = MockMCFClient(_device_id="MCF-TEST", clock=lambda: NOW)
    dgi = DgiModule(client, table, RetryPolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300))
    ventes = VentesModule()
    registry = ModuleRegistry()
    registry.register(dgi)
    registry.register(ventes)
    registry.start_all(ctx)
    assert registry.get_state("dgi") == ModuleState.RUNNING
    assert registry.get_state("ventes") == ModuleState.RUNNING
    assert ventes.uow_factory is not None
    with ventes.uow_factory() as uow:
        uow.customers.save(Customer(partner=Partner(
            id="CUST-001", name="Client Test", partner_type="client", nif="NIF-123", rccm="RCCM-123", address="Lubumbashi")))
        uow.commit()
    actor = User(id="U1", username="tester", direct_permissions=frozenset({"ventes.create", "ventes.confirm"}))
    return Env(ctx, bus, store, ventes, dgi, client, actor)


def test_ready_event_is_published_after_sql_commit():
    env = build()
    env.client.simulate_outage(True)  # la facture reste VALIDATED : on observe l'etat SQL au moment de la publication
    seen: list[InvoiceStatus] = []

    def probe(event: Event) -> None:
        assert env.ventes.uow_factory is not None
        with env.ventes.uow_factory() as uow:  # nouvelle session : ne voit que ce qui est commite
            found = uow.invoices.get_by_number(str(event.correlation_id))
        assert found is not None
        seen.append(found.status)

    env.bus.subscribe(READY, probe)
    env.submit()
    assert seen == [InvoiceStatus.VALIDATED]


def test_full_circuit_certifies_and_persists_complete_stamp():
    env = build()
    invoice = env.reload(env.submit())
    assert invoice.number == NUMBER
    assert invoice.status == InvoiceStatus.CERTIFIED
    stamp = invoice.fiscal_stamp
    assert stamp is not None
    assert stamp.device_id == "MCF-TEST" and stamp.auth_code and stamp.qr_code
    assert stamp.certified_at.replace(tzinfo=UTC) == NOW
    assert dict(stamp.counters) == {}


def test_final_rejection_is_persisted():
    env = build()
    env.client.reject_serials.add(NUMBER)
    invoice = env.reload(env.submit())
    assert invoice.status == InvoiceStatus.REJECTED
    assert invoice.fiscal_stamp is None and invoice.rejection_reason


def test_temporary_outage_keeps_validated_then_retry_certifies():
    env = build()
    env.client.simulate_outage(True)
    invoice_id = env.submit()
    assert env.reload(invoice_id).status == InvoiceStatus.VALIDATED
    env.client.simulate_outage(False)
    env.dgi.process_retries(NOW + timedelta(hours=1))
    done = env.reload(invoice_id)
    assert done.status == InvoiceStatus.CERTIFIED and done.fiscal_stamp is not None


def test_retry_then_final_rejection_notifies_sales():
    env = build()
    env.client.simulate_outage(True)
    invoice_id = env.submit()
    env.client.simulate_outage(False)
    env.client.reject_serials.add(NUMBER)
    env.dgi.process_retries(NOW + timedelta(hours=1))
    assert env.reload(invoice_id).status == InvoiceStatus.REJECTED


def test_repeated_ready_event_and_bus_replay_leave_sql_unchanged():
    env = build()
    invoice_id = env.submit()
    before = env.reload(invoice_id)
    first = env.store.list_events(READY)[0]
    env.bus.publish(Event(type=READY, payload=first.payload, occurred_at=NOW, correlation_id=first.correlation_id))
    env.bus.replay()
    after = env.reload(invoice_id)
    assert after.status == InvoiceStatus.CERTIFIED
    assert after.fiscal_stamp is not None and before.fiscal_stamp is not None
    assert after.fiscal_stamp.auth_code == before.fiscal_stamp.auth_code
    assert len(env.store.list_events("invoice.certified")) == 1


def test_conflicting_stamp_event_is_refused_and_sql_keeps_original():
    env = build()
    invoice_id = env.submit()
    good = env.store.list_events("invoice.certified")[0]
    forged = dict(good.payload, authentication_code="AUTH-FORGED")
    env.bus.publish(Event(type="invoice.certified", payload=forged, occurred_at=NOW, correlation_id=NUMBER))
    stamp = env.reload(invoice_id).fiscal_stamp
    assert stamp is not None and stamp.auth_code != "AUTH-FORGED"
    assert env.store.list_events(EVENT_HANDLER_FAILED)


def test_certified_invoice_with_incomplete_stamp_is_refused_on_reload():
    env = build()
    invoice_id = env.submit()
    with env.ctx.db.begin() as conn:
        conn.execute(update(SalesInvoiceModel).where(SalesInvoiceModel.id == invoice_id).values(stamp_qr_code=None))
    with pytest.raises(SalesComplianceError):
        env.reload(invoice_id)


def test_missing_tax_mapping_rejects_with_explicit_reason():
    env = build(mapped=False)
    invoice = env.reload(env.submit())
    assert invoice.status == InvoiceStatus.REJECTED
    assert invoice.rejection_reason is not None and "Correspondance fiscale absente" in invoice.rejection_reason
    assert invoice.fiscal_stamp is None
    assert env.dgi.service is not None
    assert env.dgi.service.certification_status(NUMBER) is None
    assert not env.store.list_events("invoice.certified")


def test_certified_invoice_can_build_customer_document_with_stored_stamp():
    env = build()
    invoice = env.reload(env.submit())
    assert invoice.fiscal_stamp is not None
    assert env.ventes.uow_factory is not None
    with env.ventes.uow_factory() as uow:
        customer = uow.customers.get("CUST-001")
    assert customer is not None
    settings = env.ventes.invoice_service._deps.settings  # type: ignore[union-attr]
    dto = invoice.to_invoice_dto(customer, settings.seller)
    assert dto.number == NUMBER and dto.total_ttc == Decimal("232.00")
