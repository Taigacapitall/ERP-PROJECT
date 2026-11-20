"""Entites et politique declarative des relances d'impayes (V2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesValidationError
from vorpal.modules.ventes.domain.invoices import InvoicePaymentStatus, InvoiceStatus, SalesInvoice


@dataclass(frozen=True)
class ReminderLevel:
    """Palier declaratif de relance selon le retard en jours."""

    code: str
    days_after_due: int
    channel: str
    template: str

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise SalesValidationError("Le code du palier de relance est obligatoire.")
        if self.days_after_due < 1:
            raise SalesValidationError("Le delai de relance doit etre d'au moins 1 jour.")
        if not self.channel or not self.channel.strip():
            raise SalesValidationError("Le canal de relance est obligatoire.")
        if not self.template or not self.template.strip():
            raise SalesValidationError("Le modele de message de relance est obligatoire.")


@dataclass(frozen=True)
class Reminder:
    """Relance generee pour une facture impayee."""

    id: str
    invoice_id: str
    invoice_number: str
    customer_id: str
    level_code: str
    channel: str
    days_overdue: int
    balance_due: Money
    message: str
    generated_at: date
    sent: bool = False


@dataclass(frozen=True)
class OverdueCustomerSummary:
    """Synthese de l'encours en retard d'un client."""

    customer_id: str
    customer_name: str
    currency: Currency
    overdue_invoice_count: int
    total_overdue_amount: Money
    max_days_overdue: int


class ReminderPolicy:
    """Evalue les factures eligibles et determine le palier de relance applicable."""

    def __init__(self, levels: list[ReminderLevel] | tuple[ReminderLevel, ...]) -> None:
        codes = [lvl.code for lvl in levels]
        if len(codes) != len(set(codes)):
            raise SalesValidationError("Les codes de niveaux de relance doivent etre uniques.")
        self._levels = tuple(sorted(levels, key=lambda lvl: lvl.days_after_due))

    @property
    def levels(self) -> tuple[ReminderLevel, ...]:
        """Niveaux tries par nombre de jours croissant."""
        return self._levels

    def days_overdue(self, invoice: SalesInvoice, as_of: date) -> int:
        """Retourne le nombre de jours de retard (0 si non echue)."""
        if as_of <= invoice.due_date:
            return 0
        return (as_of - invoice.due_date).days

    def is_eligible(self, invoice: SalesInvoice, as_of: date) -> bool:
        """Verifie si la facture est emise, impayee et en retard."""
        if invoice.invoice_type != "vente":
            return False
        if invoice.status not in (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED):
            return False
        if invoice.payment_status in (InvoicePaymentStatus.PAID, InvoicePaymentStatus.OVERPAID):
            return False
        return self.days_overdue(invoice, as_of) > 0

    def applicable_level(self, invoice: SalesInvoice, as_of: date) -> ReminderLevel | None:
        """Retourne le niveau de relance le plus eleve atteint a la date donnee."""
        if not self.is_eligible(invoice, as_of):
            return None
        overdue = self.days_overdue(invoice, as_of)
        chosen: ReminderLevel | None = None
        for level in self._levels:
            if overdue >= level.days_after_due:
                chosen = level
        return chosen

    def render_message(self, level: ReminderLevel, invoice: SalesInvoice, as_of: date) -> str:
        """Formate le message declaratif avec les donnees de la facture."""
        overdue = self.days_overdue(invoice, as_of)
        balance = invoice.balance_due
        return level.template.format(
            invoice_number=invoice.number or invoice.id,
            days_overdue=overdue,
            balance_due=str(balance.amount),
            currency=balance.currency.value,
            due_date=invoice.due_date.isoformat(),
        )

    def summarize_overdue_customers(
        self,
        invoices: list[SalesInvoice],
        customer_names: dict[str, str],
        as_of: date,
    ) -> list[OverdueCustomerSummary]:
        """Agrege les encours en retard par couple (client, devise)."""
        grouped: dict[tuple[str, Currency], list[tuple[SalesInvoice, int]]] = {}
        for inv in invoices:
            if self.is_eligible(inv, as_of):
                overdue = self.days_overdue(inv, as_of)
                grouped.setdefault((inv.customer_id, inv.currency), []).append((inv, overdue))

        summaries: list[OverdueCustomerSummary] = []
        for (cust_id, currency), items in sorted(grouped.items(), key=lambda entry: (entry[0][0], entry[0][1].value)):
            zero = Money(Decimal("0.00"), currency)
            total = sum((inv.balance_due for inv, _ in items), zero)
            max_days = max(days for _, days in items)
            summaries.append(
                OverdueCustomerSummary(
                    customer_id=cust_id,
                    customer_name=customer_names.get(cust_id, cust_id),
                    currency=currency,
                    overdue_invoice_count=len(items),
                    total_overdue_amount=total,
                    max_days_overdue=max_days,
                )
            )
        return summaries

