"""Entites et regles metier des encaissements et du lettrage."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesConflictError, SalesValidationError


class PaymentMethod(str, Enum):
    """Modes d'encaissement supportes en saisie manuelle."""

    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    MOBILE_MONEY = "MOBILE_MONEY"


class PaymentStatus(str, Enum):
    """Statut d'un encaissement."""

    RECORDED = "RECORDED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Allocation:
    """Lien de lettrage entre un encaissement et une facture."""

    id: str
    payment_id: str
    invoice_id: str
    amount: Money
    allocated_at: date

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise SalesValidationError("L'identifiant de lettrage est obligatoire.")
        if not self.payment_id or not self.payment_id.strip():
            raise SalesValidationError("L'identifiant du paiement est obligatoire.")
        if not self.invoice_id or not self.invoice_id.strip():
            raise SalesValidationError("L'identifiant de la facture est obligatoire.")
        if self.amount.amount <= Decimal("0"):
            raise SalesValidationError("Le montant du lettrage doit etre strictement positif.")


@dataclass(frozen=True)
class Payment:
    """Encaissement client (acompte ou reglement de facture)."""

    id: str
    customer_id: str
    amount: Money
    payment_date: date
    method: PaymentMethod
    reference: str | None = None
    order_id: str | None = None
    status: PaymentStatus = PaymentStatus.RECORDED
    allocations: tuple[Allocation, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise SalesValidationError("L'identifiant du paiement est obligatoire.")
        if not self.customer_id or not self.customer_id.strip():
            raise SalesValidationError("Le client du paiement est obligatoire.")
        if self.amount.amount <= Decimal("0"):
            raise SalesValidationError("Le montant du paiement doit etre strictement positif.")
        if self.method in (PaymentMethod.BANK_TRANSFER, PaymentMethod.MOBILE_MONEY):
            if not self.reference or not self.reference.strip():
                raise SalesValidationError(
                    f"Une reference de transaction est obligatoire pour le mode {self.method.value}."
                )
        for alloc in self.allocations:
            if alloc.payment_id != self.id:
                raise SalesValidationError("Incoherence d'identifiant de paiement sur une allocation.")
            if alloc.amount.currency != self.currency:
                raise SalesValidationError("La devise du lettrage doit correspondre a celle du paiement.")

    @property
    def currency(self) -> Currency:
        """Devise de l'encaissement."""
        return self.amount.currency

    @property
    def allocated_amount(self) -> Money:
        """Somme des montants deja lettres sur des factures."""
        zero = Money(Decimal("0.00"), self.currency)
        return sum((alloc.amount for alloc in self.allocations), zero)

    @property
    def unallocated_amount(self) -> Money:
        """Surplus non lettre disponible (credit client / acompte)."""
        return self.amount - self.allocated_amount

    def allocate(self, allocation: Allocation, invoice_balance_due: Money) -> Payment:
        """Ajoute un lettrage apres verification des soldes et devises."""
        if self.status != PaymentStatus.RECORDED:
            raise SalesConflictError("Impossible de lettrer un encaissement annule.")
        if allocation.payment_id != self.id:
            raise SalesValidationError("L'allocation n'appartient pas a ce paiement.")
        if allocation.amount.currency != self.currency:
            raise SalesValidationError("Devise du lettrage incompatible avec le paiement.")
        if invoice_balance_due.currency != self.currency:
            raise SalesValidationError(
                f"Impossible de lettrer un paiement en {self.currency.value} sur une facture en {invoice_balance_due.currency.value}."
            )
        if allocation.amount.amount > self.unallocated_amount.amount:
            raise SalesConflictError(
                f"Le montant a lettrer ({allocation.amount.amount}) depasse le disponible non lettre ({self.unallocated_amount.amount})."
            )
        if allocation.amount.amount > invoice_balance_due.amount:
            raise SalesConflictError(
                f"Le montant a lettrer ({allocation.amount.amount}) depasse le solde du de la facture ({invoice_balance_due.amount})."
            )
        return replace(self, allocations=(*self.allocations, allocation))

    def cancel(self) -> tuple[Payment, tuple[Allocation, ...]]:
        """Annule le paiement et libere toutes ses allocations."""
        if self.status == PaymentStatus.CANCELLED:
            raise SalesConflictError("Cet encaissement est deja annule.")
        released = self.allocations
        return replace(self, status=PaymentStatus.CANCELLED, allocations=()), released
