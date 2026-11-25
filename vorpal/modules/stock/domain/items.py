"""Articles geres en stock."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vorpal.core.money import Currency
from vorpal.modules.stock.domain.errors import StockConflictError, StockValidationError
from vorpal.modules.stock.domain.values import (
    MAX_QUANTITY_DECIMALS,
    ensure_quantity,
    normalize_code,
)


@dataclass
class StockItem:
    """Article de stock : le code est celui utilise par Achats et Ventes (`item_code`)."""

    code: str
    name: str
    unit: str
    currency: Currency
    quantity_decimals: int = 3
    category: str = ""
    reorder_threshold: Decimal | None = None
    is_active: bool = True

    def __post_init__(self) -> None:
        self.code = normalize_code(self.code, "Le code article")
        if not self.name.strip():
            raise StockValidationError("Le nom de l'article est obligatoire.")
        if not self.unit.strip():
            raise StockValidationError("L'unite de l'article est obligatoire.")
        if not isinstance(self.currency, Currency):
            raise StockValidationError("La devise de valorisation de l'article est invalide.")
        if (
            isinstance(self.quantity_decimals, bool)
            or not isinstance(self.quantity_decimals, int)
            or not 0 <= self.quantity_decimals <= MAX_QUANTITY_DECIMALS
        ):
            raise StockValidationError(
                f"Le nombre de decimales doit etre un entier entre 0 et {MAX_QUANTITY_DECIMALS}."
            )
        self.name = self.name.strip()
        self.unit = self.unit.strip()
        self.category = self.category.strip()
        if self.reorder_threshold is not None:
            self.reorder_threshold = self.quantity(
                self.reorder_threshold, "Le seuil de reapprovisionnement", allow_zero=True
            )

    def quantity(
        self,
        value: Decimal | int | str,
        field_name: str = "La quantite",
        allow_zero: bool = False,
    ) -> Decimal:
        """Valide une quantite selon la precision decimale propre a cet article."""
        return ensure_quantity(value, self.quantity_decimals, field_name, allow_zero)

    def ensure_active(self) -> None:
        if not self.is_active:
            raise StockConflictError(f"L'article '{self.code}' est desactive.")

    def update_details(
        self,
        name: str | None = None,
        category: str | None = None,
        reorder_threshold: Decimal | int | str | None = None,
        clear_reorder_threshold: bool = False,
    ) -> None:
        """Met a jour les champs modifiables (unite, devise et precision restent figees)."""
        if name is not None and not name.strip():
            raise StockValidationError("Le nom de l'article est obligatoire.")
        if clear_reorder_threshold and reorder_threshold is not None:
            raise StockValidationError("Effacer le seuil et en fixer un nouveau sont incompatibles.")
        new_threshold = self.reorder_threshold
        if reorder_threshold is not None:
            new_threshold = self.quantity(
                reorder_threshold, "Le seuil de reapprovisionnement", allow_zero=True
            )
        if clear_reorder_threshold:
            new_threshold = None
        if name is not None:
            self.name = name.strip()
        if category is not None:
            self.category = category.strip()
        self.reorder_threshold = new_threshold

    def deactivate(self) -> None:
        self.is_active = False

    def reactivate(self) -> None:
        self.is_active = True
