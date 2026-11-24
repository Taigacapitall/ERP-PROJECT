"""Flux commercial classique SANS DGI : VentesModule seul, SQLite (memoire et fichier), EventBus reel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

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
    ModuleRegistry,
    Money,
    ServiceRegistry,
    User,
    create_db_engine,
)
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.invoices import (
    InvoiceLine,
    InvoicePaymentStatus,
    InvoiceStatus,
    SalesInvoice,
)
from vorpal.modules.ventes.domain.payments import PaymentMethod
from vorpal.modules.ventes.module import VentesModule
from vorpal.modules.ventes.services.common import LineInput
from vorpal.modules.ventes.services.documents import export_invoices_csv, invoice_model, render_pdf

NOW = datetime(2026, 11, 23, 9, 0, 0, tzinfo=UTC)
TODAY = NOW.date()
ALL = frozenset({"ventes.view", "ventes.create", "ventes.confirm", "ventes.admin"})


def usd(value: str) -> Money:
    return Money(Decimal(value), Currency.USD)


def cdf(value: str) -> Money:
    return Money(Decimal(value), Currency.CDF)


@dataclass
class Env:
    ventes: VentesModule
    bus: EventBus
    events: list[Event]
    actor: User

    def line_input(self, price: str = "100", qty: str = "2", disc: str = "10", cur: Currency = Currency.USD) -> LineInput:
        return LineInput(designation="Prestation", quantity=Decimal(qty), unit_price_ht=Money(Decimal(price), cur),
                         discount_percent=Decimal(disc), tax_group="A")

    def customer(self, name: str = "Client Test", nif: str | None = None) -> str:
        assert self.ventes.customer_service is not None
        return self.ventes.customer_service.create_customer(self.actor, name=name, nif=nif).id

    def issued_invoice(self, customer_id: str, price: str = "100", qty: str = "2", disc: str = "10",
                       cur: Currency = Currency.USD, due: date | None = None,
                       invoice_date: date | None = None) -> str:
        """Brouillon manuel puis emission classique (sans commande)."""
        assert self.ventes.invoice_service is not None and self.ventes.classic_service is not None
        draft = self.ventes.invoice_service.create_draft(
            self.actor, customer_id=customer_id, due_date=due or date(2026, 12, 23), currency=cur, invoice_date=invoice_date,
            lines=(InvoiceLine(item_code=None, designation="Prestation", quantity=Decimal(qty),
                               unit_price_ht=Money(Decimal(price), cur), discount_percent=Decimal(disc)),),
        )
        return self.ventes.classic_service.issue_invoice(self.actor, draft.id).id

    def reload(self, invoice_id: str) -> SalesInvoice:
        assert self.ventes.uow_factory is not None
        with self.ventes.uow_factory() as uow:
            found = uow.invoices.get(invoice_id)
        assert found is not None
        return found


def build(db_url: str = "sqlite:///:memory:") -> Env:
    engine = create_db_engine(db_url)
    bus = EventBus(store=InMemoryEventStore())
    ctx = AppContext(
        event_bus=bus, services=ServiceRegistry(), config=AppConfig(env="test", db_url=db_url), db=engine,
        audit=AuditLog(), authz=AuthorizationService(), clock=lambda: NOW,
    )
    ventes = VentesModule()
    registry = ModuleRegistry()
    registry.register(ventes)
    registry.start_all(ctx)
    events: list[Event] = []
    for event_type in ("invoice.ready_for_certification", "payment.received"):
        bus.subscribe(event_type, events.append)
    return Env(ventes, bus, events, User(id="U1", username="josh", direct_permissions=ALL))


@pytest.fixture
def env() -> Env:
    return build()


def test_full_flow_quote_to_paid_without_dgi(env: Env) -> None:
    v = env.ventes
    assert v.quote_service and v.order_service and v.classic_service and v.query_service
    cid = env.customer(nif=None)  # particulier sans NIF
    quote = v.quote_service.create_quote(env.actor, customer_id=cid, valid_until=date(2026, 12, 31), lines=[env.line_input()])
    v.quote_service.send_quote(env.actor, quote.id)
    v.quote_service.accept_quote(env.actor, quote.id)
    order = v.order_service.create_from_quote(env.actor, quote.id)
    v.order_service.confirm_order(env.actor, order.id)

    draft = v.classic_service.invoice_from_order(env.actor, order.id)
    assert draft.status == InvoiceStatus.DRAFT and draft.order_id == order.id
    invoice = v.classic_service.issue_invoice(env.actor, draft.id)
    assert invoice.number == "FAC-2026-000001" and invoice.status == InvoiceStatus.VALIDATED
    assert (invoice.total_ht, invoice.total_vat, invoice.total_ttc) == (usd("180.00"), usd("28.80"), usd("208.80"))
    assert not [e for e in env.events if e.type == "invoice.ready_for_certification"], "aucun evenement DGI attendu"
    assert env.reload(invoice.id).due_date == date(2026, 12, 23)  # echeance = date + delai client (30 j)

    first = v.classic_service.record_payment(
        env.actor, customer_id=cid, amount=usd("100.00"), method=PaymentMethod.CASH, allocations=[(invoice.id, usd("100.00"))]
    )
    partial = env.reload(invoice.id)
    assert partial.payment_status == InvoicePaymentStatus.PARTIALLY_PAID and partial.balance_due == usd("108.80")
    assert [e.type for e in env.events] == ["payment.received"] and first.unallocated_amount == usd("0.00")

    v.classic_service.record_payment(
        env.actor, customer_id=cid, amount=usd("108.80"), method=PaymentMethod.BANK_TRANSFER, reference="VIR-1",
        allocations=[(invoice.id, usd("108.80"))],
    )
    assert env.reload(invoice.id).payment_status == InvoicePaymentStatus.PAID
    assert v.query_service.list_invoices(env.actor, unpaid_only=True) == []


def test_partial_invoicing_of_an_order(env: Env) -> None:
    v = env.ventes
    assert v.order_service and v.classic_service
    cid = env.customer()
    order = v.order_service.create_order(env.actor, customer_id=cid, lines=[env.line_input(qty="10", disc="0")])
    v.order_service.confirm_order(env.actor, order.id)
    line_id = order.lines[0].line_id
    first = v.classic_service.invoice_from_order(env.actor, order.id, quantities={line_id: Decimal("4")})
    assert first.lines[0].quantity == Decimal("4") and first.lines[0].order_line_id == line_id
    with pytest.raises(SalesConflictError):
        v.classic_service.invoice_from_order(env.actor, order.id, quantities={line_id: Decimal("7")})
    second = v.classic_service.invoice_from_order(env.actor, order.id)  # solde = 6
    assert second.lines[0].quantity == Decimal("6")
    with pytest.raises(SalesConflictError):
        v.classic_service.invoice_from_order(env.actor, order.id)  # plus rien a facturer


def test_payment_guards(env: Env) -> None:
    v = env.ventes
    assert v.classic_service
    cid, other = env.customer("A"), env.customer("B")
    inv = env.issued_invoice(cid)
    svc = v.classic_service
    with pytest.raises(SalesConflictError):  # depasse le solde
        svc.record_payment(env.actor, customer_id=cid, amount=usd("500.00"), method=PaymentMethod.CASH,
                           allocations=[(inv, usd("300.00"))])
    with pytest.raises(SalesConflictError):  # facture d'un autre client
        svc.record_payment(env.actor, customer_id=other, amount=usd("10.00"), method=PaymentMethod.CASH,
                           allocations=[(inv, usd("10.00"))])
    with pytest.raises(SalesValidationError):  # devise differente
        svc.record_payment(env.actor, customer_id=cid, amount=cdf("1000"), method=PaymentMethod.CASH,
                           allocations=[(inv, cdf("1000"))])
    with pytest.raises(SalesValidationError):  # meme facture deux fois
        svc.record_payment(env.actor, customer_id=cid, amount=usd("20.00"), method=PaymentMethod.CASH,
                           allocations=[(inv, usd("10.00")), (inv, usd("10.00"))])
    svc.record_payment(env.actor, customer_id=cid, amount=usd("50.00"), method=PaymentMethod.MOBILE_MONEY, reference="MM-9")
    with pytest.raises(SalesConflictError):  # doublon de reference
        svc.record_payment(env.actor, customer_id=cid, amount=usd("50.00"), method=PaymentMethod.MOBILE_MONEY, reference="MM-9")
    assert env.reload(inv).paid_amount == usd("0.00")  # rien n'a ete enregistre par les refus


def test_deposit_then_allocate_then_cancel_payment(env: Env) -> None:
    v = env.ventes
    assert v.classic_service
    cid = env.customer()
    inv = env.issued_invoice(cid)
    deposit = v.classic_service.record_payment(env.actor, customer_id=cid, amount=usd("80.00"), method=PaymentMethod.CASH)
    assert deposit.unallocated_amount == usd("80.00") and env.reload(inv).balance_due == usd("208.80")
    allocated = v.classic_service.allocate_payment(env.actor, deposit.id, inv, usd("80.00"))
    assert allocated.unallocated_amount == usd("0.00") and env.reload(inv).balance_due == usd("128.80")
    v.classic_service.cancel_payment(env.actor, deposit.id)
    back = env.reload(inv)
    assert back.balance_due == usd("208.80") and back.payment_status == InvoicePaymentStatus.UNPAID
    with pytest.raises(SalesConflictError):
        v.classic_service.cancel_payment(env.actor, deposit.id)  # deja annule


def test_credit_notes_and_cancellation(env: Env) -> None:
    v = env.ventes
    assert v.classic_service and v.query_service
    cid = env.customer()
    inv = env.issued_invoice(cid)  # TTC 208.80
    half = (InvoiceLine(item_code=None, designation="Prestation", quantity=Decimal("1"), unit_price_ht=usd("100.00")),)
    note = v.classic_service.credit_invoice(env.actor, inv, reason="Remise commerciale", lines=half)  # TTC 116.00
    assert note.number == "AVO-2026-000001" and note.original_invoice_number == "FAC-2026-000001"
    view = v.query_service.get_invoice(env.actor, inv)
    assert view.credited == usd("116.00") and view.net_due == usd("92.80") and not view.cancelled
    with pytest.raises(SalesComplianceError):  # cumul > TTC d'origine
        v.classic_service.credit_invoice(env.actor, inv, reason="Trop", lines=half)
    with pytest.raises(SalesValidationError):
        v.classic_service.credit_invoice(env.actor, inv, reason=" ")
    with pytest.raises(SalesConflictError):  # annulation refusee : deja creditee
        v.classic_service.cancel_invoice(env.actor, inv, reason="Erreur")

    inv2 = env.issued_invoice(cid)
    cancelled = v.classic_service.cancel_invoice(env.actor, inv2, reason="Erreur de saisie")
    view2 = v.query_service.get_invoice(env.actor, inv2)
    assert cancelled.total_ttc == usd("208.80") and view2.cancelled and view2.label == "Annulée" and view2.net_due == usd("0.00")
    assert inv2 not in [x.invoice.id for x in v.query_service.list_invoices(env.actor, unpaid_only=True)]

    inv3 = env.issued_invoice(cid)
    v.classic_service.record_payment(env.actor, customer_id=cid, amount=usd("10.00"), method=PaymentMethod.CASH,
                                     allocations=[(inv3, usd("10.00"))])
    with pytest.raises(SalesConflictError):  # encaissee : annuler d'abord le paiement
        v.classic_service.cancel_invoice(env.actor, inv3, reason="Erreur")


def test_permissions_are_enforced(env: Env) -> None:
    v = env.ventes
    assert v.classic_service and v.query_service
    cid = env.customer()
    inv = env.issued_invoice(cid)
    clerk = User(id="U2", username="clerk", direct_permissions=frozenset({"ventes.create"}))
    with pytest.raises(SalesComplianceError):
        v.classic_service.credit_invoice(clerk, inv, reason="x")
    with pytest.raises(SalesComplianceError):
        v.classic_service.issue_invoice(clerk, inv)
    with pytest.raises(SalesComplianceError):
        v.query_service.list_invoices(clerk)


def test_overdue_and_statement_multi_currency(env: Env) -> None:
    v = env.ventes
    assert v.query_service and v.classic_service
    cid = env.customer()
    late = env.issued_invoice(cid, due=date(2026, 11, 3), invoice_date=date(2026, 10, 20))  # 20 jours de retard au 23/11
    env.issued_invoice(cid, due=date(2026, 12, 30))  # pas encore echue
    cdf_inv = env.issued_invoice(cid, price="285000.50", qty="3", disc="0", cur=Currency.CDF, due=date(2026, 11, 13),
                              invoice_date=date(2026, 10, 20))
    v.classic_service.record_payment(env.actor, customer_id=cid, amount=usd("100.00"), method=PaymentMethod.CASH,
                                     allocations=[(late, usd("100.00"))])
    overdue = v.query_service.list_overdue(env.actor)
    assert [(x.invoice.id, x.days_overdue) for x in overdue] == [(late, 20), (cdf_inv, 10)]
    assert overdue[0].net_due == usd("108.80")
    stmt = v.query_service.customer_statement(env.actor, cid)
    assert stmt.totals[Currency.USD].invoiced == usd("417.60") and stmt.totals[Currency.USD].balance == usd("317.60")
    assert stmt.totals[Currency.CDF].invoiced == cdf("991801.74") and len(stmt.payments) == 1


def test_data_survives_restart_on_file_database(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'vorpal.db'}"
    first = build(url)
    assert first.ventes.classic_service
    cid = first.customer()
    inv = first.issued_invoice(cid)
    first.ventes.classic_service.record_payment(
        first.actor, customer_id=cid, amount=usd("100.00"), method=PaymentMethod.CASH, allocations=[(inv, usd("100.00"))]
    )
    second = build(url)  # nouveau processus simule : nouveau moteur, memes donnees
    assert second.ventes.query_service and second.ventes.classic_service
    view = second.ventes.query_service.get_invoice(second.actor, inv)
    assert view.invoice.number == "FAC-2026-000001" and view.net_due == usd("108.80")
    nxt = second.issued_invoice(cid)
    assert second.reload(nxt).number == "FAC-2026-000002"  # la sequence continue sans trou ni doublon


def test_pdf_and_csv_exports(env: Env) -> None:
    pytest.importorskip("reportlab")
    v = env.ventes
    assert v.query_service and v.classic_service and v.customer_service and v.settings and v.uow_factory
    cid = env.customer("Société Éclair & Fils")
    inv = env.issued_invoice(cid)
    v.classic_service.record_payment(env.actor, customer_id=cid, amount=usd("50.00"), method=PaymentMethod.CASH,
                                     allocations=[(inv, usd("50.00"))])
    note = v.classic_service.credit_invoice(env.actor, inv, reason="Geste commercial",
                                            lines=(InvoiceLine(item_code=None, designation="Remise", quantity=Decimal("1"),
                                                               unit_price_ht=usd("10.00")),))
    with v.uow_factory() as uow:
        customer = uow.customers.get(cid)
        payments = uow.payments.list_by_customer(cid)
    assert customer is not None
    for number_id in (inv, note.id):
        view = v.query_service.get_invoice(env.actor, number_id)
        pdf = render_pdf(invoice_model(view, customer, v.settings.seller, payments))
        assert pdf.startswith(b"%PDF") and len(pdf) > 1500
    rows = export_invoices_csv(v.query_service.list_invoices(env.actor)).splitlines()
    assert rows[0].startswith("numero;type;statut") and len(rows) == 3 and "FAC-2026-000001" in "".join(rows)


def test_concurrent_issuance_never_duplicates_numbers(tmp_path: Path) -> None:
    import threading

    env = build(f"sqlite:///{tmp_path / 'concurrent.db'}")
    cid = env.customer()
    numbers: list[str] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            numbers.append(str(env.reload(env.issued_invoice(cid)).number))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(numbers) == [f"FAC-2026-{n:06d}" for n in range(1, 9)]
