"""Catalogue d'articles vendables et listes de prix USD/CDF."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesNotFoundError, SalesValidationError
from vorpal.modules.ventes.domain.totals import ensure_strict_decimal


@dataclass(frozen=True)
class SalesItem:
    """Article ou prestation commercialisee."""

    code: str
    designation: str
    tax_group: str = "A"
    vat_rate: Decimal = Decimal("0.16")
    is_out_of_scope: bool = False
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise SalesValidationError("Le code article est obligatoire.")
        if not self.designation or not self.designation.strip():
            raise SalesValidationError("La designation de l'article est obligatoire.")
        rate = ensure_strict_decimal(self.vat_rate, "vat_rate")
        if rate < Decimal("0"):
            raise SalesValidationError("Le taux de TVA d'un article ne peut pas etre negatif.")
        if self.is_out_of_scope and rate != Decimal("0"):
            raise SalesValidationError("Un article hors champ doit avoir un taux de TVA nul (0).")


@dataclass(frozen=True)
class PriceList:
    """Liste de prix HT dans une devise unique."""

    id: str
    name: str
    currency: Currency
    prices: dict[str, Money]
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise SalesValidationError("L'identifiant de la liste de prix est obligatoire.")
        if not self.name or not self.name.strip():
            raise SalesValidationError("Le nom de la liste de prix est obligatoire.")
        for item_code, price in self.prices.items():
            if not item_code or not item_code.strip():
                raise SalesValidationError("Un code article dans la liste de prix ne peut pas etre vide.")
            if price.currency != self.currency:
                raise SalesValidationError(
                    f"La devise du prix ({price.currency.value}) doit correspondre a la liste ({self.currency.value})."
                )
            if price.amount < Decimal("0"):
                raise SalesValidationError("Un prix unitaire HT ne peut pas etre negatif.")

    def get_unit_price(self, item_code: str) -> Money | None:
        """Retourne le prix unitaire HT de l'article s'il est present dans cette liste."""
        return self.prices.get(item_code)

    def with_price(self, item_code: str, unit_price: Money) -> PriceList:
        """Retourne une copie de la liste avec le prix mis a jour."""
        if unit_price.currency != self.currency:
            raise SalesValidationError("La devise du prix ne correspond pas a celle de la liste.")
        if unit_price.amount < Decimal("0"):
            raise SalesValidationError("Un prix unitaire HT ne peut pas etre negatif.")
        updated = dict(self.prices)
        updated[item_code] = unit_price
        return replace(self, prices=updated)


def resolve_catalog_price(
    item_code: str,
    target_currency: Currency,
    price_lists: list[PriceList],
) -> Money:
    """Recherche un prix direct dans la devise cible parmi les listes actives."""
    for plist in price_lists:
        if plist.is_active and plist.currency == target_currency:
            price = plist.get_unit_price(item_code)
            if price is not None:
                return price
    raise SalesNotFoundError(
        f"Aucun prix direct trouve pour l'article '{item_code}' en devise {target_currency.value}."
    )
