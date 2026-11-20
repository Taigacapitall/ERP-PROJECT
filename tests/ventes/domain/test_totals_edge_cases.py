from decimal import Decimal

import pytest

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesValidationError
from vorpal.modules.ventes.domain.totals import (
    LineCalculationInput,
    compute_document_totals,
    compute_line_amounts,
    ensure_strict_decimal,
    round_decimal,
)


def line(
    quantity: str = "1",
    price: str = "100",
    discount: str = "0",
    vat: str = "0.16",
) -> LineCalculationInput:
    return LineCalculationInput(
        quantity=Decimal(quantity),
        unit_price_ht=Decimal(price),
        discount_percent=Decimal(discount),
        vat_rate=Decimal(vat),
    )


class TestDecimalHelpers:
    def test_round_half_up(self) -> None:
        assert round_decimal(Decimal("1.235"), 2) == Decimal("1.24")

    def test_round_half_up_negative_value(self) -> None:
        assert round_decimal(Decimal("-1.235"), 2) == Decimal("-1.24")

    def test_round_zero_decimals(self) -> None:
        assert round_decimal(Decimal("12.5"), 0) == Decimal("13")

    def test_rejects_negative_decimal_places(self) -> None:
        with pytest.raises(SalesValidationError):
            round_decimal(Decimal("1"), -1)

    def test_rejects_float(self) -> None:
        with pytest.raises(SalesValidationError):
            round_decimal(1.25, 2)  # type: ignore[arg-type]

    def test_rejects_bool(self) -> None:
        with pytest.raises(SalesValidationError):
            round_decimal(True, 2)  # type: ignore[arg-type]

    def test_rejects_non_finite_value(self) -> None:
        with pytest.raises(SalesValidationError):
            round_decimal(Decimal("NaN"), 2)

    @pytest.mark.parametrize("value", [1.5, True, Decimal("Infinity")])
    def test_ensure_strict_decimal_rejects_invalid_values(self, value: object) -> None:
        with pytest.raises(SalesValidationError):
            ensure_strict_decimal(value, "amount")  # type: ignore[arg-type]

    def test_ensure_strict_decimal_accepts_decimal(self) -> None:
        assert ensure_strict_decimal(Decimal("1.5"), "amount") == Decimal("1.5")


class TestLineCalculations:
    def test_calculates_line_amounts(self) -> None:
        result = compute_line_amounts(line("2", "100"), Currency.USD)
        assert result.net_unit_price_ht == Money(Decimal("100.00"), Currency.USD)
        assert result.total_ht == Money(Decimal("200.00"), Currency.USD)
        assert result.total_vat == Money(Decimal("32.00"), Currency.USD)
        assert result.total_ttc == Money(Decimal("232.00"), Currency.USD)

    def test_applies_discount(self) -> None:
        result = compute_line_amounts(line("2", "100", "10"), Currency.USD)
        assert result.net_unit_price_ht.amount == Decimal("90.00")
        assert result.total_ht.amount == Decimal("180.00")

    def test_accepts_full_discount(self) -> None:
        result = compute_line_amounts(line("2", "100", "100"), Currency.USD)
        assert result.total_ht.amount == Decimal("0.00")
        assert result.total_ttc.amount == Decimal("0.00")

    def test_zero_vat(self) -> None:
        result = compute_line_amounts(line(vat="0"), Currency.USD)
        assert result.total_vat.amount == Decimal("0.00")

    def test_zero_decimal_rounding(self) -> None:
        result = compute_line_amounts(line("1", "10.5", vat="0"), Currency.USD, 0)
        assert result.total_ht.amount == Decimal("11")

    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_rejects_non_positive_quantity(self, quantity: str) -> None:
        with pytest.raises(SalesValidationError):
            compute_line_amounts(line(quantity=quantity), Currency.USD)

    def test_rejects_negative_unit_price(self) -> None:
        with pytest.raises(SalesValidationError):
            compute_line_amounts(line(price="-1"), Currency.USD)

    @pytest.mark.parametrize("discount", ["-1", "100.01"])
    def test_rejects_invalid_discount(self, discount: str) -> None:
        with pytest.raises(SalesValidationError):
            compute_line_amounts(line(discount=discount), Currency.USD)

    def test_rejects_negative_vat(self) -> None:
        with pytest.raises(SalesValidationError):
            compute_line_amounts(line(vat="-0.01"), Currency.USD)

    def test_rejects_float_quantity(self) -> None:
        invalid = LineCalculationInput(
            quantity=1.0,  # type: ignore[arg-type]
            unit_price_ht=Decimal("10"),
        )
        with pytest.raises(SalesValidationError):
            compute_line_amounts(invalid, Currency.USD)


class TestDocumentCalculations:
    def test_rejects_empty_document(self) -> None:
        with pytest.raises(SalesValidationError):
            compute_document_totals([], Currency.USD)

    def test_aggregates_multiple_lines(self) -> None:
        result = compute_document_totals(
            [line("2", "100"), line("1", "50", vat="0.10")],
            Currency.USD,
        )
        assert result.total_ht.amount == Decimal("250.00")
        assert result.total_vat.amount == Decimal("37.00")
        assert result.total_ttc == result.total_ht + result.total_vat

    def test_preserves_requested_decimal_precision(self) -> None:
        result = compute_document_totals([line("1", "10.555", vat="0")], Currency.USD, 3)
        assert result.total_ht.amount == Decimal("10.555")
        assert result.total_ttc.amount == Decimal("10.555")

    def test_zero_decimal_document(self) -> None:
        result = compute_document_totals([line("1", "10.5", vat="0")], Currency.USD, 0)
        assert result.total_ht.amount == Decimal("11")
