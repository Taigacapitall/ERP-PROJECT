"""Entites et machine a etats des devis (Quotes)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesConflictError, SalesValidationError
from vorpal.modules.ventes.domain.totals import (
    DocumentTotals,
    LineCalculationInput,
    compute_document_totals,
    ensure_strict_decimal,
)


class QuoteStatus(str, Enum):
    """Etats possibles d'un devis."""

    DRAFT = "DRAFT"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONVERTED = "CONVERTED"


@dataclass(frozen=True)
class QuoteLine:
    """Ligne d'un devis."""

    item_code: str | None
    designation: str
    quantity: Decimal
    unit_price_ht: Money
    discount_percent: Decimal = Decimal("0")
    tax_group: str = "A"
    vat_rate: Decimal = Decimal("0.16")
    is_out_of_scope: bool = False

    def __post_init__(self) -> None:
        if not self.designation or not self.designation.strip():
            raise SalesValidationError("La designation de la ligne de devis est obligatoire.")
        qty = ensure_strict_decimal(self.quantity, "quantity")
        discount = ensure_strict_decimal(self.discount_percent, "discount_percent")
        rate = ensure_strict_decimal(self.vat_rate, "vat_rate")
        if qty <= Decimal("0"):
            raise SalesValidationError("La quantite doit etre strictement positive.")
        if self.unit_price_ht.amount < Decimal("0"):
            raise SalesValidationError("Le prix unitaire HT ne peut pas etre negatif.")
        if discount < Decimal("0") or discount > Decimal("100"):
            raise SalesValidationError("La remise doit etre comprise entre 0 % et 100 %.")
        if rate < Decimal("0"):
            raise SalesValidationError("Le taux de TVA ne peut pas etre negatif.")
        if self.is_out_of_scope and (not self.item_code or not self.item_code.strip()):
            raise SalesValidationError("Une ligne hors champ exige un code article (item_code).")


@dataclass(frozen=True)
class Quote:
    """Devis client avec gestion de validite et conversion unique en commande."""

    id: str
    number: str
    customer_id: str
    quote_date: date
    valid_until: date
    currency: Currency
    lines: tuple[QuoteLine, ...]
    status: QuoteStatus = QuoteStatus.DRAFT
    converted_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise SalesValidationError("L'identifiant du devis est obligatoire.")
        if not self.number or not self.number.strip():
            raise SalesValidationError("Le numero du devis est obligatoire.")
        if not self.customer_id or not self.customer_id.strip():
            raise SalesValidationError("Le client du devis est obligatoire.")
        if self.valid_until < self.quote_date:
            raise SalesValidationError("La date de validite ne peut pas etre anterieure a la date du devis.")
        if not self.lines:
            raise SalesValidationError("Un devis doit contenir au moins une ligne.")
        for line in self.lines:
            if line.unit_price_ht.currency != self.currency:
                raise SalesValidationError("Toutes les lignes d'un devis doivent partager la meme devise.")

    def compute_totals(self, decimals: int = 2) -> DocumentTotals:
        """Calcule les totaux HT, TVA et TTC du devis."""
        inputs = [
            LineCalculationInput(
                quantity=line.quantity,
                unit_price_ht=line.unit_price_ht.amount,
                discount_percent=line.discount_percent,
                vat_rate=line.vat_rate,
            )
            for line in self.lines
        ]
        return compute_document_totals(inputs, currency=self.currency, decimals=decimals)

    def send(self, current_date: date) -> Quote:
        """Passe le devis de DRAFT a SENT."""
        if self.status != QuoteStatus.DRAFT:
            raise SalesConflictError(f"Impossible d'envoyer un devis en etat {self.status.value}.")
        if current_date > self.valid_until:
            raise SalesConflictError("Impossible d'envoyer un devis deja expire.")
        return replace(self, status=QuoteStatus.SENT)

    def mark_expired(self, current_date: date) -> Quote:
        """Expire un devis dont la date limite est depassee."""
        if self.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT):
            raise SalesConflictError(f"Un devis en etat {self.status.value} ne peut pas expirer.")
        if current_date <= self.valid_until:
            raise SalesConflictError("La date de validite du devis n'est pas encore depassee.")
        return replace(self, status=QuoteStatus.EXPIRED)

    def accept(self, current_date: date) -> Quote:
        """Accepte un devis envoye s'il est encore valide."""
        if self.status != QuoteStatus.SENT:
            raise SalesConflictError(f"Seul un devis envoye peut etre accepte (etat actuel : {self.status.value}).")
        if current_date > self.valid_until:
            raise SalesConflictError("Impossible d'accepter un devis expire.")
        return replace(self, status=QuoteStatus.ACCEPTED)

    def reject(self) -> Quote:
        """Refuse un devis envoye."""
        if self.status != QuoteStatus.SENT:
            raise SalesConflictError(f"Seul un devis envoye peut etre refuse (etat actuel : {self.status.value}).")
        return replace(self, status=QuoteStatus.REJECTED)

    def mark_converted(self, order_id: str) -> Quote:
        """Marque un devis accepte comme converti en commande (une seule fois)."""
        if self.status == QuoteStatus.CONVERTED or self.converted_order_id is not None:
            raise SalesConflictError("Ce devis a deja ete converti en commande.")
        if self.status != QuoteStatus.ACCEPTED:
            raise SalesConflictError(
                f"Seul un devis accepte peut etre converti en commande (etat actuel : {self.status.value})."
            )
        if not order_id or not order_id.strip():
            raise SalesValidationError("L'identifiant de la commande cible est obligatoire.")
        return replace(self, status=QuoteStatus.CONVERTED, converted_order_id=order_id)
