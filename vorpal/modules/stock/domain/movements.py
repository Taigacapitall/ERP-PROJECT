"""Mouvements de stock : lignes immuables du journal des mouvements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from vorpal.core.money import Currency
from vorpal.modules.stock.domain.errors import StockValidationError
from vorpal.modules.stock.domain.values import ZERO

# Types de source reserves aux flux internes : un appelant manuel ne peut pas les utiliser,
# sinon sa reference pourrait masquer (cle d'idempotence) un vrai evenement Achats ou un inventaire.
SOURCE_GOODS_RECEIPT = "goods_receipt"
SOURCE_LANDED_COST = "landed_cost"
SOURCE_INVENTORY = "inventory"
SOURCE_MANUAL = "manual"
RESERVED_SOURCE_TYPES: frozenset[str] = frozenset(
    {SOURCE_GOODS_RECEIPT, SOURCE_LANDED_COST, SOURCE_INVENTORY}
)


class MovementType(str, Enum):
    """Nature d'un mouvement ; le signe de `quantity_delta` en donne le sens."""

    RECEIPT = "RECEIPT"
    ISSUE = "ISSUE"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    ADJUSTMENT = "ADJUSTMENT"
    REVALUATION = "REVALUATION"


@dataclass(frozen=True)
class StockMovement:
    """Mouvement valorise ; `quantity_after` et `value_after` sont le solde apres mouvement."""

    id: str
    number: str
    movement_type: MovementType
    item_code: str
    warehouse_code: str
    movement_date: date
    quantity_delta: Decimal
    value_delta: Decimal
    currency: Currency
    quantity_after: Decimal
    value_after: Decimal
    source_type: str
    source_ref: str
    created_at: datetime
    source_line_ref: str = ""
    reason: str = ""
    actor: str = "system"

    def __post_init__(self) -> None:
        for label, text in (
            ("L'identifiant", self.id),
            ("Le numero", self.number),
            ("Le code article", self.item_code),
            ("Le code entrepot", self.warehouse_code),
            ("Le type de source", self.source_type),
            ("La reference de source", self.source_ref),
        ):
            if not text.strip():
                raise StockValidationError(f"{label} du mouvement est obligatoire.")
        if self.quantity_after < ZERO or self.value_after < ZERO:
            raise StockValidationError("Le solde apres mouvement ne peut pas etre negatif.")
        self._check_signs()

    def _check_signs(self) -> None:
        qty, value = self.quantity_delta, self.value_delta
        kind = self.movement_type
        if kind in (MovementType.RECEIPT, MovementType.TRANSFER_IN):
            ok = qty > ZERO and value >= ZERO
        elif kind in (MovementType.ISSUE, MovementType.TRANSFER_OUT):
            ok = qty < ZERO and value <= ZERO
        elif kind is MovementType.ADJUSTMENT:
            ok = qty != ZERO and (value == ZERO or (value > ZERO) == (qty > ZERO))
        else:  # REVALUATION : la quantite ne bouge pas
            ok = qty == ZERO and value >= ZERO
        if not ok:
            raise StockValidationError(
                f"Signes incoherents pour un mouvement {kind.value} "
                f"(quantite {qty}, valeur {value})."
            )

    @property
    def idempotency_key(self) -> tuple[str, str, str, str]:
        """Cle d'unicite : un meme fait source ne produit jamais deux fois le meme mouvement."""
        return (self.source_type, self.source_ref, self.source_line_ref, self.movement_type.value)

    @property
    def unit_cost(self) -> Decimal | None:
        """Cout unitaire applique (valeur absolue / quantite absolue), None sans quantite."""
        if self.quantity_delta == ZERO:
            return None
        return abs(self.value_delta / self.quantity_delta)
