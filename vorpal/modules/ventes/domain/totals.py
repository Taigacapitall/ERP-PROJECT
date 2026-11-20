"""Fonctions pures de calcul des montants HT, TVA et TTC."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesValidationError


def _quantize_step(decimals: int) -> Decimal:
    if decimals < 0:
        raise SalesValidationError("Le nombre de decimales doit etre positif ou nul.")
    if decimals == 0:
        return Decimal("1")
    return Decimal("1").scaleb(-decimals)


def round_decimal(value: Decimal, decimals: int = 2) -> Decimal:
    """Arrondit un Decimal avec ROUND_HALF_UP."""
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise SalesValidationError("La valeur a arrondir doit etre un Decimal strict.")
    if not value.is_finite():
        raise SalesValidationError("La valeur a arrondir doit etre finie.")
    return value.quantize(_quantize_step(decimals), rounding=ROUND_HALF_UP)


def ensure_strict_decimal(value: Decimal, field_name: str) -> Decimal:
    """Valide qu'une valeur est bien un Decimal fini (ni float ni bool)."""
    if not isinstance(value, Decimal) or isinstance(value, bool):
        raise SalesValidationError(f"Le champ '{field_name}' doit etre un Decimal (float/bool interdits).")
    if not value.is_finite():
        raise SalesValidationError(f"Le champ '{field_name}' doit etre un nombre fini.")
    return value


@dataclass(frozen=True)
class LineCalculationInput:
    """Entree pure pour le calcul d'une ligne de document."""

    quantity: Decimal
    unit_price_ht: Decimal
    discount_percent: Decimal = Decimal("0")
    vat_rate: Decimal = Decimal("0")


@dataclass(frozen=True)
class LineAmounts:
    """Resultat arrondi du calcul d'une ligne."""

    net_unit_price_ht: Money
    total_ht: Money
    total_vat: Money
    total_ttc: Money


@dataclass(frozen=True)
class DocumentTotals:
    """Totaux agreges d'un document commercial."""

    total_ht: Money
    total_vat: Money
    total_ttc: Money


def compute_line_amounts(
    line: LineCalculationInput,
    currency: Currency,
    decimals: int = 2,
) -> LineAmounts:
    """Calcule les montants HT, TVA et TTC arrondis d'une ligne."""
    qty = ensure_strict_decimal(line.quantity, "quantity")
    unit_price = ensure_strict_decimal(line.unit_price_ht, "unit_price_ht")
    discount = ensure_strict_decimal(line.discount_percent, "discount_percent")
    vat_rate = ensure_strict_decimal(line.vat_rate, "vat_rate")

    if qty <= Decimal("0"):
        raise SalesValidationError("La quantite doit etre strictement positive.")
    if unit_price < Decimal("0"):
        raise SalesValidationError("Le prix unitaire HT ne peut pas etre negatif.")
    if discount < Decimal("0") or discount > Decimal("100"):
        raise SalesValidationError("La remise doit etre comprise entre 0 % et 100 %.")
    if vat_rate < Decimal("0"):
        raise SalesValidationError("Le taux de TVA ne peut pas etre negatif.")

    discount_factor = (Decimal("100") - discount) / Decimal("100")
    net_unit = round_decimal(unit_price * discount_factor, decimals)
    # TODO(A_VALIDER): Confirmer si l'arrondi s'applique sur le prix unitaire net puis le total ligne,
    # ou directement sur (qty * unit_price * discount_factor).
    line_ht = round_decimal(qty * net_unit, decimals)
    line_vat = round_decimal(line_ht * vat_rate, decimals)
    line_ttc = line_ht + line_vat

    return LineAmounts(
        net_unit_price_ht=Money(net_unit, currency),
        total_ht=Money(line_ht, currency),
        total_vat=Money(line_vat, currency),
        total_ttc=Money(line_ttc, currency),
    )


def compute_document_totals(
    lines: Sequence[LineCalculationInput],
    currency: Currency,
    decimals: int = 2,
) -> DocumentTotals:
    """Calcule les totaux d'un document : total_ttc == total_ht + total_vat exactement."""
    if not lines:
        raise SalesValidationError("Un document doit contenir au moins une ligne.")

    zero = Money(Decimal("0").quantize(_quantize_step(decimals)), currency)
    total_ht = zero
    total_vat = zero

    # TODO(A_VALIDER): Regle d'arrondi officielle DGI (somme des arrondis par ligne vs arrondi global).
    for line in lines:
        amounts = compute_line_amounts(line, currency=currency, decimals=decimals)
        total_ht = total_ht + amounts.total_ht
        total_vat = total_vat + amounts.total_vat

    total_ttc = total_ht + total_vat
    return DocumentTotals(total_ht=total_ht, total_vat=total_vat, total_ttc=total_ttc)
