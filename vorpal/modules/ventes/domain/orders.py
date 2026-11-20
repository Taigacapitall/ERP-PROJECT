"""Entites et machine a etats des commandes clients (SalesOrder)."""

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


class OrderStatus(str, Enum):
    """Etats possibles d'une commande client."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_INVOICED = "PARTIALLY_INVOICED"
    INVOICED = "INVOICED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class OrderLine:
    """Ligne d'une commande client avec suivi des quantites facturees."""

    line_id: str
    item_code: str | None
    designation: str
    quantity: Decimal
    unit_price_ht: Money
    discount_percent: Decimal = Decimal("0")
    tax_group: str = "A"
    vat_rate: Decimal = Decimal("0.16")
    is_out_of_scope: bool = False
    invoiced_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.line_id or not self.line_id.strip():
            raise SalesValidationError("L'identifiant de ligne de commande est obligatoire.")
        if not self.designation or not self.designation.strip():
            raise SalesValidationError("La designation de la ligne de commande est obligatoire.")
        qty = ensure_strict_decimal(self.quantity, "quantity")
        inv_qty = ensure_strict_decimal(self.invoiced_quantity, "invoiced_quantity")
        discount = ensure_strict_decimal(self.discount_percent, "discount_percent")
        rate = ensure_strict_decimal(self.vat_rate, "vat_rate")
        if qty <= Decimal("0"):
            raise SalesValidationError("La quantite doit etre strictement positive.")
        if inv_qty < Decimal("0") or inv_qty > qty:
            raise SalesValidationError("La quantite facturee doit etre comprise entre 0 et la quantite commandee.")
        if self.unit_price_ht.amount < Decimal("0"):
            raise SalesValidationError("Le prix unitaire HT ne peut pas etre negatif.")
        if discount < Decimal("0") or discount > Decimal("100"):
            raise SalesValidationError("La remise doit etre comprise entre 0 % et 100 %.")
        if rate < Decimal("0"):
            raise SalesValidationError("Le taux de TVA ne peut pas etre negatif.")
        if self.is_out_of_scope and (not self.item_code or not self.item_code.strip()):
            raise SalesValidationError("Une ligne hors champ exige un code article (item_code).")

    @property
    def remaining_quantity(self) -> Decimal:
        """Quantite restant a facturer sur cette ligne."""
        return self.quantity - self.invoiced_quantity


@dataclass(frozen=True)
class SalesOrder:
    """Commande client."""

    id: str
    number: str
    customer_id: str
    order_date: date
    currency: Currency
    lines: tuple[OrderLine, ...]
    status: OrderStatus = OrderStatus.DRAFT
    quote_id: str | None = None
    deposit_required: Money | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise SalesValidationError("L'identifiant de la commande est obligatoire.")
        if not self.number or not self.number.strip():
            raise SalesValidationError("Le numero de la commande est obligatoire.")
        if not self.customer_id or not self.customer_id.strip():
            raise SalesValidationError("Le client de la commande est obligatoire.")
        if not self.lines:
            raise SalesValidationError("Une commande doit contenir au moins une ligne.")
        seen_ids: set[str] = set()
        for line in self.lines:
            if line.line_id in seen_ids:
                raise SalesValidationError(f"Identifiant de ligne en double : '{line.line_id}'.")
            seen_ids.add(line.line_id)
            if line.unit_price_ht.currency != self.currency:
                raise SalesValidationError("Toutes les lignes d'une commande doivent partager la meme devise.")
        if self.deposit_required is not None:
            if self.deposit_required.currency != self.currency:
                raise SalesValidationError("La devise de l'acompte doit correspondre a la devise de la commande.")
            if self.deposit_required.amount < Decimal("0"):
                raise SalesValidationError("Le montant d'acompte exige ne peut pas etre negatif.")

    def compute_totals(self, decimals: int = 2) -> DocumentTotals:
        """Calcule les totaux HT, TVA et TTC de la commande."""
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

    def update_lines(self, new_lines: tuple[OrderLine, ...]) -> SalesOrder:
        """Modifie les lignes uniquement tant que la commande est en brouillon."""
        if self.status != OrderStatus.DRAFT:
            raise SalesConflictError(
                f"Aucune modification de lignes n'est autorisee en etat {self.status.value}."
            )
        return replace(self, lines=new_lines)

    def confirm(self) -> SalesOrder:
        """Confirme une commande en brouillon."""
        if self.status != OrderStatus.DRAFT:
            raise SalesConflictError(f"Seule une commande en brouillon peut etre confirmee ({self.status.value}).")
        return replace(self, status=OrderStatus.CONFIRMED)

    def cancel(self) -> SalesOrder:
        """Annule une commande avant toute facturation."""
        if self.status not in (OrderStatus.DRAFT, OrderStatus.CONFIRMED):
            raise SalesConflictError(
                f"Impossible d'annuler une commande en etat {self.status.value}."
            )
        if any(line.invoiced_quantity > Decimal("0") for line in self.lines):
            raise SalesConflictError("Impossible d'annuler une commande ayant deja ete partiellement facturee.")
        return replace(self, status=OrderStatus.CANCELLED)

    def record_invoiced_quantities(self, billed_by_line: dict[str, Decimal]) -> SalesOrder:
        """Enregistre les quantites facturees et met a jour le statut de facturation."""
        if self.status not in (OrderStatus.CONFIRMED, OrderStatus.PARTIALLY_INVOICED):
            raise SalesConflictError(
                f"La commande doit etre confirmee pour etre facturee (etat actuel : {self.status.value})."
            )
        if not billed_by_line:
            raise SalesValidationError("Aucune quantite facturee fournie.")

        line_map = {line.line_id: line for line in self.lines}
        for line_id in billed_by_line:
            if line_id not in line_map:
                raise SalesValidationError(f"Ligne de commande introuvable : '{line_id}'.")

        updated_lines: list[OrderLine] = []
        for line in self.lines:
            added = billed_by_line.get(line.line_id, Decimal("0"))
            added_dec = ensure_strict_decimal(added, "billed_quantity")
            if added_dec < Decimal("0"):
                raise SalesValidationError("La quantite facturee ne peut pas etre negative.")
            if added_dec > line.remaining_quantity:
                raise SalesConflictError(
                    f"Quantite facturee ({added_dec}) superieure au reliquat ({line.remaining_quantity}) sur '{line.line_id}'."
                )
            updated_lines.append(replace(line, invoiced_quantity=line.invoiced_quantity + added_dec))

        all_invoiced = all(line.remaining_quantity == Decimal("0") for line in updated_lines)
        next_status = OrderStatus.INVOICED if all_invoiced else OrderStatus.PARTIALLY_INVOICED
        return replace(self, lines=tuple(updated_lines), status=next_status)
