"""Ventes (+ DGI) -> Comptabilité native : EventBus réel, SQLite, MockMCFClient.

Les résultats du simulateur DGI ne constituent PAS une homologation. La correspondance fiscale A -> TG-01
et le taux CDF/USD sont déclarés ici pour les TESTS uniquement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from vorpal.core import (
    AppConfig,
    AppContext,
    AuditLog,
    AuthorizationService,
    Currency,
    Event,
    EventBus,
    InMemoryEventStore,
    InMemoryRateProvider,
    ModuleRegistry,
    ModuleState,
    Money,
    ServiceRegistry,
    User,
    create_db_engine,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
from vorpal.modules.comptabilite.domain.entries import PostedEntry
from vorpal.modules.comptabilite.module import ComptabiliteModule
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.module import DgiModule
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.tax_groups import TaxGroupTable
from vorpal.modules.ventes.domain.invoices import InvoiceLine
from vorpal.modules.ventes.domain.payments import PaymentMethod
from vorpal.modules.ventes.module import VentesModule

NOW = datetime(2026, 11, 23, 9, 0, 0, tzinfo=UTC)
D = Decimal
ALL = frozenset({"ventes.view", "ventes.create", "ventes.confirm", "ventes.admin"})


@dataclass
class Env:
    ventes: VentesModule
    compta: ComptabiliteModule
    bus: EventBus
    store: InMemoryEventStore
    actor: User

    def customer(self, nif: str | None = None) -> str:
        assert self.ventes.customer_service is not None
        return self.ventes.customer_service.create_customer(
            self.actor, name="Client Test", nif=nif, rccm="RCCM-1", address="Lubumbashi"
        ).id

    def draft(self, customer_id: str, invoice_date: date | None = None, tax_group: str = "A") -> str:
        assert self.ventes.invoice_service is not None
        line = InvoiceLine(
            item_code="SRV-01", designation="Prestation", quantity=D("2"),
            unit_price_ht=Money(D("100"), Currency.USD), discount_percent=D("10"), tax_group=tax_group,
        )
        return self.ventes.invoice_service.create_draft(
            self.actor, customer_id=customer_id, due_date=date(2026, 12, 23),
            invoice_date=invoice_date, lines=(line,),
        ).id

    def issue(self, customer_id: str, invoice_date: date | None = None) -> str:
        assert self.ventes.classic_service is not None
        return self.ventes.classic_service.issue_invoice(self.actor, self.draft(customer_id, invoice_date)).id

    def entries(self) -> list[PostedEntry]:
        assert self.compta.uow_factory is not None
        with self.compta.uow_factory() as uow:
            return uow.ledger.list_entries()

    def by_ref(self, ref: str) -> PostedEntry:
        found = [e for e in self.entries() if e.source_ref == ref]
        assert len(found) == 1, ref
        return found[0]

    def failures(self) -> list[Event]:
        return self.store.list_events(EVENT_HANDLER_FAILED)


def build(with_dgi: bool = False) -> Env:
    engine = create_db_engine("sqlite:///:memory:")
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    ctx = AppContext(
        event_bus=bus, services=ServiceRegistry(), config=AppConfig(env="test", db_url="sqlite:///:memory:"),
        db=engine, audit=AuditLog(), authz=AuthorizationService(), clock=lambda: NOW,
    )
    rates = InMemoryRateProvider({(Currency.CDF, Currency.USD, date(2026, 11, 23)): D("0.00035")})
    compta = ComptabiliteModule(rate_provider=rates)
    ventes = VentesModule()
    registry = ModuleRegistry()
    registry.register(compta)
    registry.register(ventes)
    if with_dgi:
        base = TaxGroupTable.from_json_file()
        table = TaxGroupTable(groups=base.list_active_on(date(2026, 11, 23)), mappings={"A": "TG-01"})
        client = MockMCFClient(_device_id="MCF-TEST", clock=lambda: NOW)
        registry.register(DgiModule(client, table, RetryPolicy(max_attempts=3, base_delay_seconds=10, max_delay_seconds=60)))
    registry.start_all(ctx)
    for name in ("comptabilite", "ventes"):
        assert registry.get_state(name) == ModuleState.RUNNING
    return Env(ventes, compta, bus, store, User(id="U1", username="josh", direct_permissions=ALL))


def test_classic_invoice_is_accounted_after_issue():
    env = build()
    env.issue(env.customer())
    (entry,) = env.entries()
    assert entry.source_ref == "ventes.invoice.issued:FAC-2026-000001" and entry.number == "VTE-2026-000001"
    assert [(ln.account_code, ln.debit, ln.credit) for ln in entry.lines] == [
        ("4111", D("208.80"), D("0.00")),
        ("7011", D("0.00"), D("180.00")),
        ("4431", D("0.00"), D("28.80")),
    ]
    assert entry.dgi_code is None and entry.lines[0].partner_ref is not None
    assert env.compta.reports is not None
    assert env.compta.reports.trial_balance(date(2026, 1, 1), date(2026, 12, 31)).is_balanced
    assert not env.failures()


def test_payment_credit_note_and_replay_are_idempotent_and_consistent():
    env = build()
    assert env.ventes.classic_service is not None and env.compta.reports is not None
    customer = env.customer()
    invoice_id = env.issue(customer)
    payment = env.ventes.classic_service.record_payment(
        env.actor, customer_id=customer, amount=Money(D("100"), Currency.USD), method=PaymentMethod.CASH,
        allocations=[(invoice_id, Money(D("100"), Currency.USD))],
    )
    pay_entry = env.by_ref(f"payment.received:{payment.id}")
    assert pay_entry.journal_code == "CAU"
    assert [(ln.account_code, ln.debit, ln.credit) for ln in pay_entry.lines] == [
        ("5712", D("100.00"), D("0.00")),
        ("4111", D("0.00"), D("100.00")),
    ]
    env.ventes.classic_service.cancel_payment(env.actor, payment.id)
    reversal = env.by_ref(f"reversal:{pay_entry.id}")
    assert reversal.reversal_of == pay_entry.id
    note = env.ventes.classic_service.cancel_invoice(env.actor, invoice_id, reason="Erreur de saisie")
    credit = env.by_ref(f"ventes.credit_note.issued:{note.number}")
    assert [(ln.account_code, ln.debit, ln.credit) for ln in credit.lines] == [
        ("4111", D("0.00"), D("208.80")),
        ("7011", D("180.00"), D("0.00")),
        ("4431", D("28.80"), D("0.00")),
    ]
    count = len(env.entries())
    env.bus.replay()
    env.bus.replay()
    assert len(env.entries()) == count == 4
    tb = env.compta.reports.trial_balance(date(2026, 1, 1), date(2026, 12, 31), include_zero=True)
    by = {r.code: r for r in tb.rows}
    assert tb.is_balanced
    assert by["4111"].closing_debit == by["4111"].closing_credit == D("0")
    assert by["7011"].closing_debit == by["7011"].closing_credit == D("0")
    assert not env.failures()


def test_cdf_payment_is_converted_with_the_injected_rate():
    env = build()
    assert env.ventes.classic_service is not None
    customer = env.customer()
    payment = env.ventes.classic_service.record_payment(
        env.actor, customer_id=customer, amount=Money(D("2800000"), Currency.CDF), method=PaymentMethod.CASH
    )
    entry = env.by_ref(f"payment.received:{payment.id}")
    assert entry.journal_code == "CAF" and entry.total_debit == D("980.00") and entry.currency == "CDF"


def test_handler_failure_is_retried_by_replay_once_the_fiscal_year_exists():
    env = build()
    assert env.compta.chart is not None
    invoice_id = env.issue(env.customer(), invoice_date=date(2025, 6, 1))
    assert env.ventes.uow_factory is not None
    assert env.entries() == [] and env.failures()  # exercice 2025 absent : refus explicite, facture Ventes intacte
    with env.ventes.uow_factory() as uow:
        assert uow.invoices.get(invoice_id) is not None
    env.compta.chart.ensure_fiscal_year(2025)
    env.bus.replay()
    (entry,) = env.entries()
    assert entry.entry_date == date(2025, 6, 1) and entry.fiscal_year == "2025"


def test_incomplete_dgi_stamp_is_never_accounted():
    env = build()
    payload = {
        "serial_number": "FAC-X", "is_credit_note": False, "fiscal_device_id": "MCF", "authentication_code": "AUTH",
        "qr_code": "", "certified_at": "2026-11-23T09:00:00+00:00", "invoice_date": "2026-11-23", "currency": "USD",
        "total_ht": "100", "total_vat": "16", "total_ttc": "116", "customer_name": "C", "customer_nif": "N1",
    }
    env.bus.publish(Event(type="invoice.certified", payload=payload, occurred_at=NOW, source_module="dgi"))
    assert env.entries() == []
    assert any("Cachet fiscal incomplet" in str(f.payload["error"]) for f in env.failures())


def test_dgi_certified_invoice_is_accounted_with_its_fiscal_stamp():
    env = build(with_dgi=True)
    assert env.ventes.invoice_service is not None and env.ventes.uow_factory is not None
    customer = env.customer(nif="NIF-CLIENT-1")
    invoice_id = env.draft(customer)
    env.ventes.invoice_service.validate_and_submit(env.actor, invoice_id)
    with env.ventes.uow_factory() as uow:
        invoice = uow.invoices.get(invoice_id)
    assert invoice is not None and invoice.fiscal_stamp is not None
    entry = env.by_ref("invoice.certified:FAC-2026-000001")
    assert entry.dgi_code == invoice.fiscal_stamp.auth_code and entry.dgi_qr == invoice.fiscal_stamp.qr_code
    assert [(ln.account_code, ln.debit, ln.credit) for ln in entry.lines] == [
        ("4111", D("208.80"), D("0.00")),
        ("7011", D("0.00"), D("180.00")),
        ("4431", D("0.00"), D("28.80")),
    ]
    count = len(env.entries())
    env.bus.replay()
    assert len(env.entries()) == count == 1
    assert not env.failures()


@pytest.mark.parametrize("event_type", ["ventes.invoice.issued", "ventes.credit_note.issued"])
def test_inconsistent_document_events_are_refused(event_type: str):
    env = build()
    payload = {
        "mode": "classique", "number": "F1", "invoice_type": "vente" if "credit" in event_type else "avoir",
        "invoice_date": "2026-11-23", "currency": "USD", "total_ht": "100", "total_vat": "16", "total_ttc": "116",
        "customer_id": "C1",
    }
    env.bus.publish(Event(type=event_type, payload=payload, occurred_at=NOW, source_module="ventes"))
    assert env.entries() == [] and env.failures()
