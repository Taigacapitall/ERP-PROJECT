"""Calculs purs de montants et totaux (Decimal / Money uniquement, sans float)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from vorpal.core.money import Currency, Money
from vorpal.modules.achats.domain.errors import PurchaseValidationError

TWO_DECIMALS = Decimal("0.01")


def ensure_decimal(value: Decimal | int | str, field_name: str = "valeur") -> Decimal:
    """Convertit en Decimal en rejetant strictement float, bool, NaN et infini."""
    if isinstance(value, bool) or isinstance(value, float):
        raise PurchaseValidationError(f"{field_name} interdit les types float ou bool.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise PurchaseValidationError(f"{field_name} ne peut pas etre vide.")
        try:
            result = Decimal(cleaned)
        except InvalidOperation as exc:
            raise PurchaseValidationError(
                f"{field_name} n'est pas un nombre decimal valide."
            ) from exc
    else:
        raise PurchaseValidationError(f"{field_name} doit etre un Decimal, int ou str.")

    if result.is_nan() or result.is_infinite():
        raise PurchaseValidationError(f"{field_name} doit etre un nombre fini.")
    return result


def round_money_amount(amount: Decimal, rounding: str = ROUND_HALF_UP) -> Decimal:
    """Arrondit un montant Decimal a 2 decimales (TODO(A_VALIDER): regle officielle par devise)."""
    return amount.quantize(TWO_DECIMALS, rounding=rounding)


def round_money(money: Money, rounding: str = ROUND_HALF_UP) -> Money:
    """Retourne un Money arrondi a 2 decimales."""
    return Money(round_money_amount(money.amount, rounding=rounding), money.currency)


@dataclass(frozen=True)
class LineTotals:
    """Totaux HT, TVA et TTC d'une ligne."""

    line_ht: Money
    line_vat: Money
    line_ttc: Money


@dataclass(frozen=True)
class DocumentTotals:
    """Totaux agregees d'un document."""

    total_ht: Money
    total_vat: Money
    total_ttc: Money


def compute_line_totals(
    quantity: Decimal | int | str,
    unit_price_ht: Decimal | int | str,
    vat_rate: Decimal | int | str,
    currency: Currency,
    discount_rate: Decimal | int | str = Decimal("0"),
) -> LineTotals:
    """Calcule les montants HT, TVA et TTC d'une ligne avec remise optionnelle en pourcentage [0..100]."""
    qty = ensure_decimal(quantity, "quantite")
    price = ensure_decimal(unit_price_ht, "prix_unitaire_ht")
    rate = ensure_decimal(vat_rate, "taux_tva")
    discount = ensure_decimal(discount_rate, "remise_pct")

    if qty <= Decimal("0"):
        raise PurchaseValidationError("La quantite doit etre strictement positive.")
    if price < Decimal("0"):
        raise PurchaseValidationError("Le prix unitaire HT ne peut pas etre negatif.")
    if rate < Decimal("0"):
        raise PurchaseValidationError("Le taux de TVA ne peut pas etre negatif.")
    if discount < Decimal("0") or discount > Decimal("100"):
        raise PurchaseValidationError("La remise en pourcentage doit etre comprise entre 0 et 100.")

    net_unit_ht = round_money_amount(price * (Decimal("1") - (discount / Decimal("100"))))
    line_ht_amount = round_money_amount(qty * net_unit_ht)
    line_vat_amount = round_money_amount(line_ht_amount * rate)
    line_ttc_amount = round_money_amount(line_ht_amount + line_vat_amount)

    return LineTotals(
        line_ht=Money(line_ht_amount, currency),
        line_vat=Money(line_vat_amount, currency),
        line_ttc=Money(line_ttc_amount, currency),
    )


def aggregate_totals(lines: list[LineTotals], currency: Currency) -> DocumentTotals:
    """Agrege une liste de totaux de lignes dans la devise du document."""
    if not lines:
        raise PurchaseValidationError("Impossible de calculer les totaux sans au moins une ligne.")

    sum_ht = Decimal("0.00")
    sum_vat = Decimal("0.00")
    for line in lines:
        if (
            line.line_ht.currency != currency
            or line.line_vat.currency != currency
            or line.line_ttc.currency != currency
        ):
            raise PurchaseValidationError(
                "Toutes les lignes doivent utiliser la devise du document."
            )
        sum_ht += line.line_ht.amount
        sum_vat += line.line_vat.amount

    total_ht_val = round_money_amount(sum_ht)
    total_vat_val = round_money_amount(sum_vat)
    total_ttc_val = round_money_amount(total_ht_val + total_vat_val)

    return DocumentTotals(
        total_ht=Money(total_ht_val, currency),
        total_vat=Money(total_vat_val, currency),
        total_ttc=Money(total_ttc_val, currency),
    )
