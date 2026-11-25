"""Type SQLAlchemy personnalise : Decimal stocke en texte (aucun float SQL, aucune derive)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.types import String, TypeDecorator

from vorpal.modules.stock.domain.errors import StockValidationError


class DecimalText(TypeDecorator[Decimal]):
    """Stocke un Decimal sous forme de chaine de caracteres afin d'eviter tout float SQL."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float):
            raise StockValidationError("DecimalText interdit les valeurs float ou bool.")
        if not isinstance(value, Decimal):
            try:
                value = Decimal(str(value))
            except InvalidOperation as exc:
                raise StockValidationError("Valeur decimale invalide pour DecimalText.") from exc
        if value.is_nan() or value.is_infinite():
            raise StockValidationError("DecimalText interdit NaN et infini.")
        return format(value, "f")

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise StockValidationError("Valeur stockee invalide dans DecimalText.") from exc
