"""Receptions de marchandises (bons de reception)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from vorpal.core.money import Currency, Money
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseValidationError
from vorpal.modules.achats.domain.totals import ensure_decimal, round_money_amount


class GoodsReceiptStatus(str, Enum):
    """Etats d'un bon de reception."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    CANCELLED = "CANCELLED"


@dataclass
class GoodsReceiptLine:
    """Ligne d'un bon de reception."""

    line_id: str
    order_line_id: str
    item_code: str
    designation: str
    quantity_received: Decimal
    unit_cost_ht: Money
    landed_cost_allocated: Money | None = None

    def __post_init__(self) -> None:
        if not self.line_id.strip():
            raise PurchaseValidationError("L'identifiant de ligne de reception est obligatoire.")
        if not self.order_line_id.strip():
            raise PurchaseValidationError("La reference de ligne de commande est obligatoire.")
        if not self.item_code.strip():
            raise PurchaseValidationError(
                "Le code article est obligatoire sur la ligne de reception."
            )
        if not self.designation.strip():
            raise PurchaseValidationError(
                "La designation est obligatoire sur la ligne de reception."
            )
        qty = ensure_decimal(self.quantity_received, "quantity_received")
        if qty <= Decimal("0"):
            raise PurchaseValidationError(
                "La quantite receptionnee doit etre strictement positive."
            )
        if self.unit_cost_ht.amount < Decimal("0"):
            raise PurchaseValidationError("Le cout unitaire HT ne peut pas etre negatif.")
        self.quantity_received = qty
        if self.landed_cost_allocated is None:
            self.landed_cost_allocated = Money(Decimal("0.00"), self.unit_cost_ht.currency)

    @property
    def base_line_ht(self) -> Money:
        amount = round_money_amount(self.quantity_received * self.unit_cost_ht.amount)
        return Money(amount, self.unit_cost_ht.currency)

    @property
    def total_landed_cost_ht(self) -> Money:
        allocated = self.landed_cost_allocated or Money(Decimal("0.00"), self.unit_cost_ht.currency)
        if allocated.currency != self.unit_cost_ht.currency:
            raise PurchaseValidationError(
                "La devise des frais d'approche doit correspondre a la ligne."
            )
        return Money(
            round_money_amount(self.base_line_ht.amount + allocated.amount),
            self.unit_cost_ht.currency,
        )

    @property
    def effective_unit_cost_ht(self) -> Money:
        """Cout unitaire reel HT apres ajout des frais d'approche alloues."""
        unit_val = round_money_amount(self.total_landed_cost_ht.amount / self.quantity_received)
        return Money(unit_val, self.unit_cost_ht.currency)


@dataclass
class GoodsReceipt:
    """Bon de reception lie a un bon de commande."""

    id: str
    purchase_order_id: str
    supplier_id: str
    receipt_date: date
    currency: Currency
    warehouse_code: str = "MAIN"
    lines: list[GoodsReceiptLine] = field(default_factory=list)
    status: GoodsReceiptStatus = GoodsReceiptStatus.DRAFT
    number: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise PurchaseValidationError("L'identifiant du bon de reception est obligatoire.")
        if not self.purchase_order_id.strip():
            raise PurchaseValidationError("L'identifiant du bon de commande est obligatoire.")
        if not self.supplier_id.strip():
            raise PurchaseValidationError("L'identifiant du fournisseur est obligatoire.")
        if not self.warehouse_code.strip():
            raise PurchaseValidationError("Le code d'entrepot/emplacement est obligatoire.")
        for line in self.lines:
            if line.unit_cost_ht.currency != self.currency:
                raise PurchaseValidationError(
                    "La devise d'une ligne de reception doit correspondre a celle du bon de reception."
                )

    def add_line(self, line: GoodsReceiptLine) -> None:
        if self.status != GoodsReceiptStatus.DRAFT:
            raise PurchaseConflictError(
                f"Impossible d'ajouter une ligne a une reception en etat {self.status.value}."
            )
        if line.unit_cost_ht.currency != self.currency:
            raise PurchaseValidationError(
                "La devise de la ligne doit correspondre a celle de la reception."
            )
        self.lines.append(line)

    def total_base_ht(self) -> Money:
        total = Decimal("0.00")
        for line in self.lines:
            total += line.base_line_ht.amount
        return Money(round_money_amount(total), self.currency)

    def validate(self, assigned_number: str) -> None:
        if self.status != GoodsReceiptStatus.DRAFT:
            raise PurchaseConflictError(
                f"Impossible de valider une reception en etat {self.status.value}."
            )
        if not self.lines:
            raise PurchaseValidationError("Impossible de valider une reception sans ligne.")
        if not assigned_number.strip():
            raise PurchaseValidationError(
                "Le numero de bon de reception est obligatoire a la validation."
            )
        self.number = assigned_number
        self.status = GoodsReceiptStatus.VALIDATED

    def cancel(self) -> None:
        if self.status != GoodsReceiptStatus.DRAFT:
            raise PurchaseConflictError(
                f"Impossible d'annuler une reception en etat {self.status.value}."
            )
        self.status = GoodsReceiptStatus.CANCELLED
