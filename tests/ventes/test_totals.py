"""Tests des fonctions pures de calcul HT, TVA et TTC."""

from decimal import Decimal

import pytest

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import SalesValidationError
from vorpal.modules.ventes.domain.totals import (
    LineCalculationInput,
    compute_document_totals,
    compute_line_amounts,
    round_decimal,
)


def test_round_decimal_half_up_boundaries() -> None:
    assert round_decimal(Decimal("10.005"), 2) == Decimal("10.01")
    assert round_decimal(Decimal("10.004"), 2) == Decimal("10.00")
    assert round_decimal(Decimal("2500.555"), 2) == Decimal("2500.56")


def test_compute_line_amounts_usd_with_discount() -> None:
    line = LineCalculationInput(
        quantity=Decimal("3"),
        unit_price_ht=Decimal("100.00"),
        discount_percent=Decimal("10"),
        vat_rate=Decimal("0.16"),
    )
    amounts = compute_line_amounts(line, Currency.USD)
    assert amounts.net_unit_price_ht == Money(Decimal("90.00"), Currency.USD)
    assert amounts.total_ht == Money(Decimal("270.00"), Currency.USD)
    assert amounts.total_vat == Money(Decimal("43.20"), Currency.USD)
    assert amounts.total_ttc == Money(Decimal("313.20"), Currency.USD)


def test_compute_document_totals_cdf_multi_lines() -> None:
    lines = [
        LineCalculationInput(quantity=Decimal("2"), unit_price_ht=Decimal("14250.50"), vat_rate=Decimal("0.16")),
        LineCalculationInput(quantity=Decimal("1"), unit_price_ht=Decimal("5000.00"), vat_rate=Decimal("0.00")),
    ]
    totals = compute_document_totals(lines, Currency.CDF)
    assert totals.total_ht == Money(Decimal("33501.00"), Currency.CDF)
    assert totals.total_vat == Money(Decimal("4560.16"), Currency.CDF)
    assert totals.total_ttc == totals.total_ht + totals.total_vat
    assert totals.total_ttc == Money(Decimal("38061.16"), Currency.CDF)


def test_totals_reject_float_and_bool() -> None:
    with pytest.raises(SalesValidationError):
        round_decimal(10.5, 2)  # type: ignore[arg-type]

    with pytest.raises(SalesValidationError):
        compute_line_amounts(
            LineCalculationInput(quantity=Decimal("1"), unit_price_ht=100.0),  # type: ignore[arg-type]
            Currency.USD,
        )

    with pytest.raises(SalesValidationError):
        compute_line_amounts(
            LineCalculationInput(quantity=True, unit_price_ht=Decimal("10")),  # type: ignore[arg-type]
            Currency.USD,
        )


def test_totals_reject_invalid_ranges() -> None:
    with pytest.raises(SalesValidationError):
        compute_document_totals([], Currency.USD)

    with pytest.raises(SalesValidationError):
        compute_line_amounts(
            LineCalculationInput(quantity=Decimal("0"), unit_price_ht=Decimal("10")),
            Currency.USD,
        )

    with pytest.raises(SalesValidationError):
        compute_line_amounts(
            LineCalculationInput(quantity=Decimal("1"), unit_price_ht=Decimal("10"), discount_percent=Decimal("105")),
            Currency.USD,
        )
