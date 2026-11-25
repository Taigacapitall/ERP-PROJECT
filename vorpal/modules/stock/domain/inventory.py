"""Inventaire physique : fiche de comptage, ecarts et validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from vorpal.modules.stock.domain.errors import StockConflictError, StockValidationError
from vorpal.modules.stock.domain.values import normalize_code


class InventoryStatus(str, Enum):
    """Etats d'une fiche d'inventaire."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    CANCELLED = "CANCELLED"


@dataclass
class InventoryLine:
    """Ligne de comptage : `theoretical_quantity` est le solde releve a l'ouverture (information)."""

    item_code: str
    theoretical_quantity: Decimal
    counted_quantity: Decimal | None = None
    unit_cost: Decimal | None = None

    def __post_init__(self) -> None:
        self.item_code = normalize_code(self.item_code, "Le code article de la ligne")


@dataclass
class InventoryCount:
    """Fiche d'inventaire d'un entrepot ; le numero est attribue a la validation."""

    id: str
    warehouse_code: str
    count_date: date
    status: InventoryStatus = InventoryStatus.DRAFT
    number: str | None = None
    notes: str = ""
    lines: list[InventoryLine] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = normalize_code(self.id, "L'identifiant de la fiche d'inventaire")
        self.warehouse_code = normalize_code(self.warehouse_code, "Le code entrepot")
        codes = [line.item_code for line in self.lines]
        if len(codes) != len(set(codes)):
            raise StockValidationError("Un article ne peut apparaitre qu'une fois par fiche.")

    def line_for(self, item_code: str) -> InventoryLine | None:
        for line in self.lines:
            if line.item_code == item_code:
                return line
        return None

    def counted_lines(self) -> list[InventoryLine]:
        return [line for line in self.lines if line.counted_quantity is not None]

    def _ensure_draft(self, action: str) -> None:
        if self.status != InventoryStatus.DRAFT:
            raise StockConflictError(
                f"Impossible de {action} une fiche d'inventaire en etat {self.status.value}."
            )

    def set_count(
        self,
        item_code: str,
        counted_quantity: Decimal,
        theoretical_now: Decimal,
        unit_cost: Decimal | None = None,
    ) -> InventoryLine:
        """Saisit (ou corrige) le comptage d'un article ; ajoute la ligne si elle n'existe pas."""
        self._ensure_draft("modifier")
        code = normalize_code(item_code, "Le code article de la ligne")
        if counted_quantity < Decimal("0"):
            raise StockValidationError("La quantite comptee ne peut pas etre negative.")
        line = self.line_for(code)
        if line is None:
            line = InventoryLine(item_code=code, theoretical_quantity=theoretical_now)
            self.lines.append(line)
        line.counted_quantity = counted_quantity
        line.unit_cost = unit_cost
        return line

    def mark_validated(self, number: str) -> None:
        self._ensure_draft("valider")
        if not number.strip():
            raise StockValidationError("Le numero de la fiche d'inventaire est obligatoire.")
        if not self.counted_lines():
            raise StockValidationError("Impossible de valider une fiche sans aucune ligne comptee.")
        self.number = number
        self.status = InventoryStatus.VALIDATED

    def cancel(self) -> None:
        self._ensure_draft("annuler")
        self.status = InventoryStatus.CANCELLED
