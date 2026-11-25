"""Entrepots (un emplacement est simplement un autre code d'entrepot)."""

from __future__ import annotations

from dataclasses import dataclass

from vorpal.modules.stock.domain.errors import StockConflictError, StockValidationError
from vorpal.modules.stock.domain.values import normalize_code


@dataclass
class Warehouse:
    """Entrepot ou depot ; `code` correspond au `warehouse_code` des receptions Achats."""

    code: str
    name: str
    is_active: bool = True

    def __post_init__(self) -> None:
        self.code = normalize_code(self.code, "Le code entrepot")
        if not self.name.strip():
            raise StockValidationError("Le nom de l'entrepot est obligatoire.")
        self.name = self.name.strip()

    def ensure_active(self) -> None:
        if not self.is_active:
            raise StockConflictError(f"L'entrepot '{self.code}' est desactive.")

    def deactivate(self) -> None:
        self.is_active = False

    def reactivate(self) -> None:
        self.is_active = True
