"""Tests du type SQL DecimalText."""

from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from vorpal.modules.ventes.adapters.persistence.types import DecimalText
from vorpal.modules.ventes.domain.errors import SalesValidationError


@pytest.fixture
def decimal_type() -> DecimalText:
    return DecimalText()


def test_load_dialect_impl_postgresql(decimal_type: DecimalText) -> None:
    result = decimal_type.load_dialect_impl(postgresql.dialect())

    assert result.precision == 38
    assert result.scale == 12


def test_load_dialect_impl_sqlite(decimal_type: DecimalText) -> None:
    result = decimal_type.load_dialect_impl(sqlite.dialect())

    assert result.length == 64


def test_bind_none(decimal_type: DecimalText) -> None:
    assert decimal_type.process_bind_param(None, sqlite.dialect()) is None


def test_bind_decimal_as_string_sqlite(decimal_type: DecimalText) -> None:
    value = Decimal("123456789.123456789")

    assert decimal_type.process_bind_param(value, sqlite.dialect()) == str(value)


def test_bind_decimal_as_decimal_postgresql(decimal_type: DecimalText) -> None:
    value = Decimal("123456789.123456789")

    assert decimal_type.process_bind_param(value, postgresql.dialect()) == value


@pytest.mark.parametrize("value", [True, False, 1.5])
def test_bind_rejects_bool_and_float(
    decimal_type: DecimalText,
    value: object,
) -> None:
    with pytest.raises(SalesValidationError, match="float ou bool"):
        decimal_type.process_bind_param(value, sqlite.dialect())  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [123, "12.50", object()])
def test_bind_rejects_non_decimal(
    decimal_type: DecimalText,
    value: object,
) -> None:
    with pytest.raises(SalesValidationError, match="instance de Decimal"):
        decimal_type.process_bind_param(value, sqlite.dialect())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_bind_rejects_non_finite_decimal(
    decimal_type: DecimalText,
    value: Decimal,
) -> None:
    with pytest.raises(SalesValidationError, match="Decimal fini"):
        decimal_type.process_bind_param(value, sqlite.dialect())


def test_result_none(decimal_type: DecimalText) -> None:
    assert decimal_type.process_result_value(None, sqlite.dialect()) is None


def test_result_finite_decimal(decimal_type: DecimalText) -> None:
    value = Decimal("123.456")

    assert decimal_type.process_result_value(value, sqlite.dialect()) == value


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_result_rejects_non_finite_decimal(
    decimal_type: DecimalText,
    value: Decimal,
) -> None:
    with pytest.raises(SalesValidationError, match="Decimal non finie"):
        decimal_type.process_result_value(value, sqlite.dialect())


@pytest.mark.parametrize("value", [True, False, 1.5])
def test_result_rejects_bool_and_float(
    decimal_type: DecimalText,
    value: object,
) -> None:
    with pytest.raises(SalesValidationError, match="float ou bool"):
        decimal_type.process_result_value(value, sqlite.dialect())  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "   ", 123, b"12.50"])
def test_result_rejects_empty_or_invalid_values(
    decimal_type: DecimalText,
    value: object,
) -> None:
    with pytest.raises(SalesValidationError, match="vide ou invalide"):
        decimal_type.process_result_value(value, sqlite.dialect())  # type: ignore[arg-type]


def test_result_rejects_invalid_decimal_string(decimal_type: DecimalText) -> None:
    with pytest.raises(SalesValidationError, match="Impossible de convertir"):
        decimal_type.process_result_value("abc", sqlite.dialect())


def test_result_rejects_non_finite_decimal_string(decimal_type: DecimalText) -> None:
    with pytest.raises(SalesValidationError, match="Decimal non finie"):
        decimal_type.process_result_value("NaN", sqlite.dialect())


def test_result_converts_string_to_decimal(decimal_type: DecimalText) -> None:
    result = decimal_type.process_result_value("  123.4500  ", sqlite.dialect())

    assert result == Decimal("123.4500")
    assert isinstance(result, Decimal)
    assert result.as_tuple().exponent == -4
