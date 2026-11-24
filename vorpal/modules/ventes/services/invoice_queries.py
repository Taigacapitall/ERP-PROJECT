"""Consultation des factures : liste filtree, impayes echus, releve client (lecture seule)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vorpal.core import Currency, Money, User
from vorpal.modules.ventes.domain.errors import SalesValidationError
from vorpal.modules.ventes.domain.invoices import InvoiceStatus, SalesInvoice
from vorpal.modules.ventes.domain.payments import Payment, PaymentStatus
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.services.classic_invoicing import credited_total
from vorpal.modules.ventes.services.common import ApplicationService

PERM_VIEW = "ventes.view"

_LABELS_CLASSIC = {
    InvoiceStatus.DRAFT: "Brouillon",
    InvoiceStatus.VALIDATED: "Émise",
    InvoiceStatus.CERTIFIED: "Certifiée",
    InvoiceStatus.REJECTED: "Rejetée",
}


@dataclass(frozen=True)
class InvoiceView:
    """Facture enrichie des montants calcules (avoirs, encaissements, solde net, retard)."""

    invoice: SalesInvoice
    customer_name: str
    credited: Money
    paid: Money
    net_due: Money
    days_overdue: int
    label: str
    cancelled: bool

    @property
    def is_unpaid(self) -> bool:
        return self.invoice.invoice_type == "vente" and not self.cancelled and self.net_due.amount > 0


@dataclass(frozen=True)
class CurrencyTotals:
    invoiced: Money
    credited: Money
    paid: Money
    balance: Money


@dataclass(frozen=True)
class CustomerStatement:
    customer_id: str
    customer_name: str
    invoices: tuple[InvoiceView, ...]
    payments: tuple[Payment, ...]
    totals: dict[Currency, CurrencyTotals]


class InvoiceQueryService(ApplicationService):
    """Requetes de lecture sur les factures et les impayes."""

    def get_invoice(self, actor: User, invoice_id: str, *, as_of: date | None = None) -> InvoiceView:
        self._require(actor, PERM_VIEW)
        with self._deps.uow_factory() as uow:
            invoice = uow.invoices.get(invoice_id)
            if invoice is None:
                raise SalesValidationError(f"Facture '{invoice_id}' introuvable.")
            return self._view(uow, invoice, as_of or self._today())

    def list_invoices(
        self,
        actor: User,
        *,
        customer_id: str | None = None,
        status: InvoiceStatus | None = None,
        invoice_type: str | None = None,
        unpaid_only: bool = False,
        date_from: date | None = None,
        date_to: date | None = None,
        as_of: date | None = None,
    ) -> list[InvoiceView]:
        self._require(actor, PERM_VIEW)
        day = as_of or self._today()
        with self._deps.uow_factory() as uow:
            views = [self._view(uow, inv, day) for inv in uow.invoices.list_all()]
        result = [
            v for v in views
            if (customer_id is None or v.invoice.customer_id == customer_id)
            and (status is None or v.invoice.status == status)
            and (invoice_type is None or v.invoice.invoice_type == invoice_type)
            and (not unpaid_only or v.is_unpaid)
            and (date_from is None or v.invoice.invoice_date >= date_from)
            and (date_to is None or v.invoice.invoice_date <= date_to)
        ]
        return sorted(result, key=lambda v: (v.invoice.invoice_date, v.invoice.number or ""), reverse=True)

    def list_overdue(self, actor: User, *, as_of: date | None = None) -> list[InvoiceView]:
        """Factures emises, non soldees (solde net > 0) et dont l'echeance est depassee."""
        day = as_of or self._today()
        overdue = [
            v for v in self.list_invoices(actor, as_of=day, invoice_type="vente", unpaid_only=True)
            if v.invoice.status in (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED) and v.days_overdue > 0
        ]
        return sorted(overdue, key=lambda v: v.days_overdue, reverse=True)

    def customer_statement(self, actor: User, customer_id: str, *, as_of: date | None = None) -> CustomerStatement:
        self._require(actor, PERM_VIEW)
        day = as_of or self._today()
        with self._deps.uow_factory() as uow:
            customer = uow.customers.get(customer_id)
            if customer is None:
                raise SalesValidationError(f"Client '{customer_id}' introuvable.")
            views = tuple(
                self._view(uow, inv, day) for inv in uow.invoices.list_all()
                if inv.customer_id == customer_id and inv.invoice_type == "vente"
                and inv.status in (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED)
            )
            payments = tuple(
                p for p in uow.payments.list_by_customer(customer_id) if p.status == PaymentStatus.RECORDED
            )
        totals: dict[Currency, CurrencyTotals] = {}
        for cur in sorted({v.invoice.currency for v in views}, key=lambda c: c.value):
            zero = Money(Decimal("0.00"), cur)
            mine = [v for v in views if v.invoice.currency == cur]
            invoiced = sum((v.invoice.effective_total_ttc for v in mine), zero)
            credited = sum((v.credited for v in mine), zero)
            paid = sum((v.paid for v in mine), zero)
            totals[cur] = CurrencyTotals(invoiced, credited, paid, invoiced - credited - paid)
        return CustomerStatement(customer_id, customer.name, views, payments, totals)

    # ------------------------------------------------------------------- interne
    def _view(self, uow: SalesUnitOfWork, invoice: SalesInvoice, as_of: date) -> InvoiceView:
        customer = uow.customers.get(invoice.customer_id)
        notes = uow.invoices.list_credit_notes_for_invoice(invoice.id) if invoice.invoice_type == "vente" else []
        credited = credited_total(notes, invoice) if invoice.invoice_type == "vente" else Money(Decimal("0.00"), invoice.currency)
        paid = invoice.effective_paid_amount
        net = invoice.effective_total_ttc - credited - paid
        issued = invoice.status in (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED)
        cancelled = (
            invoice.invoice_type == "vente" and issued and credited.amount >= invoice.effective_total_ttc.amount
        )
        overdue = (as_of - invoice.due_date).days if as_of > invoice.due_date else 0
        label = "Avoir" if invoice.invoice_type == "avoir" else _LABELS_CLASSIC[invoice.status]
        if cancelled:
            label = "Annulée"
        return InvoiceView(
            invoice=invoice,
            customer_name=customer.name if customer else invoice.customer_id,
            credited=credited,
            paid=paid,
            net_due=net,
            days_overdue=overdue if (issued and invoice.invoice_type == "vente" and net.amount > 0) else 0,
            label=label,
            cancelled=cancelled,
        )
