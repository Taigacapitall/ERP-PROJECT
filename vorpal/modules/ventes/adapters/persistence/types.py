"""TypeDecorator SQLAlchemy pour la persistence exacte des montants Decimal sans float."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import String, TypeDecorator
from sqlalchemy.dialects import postgresql

from vorpal.modules.ventes.domain.errors import SalesValidationError


class DecimalText(TypeDecorator[Decimal]):
    """Stocke un Decimal exact sous forme de chaine de caracteres en base."""

    impl = String(64)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.NUMERIC(precision=38, scale=12))
        return dialect.type_descriptor(String(64))

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float):
            raise SalesValidationError("DecimalText interdit les valeurs float ou bool.")
        if not isinstance(value, Decimal):
            raise SalesValidationError("DecimalText exige une instance de Decimal.")
        if not value.is_finite():
            raise SalesValidationError("DecimalText exige un Decimal fini.")
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value: str | Decimal | None, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float):
            raise SalesValidationError("Valeur corrompue en base (float ou bool interdit).")
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise SalesValidationError("Valeur Decimal non finie lue en base.")
            return value
        if not isinstance(value, str) or not value.strip():
            raise SalesValidationError("Valeur DecimalText vide ou invalide lue en base.")
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            raise SalesValidationError(f"Impossible de convertir '{value}' en Decimal.") from exc
        if not parsed.is_finite():
            raise SalesValidationError("Valeur Decimal non finie lue en base.")
        return parsed
