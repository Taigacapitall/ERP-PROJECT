"""Calculs purs sur quantites et montants (Decimal uniquement, jamais de float)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from vorpal.modules.stock.domain.errors import StockValidationError

ZERO = Decimal("0")
TWO_DECIMALS = Decimal("0.01")
MAX_MAGNITUDE = Decimal("1e12")
MAX_QUANTITY_DECIMALS = 6


def ensure_decimal(value: Decimal | int | str, field_name: str = "valeur") -> Decimal:
    """Convertit en Decimal en rejetant strictement float, bool, NaN et infini."""
    if isinstance(value, bool) or isinstance(value, float):
        raise StockValidationError(f"{field_name} interdit les types float ou bool.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise StockValidationError(f"{field_name} ne peut pas etre vide.")
        try:
            result = Decimal(cleaned)
        except InvalidOperation as exc:
            raise StockValidationError(f"{field_name} n'est pas un nombre decimal valide.") from exc
    else:
        raise StockValidationError(f"{field_name} doit etre un Decimal, int ou str.")

    if result.is_nan() or result.is_infinite():
        raise StockValidationError(f"{field_name} doit etre un nombre fini.")
    if abs(result) >= MAX_MAGNITUDE:
        raise StockValidationError(f"{field_name} depasse la valeur maximale admise.")
    return result


def round_amount(amount: Decimal) -> Decimal:
    """Arrondit un montant a 2 decimales (TODO(A_VALIDER): regle officielle par devise)."""
    return amount.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)


def ensure_quantity(
    value: Decimal | int | str,
    decimals: int,
    field_name: str = "quantite",
    allow_zero: bool = False,
) -> Decimal:
    """Valide une quantite positive (ou nulle si permis) avec au plus `decimals` decimales."""
    qty = ensure_decimal(value, field_name)
    if qty < ZERO:
        raise StockValidationError(f"{field_name} ne peut pas etre negative.")
    if qty == ZERO and not allow_zero:
        raise StockValidationError(f"{field_name} doit etre strictement positive.")
    rounded = qty.quantize(Decimal(1).scaleb(-decimals))
    if rounded != qty:
        raise StockValidationError(f"{field_name} : au plus {decimals} decimale(s) autorisee(s).")
    return rounded


def ensure_unit_cost(value: Decimal | int | str, field_name: str = "cout unitaire") -> Decimal:
    """Valide un cout unitaire positif ou nul (l'arrondi se fait sur la valeur de la ligne)."""
    cost = ensure_decimal(value, field_name)
    if cost < ZERO:
        raise StockValidationError(f"{field_name} ne peut pas etre negatif.")
    return cost


def normalize_code(value: str, field_name: str = "code") -> str:
    """Retire les espaces autour d'un code et refuse un code vide."""
    if not isinstance(value, str) or not value.strip():
        raise StockValidationError(f"{field_name} est obligatoire.")
    return value.strip()
