"""Bon de commande fournisseur et machine a etats."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from vorpal.core.money import Currency, Money
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseValidationError
from vorpal.modules.achats.domain.totals import (
    DocumentTotals,
    aggregate_totals,
    compute_line_totals,
    ensure_decimal,
)


class PurchaseOrderStatus(str, Enum):
    """Etats possibles d'un bon de commande."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


@dataclass
class PurchaseOrderLine:
    """Ligne d'un bon de commande fournisseur."""

    line_id: str
    item_code: str
    designation: str
    quantity_ordered: Decimal
    unit_price_ht: Money
    vat_rate: Decimal = Decimal("0.16")
    discount_rate: Decimal = Decimal("0")
    quantity_received: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.line_id.strip():
            raise PurchaseValidationError("L'identifiant de ligne est obligatoire.")
        if not self.item_code.strip():
            raise PurchaseValidationError(
                "Le code article est obligatoire sur une ligne de commande."
            )
        if not self.designation.strip():
            raise PurchaseValidationError(
                "La designation est obligatoire sur une ligne de commande."
            )

        qty = ensure_decimal(self.quantity_ordered, "quantity_ordered")
        vat = ensure_decimal(self.vat_rate, "vat_rate")
        disc = ensure_decimal(self.discount_rate, "discount_rate")
        rec = ensure_decimal(self.quantity_received, "quantity_received")

        if qty <= Decimal("0"):
            raise PurchaseValidationError("La quantite commandee doit etre strictement positive.")
        if self.unit_price_ht.amount < Decimal("0"):
            raise PurchaseValidationError("Le prix unitaire HT ne peut pas etre negatif.")
        if vat < Decimal("0"):
            raise PurchaseValidationError("Le taux de TVA ne peut pas etre negatif.")
        if disc < Decimal("0") or disc > Decimal("100"):
            raise PurchaseValidationError("La remise doit etre comprise entre 0 et 100.")
        if rec < Decimal("0"):
            raise PurchaseValidationError("La quantite recue ne peut pas etre negative.")
        if rec > qty:
            raise PurchaseValidationError(
                "La quantite recue ne peut pas depasser la quantite commandee."
            )

        self.quantity_ordered = qty
        self.vat_rate = vat
        self.discount_rate = disc
        self.quantity_received = rec

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity_ordered - self.quantity_received

    def line_totals(self) -> DocumentTotals:
        lt = compute_line_totals(
            quantity=self.quantity_ordered,
            unit_price_ht=self.unit_price_ht.amount,
            vat_rate=self.vat_rate,
            currency=self.unit_price_ht.currency,
            discount_rate=self.discount_rate,
        )
        return DocumentTotals(total_ht=lt.line_ht, total_vat=lt.line_vat, total_ttc=lt.line_ttc)

    def record_receipt(self, qty: Decimal) -> None:
        qty_dec = ensure_decimal(qty, "qty")
        if qty_dec <= Decimal("0"):
            raise PurchaseValidationError(
                "La quantite receptionnee doit etre strictement positive."
            )
        new_received = self.quantity_received + qty_dec
        if new_received > self.quantity_ordered:
            raise PurchaseConflictError(
                f"Reception excedentaire sur la ligne '{self.line_id}' : "
                f"recu {new_received} > commande {self.quantity_ordered}."
            )
        self.quantity_received = new_received


@dataclass
class PurchaseOrder:
    """Bon de commande fournisseur."""

    id: str
    supplier_id: str
    order_date: date
    currency: Currency
    lines: list[PurchaseOrderLine] = field(default_factory=list)
    status: PurchaseOrderStatus = PurchaseOrderStatus.DRAFT
    number: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise PurchaseValidationError("L'identifiant du bon de commande est obligatoire.")
        if not self.supplier_id.strip():
            raise PurchaseValidationError("L'identifiant du fournisseur est obligatoire.")
        for line in self.lines:
            if line.unit_price_ht.currency != self.currency:
                raise PurchaseValidationError(
                    f"La devise de la ligne '{line.line_id}' ({line.unit_price_ht.currency}) "
                    f"ne correspond pas a la devise du bon de commande ({self.currency})."
                )

    def add_line(self, line: PurchaseOrderLine) -> None:
        if self.status != PurchaseOrderStatus.DRAFT:
            raise PurchaseConflictError(
                f"Impossible d'ajouter une ligne a un bon de commande en etat {self.status.value}."
            )
        if line.unit_price_ht.currency != self.currency:
            raise PurchaseValidationError(
                "La ligne doit avoir la meme devise que le bon de commande."
            )
        self.lines.append(line)

    def totals(self) -> DocumentTotals:
        if not self.lines:
            zero = Money(Decimal("0.00"), self.currency)
            return DocumentTotals(total_ht=zero, total_vat=zero, total_ttc=zero)
        line_totals_list = [
            compute_line_totals(
                quantity=line.quantity_ordered,
                unit_price_ht=line.unit_price_ht.amount,
                vat_rate=line.vat_rate,
                currency=self.currency,
                discount_rate=line.discount_rate,
            )
            for line in self.lines
        ]
        return aggregate_totals(line_totals_list, self.currency)

    def confirm(self, assigned_number: str) -> None:
        if self.status != PurchaseOrderStatus.DRAFT:
            raise PurchaseConflictError(
                f"Transition interdite : impossible de confirmer un bon de commande en etat {self.status.value}."
            )
        if not self.lines:
            raise PurchaseValidationError("Impossible de confirmer un bon de commande sans ligne.")
        if not assigned_number.strip():
            raise PurchaseValidationError(
                "Le numero de bon de commande est obligatoire a la confirmation."
            )
        self.number = assigned_number
        self.status = PurchaseOrderStatus.CONFIRMED

    def cancel(self) -> None:
        if self.status not in (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.CONFIRMED):
            raise PurchaseConflictError(
                f"Impossible d'annuler un bon de commande en etat {self.status.value}."
            )
        if any(line.quantity_received > Decimal("0") for line in self.lines):
            raise PurchaseConflictError(
                "Impossible d'annuler un bon de commande ayant deja fait l'objet d'une reception."
            )
        self.status = PurchaseOrderStatus.CANCELLED

    def apply_receipt_quantities(self, received_by_line_id: dict[str, Decimal]) -> None:
        if self.status not in (
            PurchaseOrderStatus.CONFIRMED,
            PurchaseOrderStatus.PARTIALLY_RECEIVED,
        ):
            raise PurchaseConflictError(
                f"Impossible d'enregistrer une reception sur un bon de commande en etat {self.status.value}."
            )
        if not received_by_line_id:
            raise PurchaseValidationError("Aucune quantite de reception fournie.")

        lines_by_id = {line.line_id: line for line in self.lines}
        for line_id, qty in received_by_line_id.items():
            if line_id not in lines_by_id:
                raise PurchaseValidationError(f"Ligne de commande inconnue : '{line_id}'.")
            lines_by_id[line_id].record_receipt(qty)

        all_received = all(line.quantity_received == line.quantity_ordered for line in self.lines)
        self.status = (
            PurchaseOrderStatus.RECEIVED if all_received else PurchaseOrderStatus.PARTIALLY_RECEIVED
        )
