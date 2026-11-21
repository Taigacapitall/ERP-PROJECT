"""Frais d'approche (transport, douane, manutention) et repartition sur receptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from vorpal.core.money import Currency, Money
from vorpal.modules.achats.domain.errors import PurchaseValidationError
from vorpal.modules.achats.domain.receipts import GoodsReceipt
from vorpal.modules.achats.domain.totals import ensure_decimal, round_money_amount
from vorpal.modules.achats.settings import LandedCostAllocationMethod


@dataclass(frozen=True)
class LandedCostComponent:
    """Composante de frais d'approche (ex. fret, douane, manutention)."""

    category: str
    label: str
    amount: Money

    def __post_init__(self) -> None:
        if not self.category.strip():
            raise PurchaseValidationError("La categorie de frais d'approche est obligatoire.")
        if not self.label.strip():
            raise PurchaseValidationError("Le libelle de frais d'approche est obligatoire.")
        if self.amount.amount <= Decimal("0"):
            raise PurchaseValidationError(
                "Le montant d'une composante de frais d'approche doit etre positif."
            )


@dataclass(frozen=True)
class LandedCostAllocationLine:
    """Part de frais d'approche allouee a une ligne de reception."""

    receipt_line_id: str
    item_code: str
    allocated_amount: Money


@dataclass
class LandedCostDocument:
    """Document de frais d'approche rattache a un bon de reception."""

    id: str
    goods_receipt_id: str
    allocation_date: date
    currency: Currency
    method: LandedCostAllocationMethod = LandedCostAllocationMethod.BY_AMOUNT
    components: list[LandedCostComponent] = field(default_factory=list)
    allocations: list[LandedCostAllocationLine] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise PurchaseValidationError(
                "L'identifiant du document de frais d'approche est obligatoire."
            )
        if not self.goods_receipt_id.strip():
            raise PurchaseValidationError("L'identifiant du bon de reception est obligatoire.")
        for comp in self.components:
            if comp.amount.currency != self.currency:
                raise PurchaseValidationError(
                    "La devise d'une composante de frais d'approche doit correspondre au document."
                )

    def total_cost(self) -> Money:
        total = Decimal("0.00")
        for comp in self.components:
            total += comp.amount.amount
        return Money(round_money_amount(total), self.currency)


def allocate_landed_costs(
    document: LandedCostDocument,
    receipt: GoodsReceipt,
) -> list[LandedCostAllocationLine]:
    """Repartit les frais d'approche d'un document sur les lignes d'une reception."""
    if document.goods_receipt_id != receipt.id:
        raise PurchaseValidationError(
            "Le document de frais d'approche ne correspond pas au bon de reception cible."
        )
    if document.currency != receipt.currency:
        raise PurchaseValidationError(
            "La devise des frais d'approche doit correspondre a la devise de la reception."
        )
    if not receipt.lines:
        raise PurchaseValidationError(
            "Impossible de repartir des frais d'approche sur une reception vide."
        )
    if not document.components:
        raise PurchaseValidationError("Aucune composante de frais d'approche a repartir.")

    total_to_allocate = document.total_cost().amount

    if document.method == LandedCostAllocationMethod.BY_AMOUNT:
        weights = [
            ensure_decimal(line.base_line_ht.amount, "base_line_ht") for line in receipt.lines
        ]
    elif document.method == LandedCostAllocationMethod.BY_QUANTITY:
        weights = [
            ensure_decimal(line.quantity_received, "quantity_received") for line in receipt.lines
        ]
    else:
        raise PurchaseValidationError(f"Methode de repartition non supportee : {document.method}")

    total_weight = sum(weights, Decimal("0"))
    if total_weight <= Decimal("0"):
        raise PurchaseValidationError(
            "Le poids total de repartition des frais d'approche doit etre positif."
        )

    allocations: list[LandedCostAllocationLine] = []
    allocated_so_far = Decimal("0.00")

    for index, (line, weight) in enumerate(zip(receipt.lines, weights, strict=True)):
        if index == len(receipt.lines) - 1:
            line_share = round_money_amount(total_to_allocate - allocated_so_far)
        else:
            raw_share = (total_to_allocate * weight) / total_weight
            line_share = round_money_amount(raw_share)
            allocated_so_far += line_share

        share_money = Money(line_share, document.currency)
        line.landed_cost_allocated = share_money
        allocations.append(
            LandedCostAllocationLine(
                receipt_line_id=line.line_id,
                item_code=line.item_code,
                allocated_amount=share_money,
            )
        )

    document.allocations = allocations
    return allocations
