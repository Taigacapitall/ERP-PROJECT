from __future__ import annotations

import operator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from vorpal.core.errors import NotFoundError, ValidationError
from vorpal.core.money import Currency, InMemoryRateProvider, Money, convert

D = date(2026, 11, 20)
USD = Currency.USD
CDF = Currency.CDF


def usd(value: str | int) -> Money:
    return Money(Decimal(value), USD)


def test_comparisons_same_currency() -> None:
    a, b = usd(5), usd(10)
    assert a < b
    assert a <= b
    assert b > a
    assert b >= a
    assert a <= usd(5)
    assert a >= usd(5)
    assert not (a > b)


@pytest.mark.parametrize("op", [operator.lt, operator.le, operator.gt, operator.ge])
def test_comparison_across_currencies_rejected(op: Any) -> None:
    with pytest.raises(ValidationError):
        op(usd(1), Money(Decimal(1), CDF))


def test_operators_with_non_money_are_not_supported() -> None:
    one: Any = 1
    m = usd(1)
    with pytest.raises(TypeError):
        m + one
    with pytest.raises(TypeError):
        m - one
    with pytest.raises(TypeError):
        m < one
    with pytest.raises(TypeError):
        m >= one


def test_add_sub_mixed_currency_rejected() -> None:
    other = Money(Decimal(1), CDF)
    with pytest.raises(ValidationError):
        usd(1) + other
    with pytest.raises(ValidationError):
        usd(1) - other


def test_negation_and_right_multiplication() -> None:
    assert (-usd(5)).amount == Decimal(-5)
    assert (3 * usd(5)).amount == Decimal(15)
    assert (usd(5) * Decimal("0.5")).amount == Decimal("2.5")


def test_multiplication_by_float_or_money_rejected() -> None:
    bad: Any = 1.5
    with pytest.raises(ValidationError):
        usd(5) * bad
    with pytest.raises(ValidationError):
        usd(5) * usd(2)


def test_invalid_amount_and_currency_types() -> None:
    none_value: Any = None
    str_currency: Any = "USD"
    bool_value: Any = True
    with pytest.raises(ValidationError):
        Money(none_value, USD)
    with pytest.raises(ValidationError):
        Money(bool_value, USD)
    with pytest.raises(ValidationError):
        Money(Decimal(1), str_currency)
    bad_text: Any = "abc"
    with pytest.raises(ValidationError):
        Money(bad_text, USD)
    int_value: Any = 7
    text_value: Any = " 1.50 "
    assert Money(int_value, USD).amount == Decimal(7)
    assert Money(text_value, USD).amount == Decimal("1.50")


def test_round_half_up_and_custom_decimals() -> None:
    assert Money(Decimal("1.005"), USD).round().amount == Decimal("1.01")
    assert Money(Decimal("2.5"), CDF).round(0).amount == Decimal(3)
    assert Money(Decimal("1.2345"), USD).round(3).amount == Decimal("1.235")
    with pytest.raises(ValidationError):
        usd(1).round(-1)


def test_money_is_immutable_and_hashable() -> None:
    m = usd(1)
    with pytest.raises(AttributeError):
        m.amount = Decimal(2)  # type: ignore[misc]
    assert {usd(1): "x"}[usd(1)] == "x"
    assert usd(1) == usd(1)
    assert usd(1) != Money(Decimal(1), CDF)


def test_rate_provider_same_currency_inverse_and_missing() -> None:
    p = InMemoryRateProvider()
    assert p.get_rate(USD, USD, D) == Decimal(1)
    p.set_rate(USD, CDF, D, Decimal(2800))
    assert p.get_rate(USD, CDF, D) == Decimal(2800)
    assert p.get_rate(CDF, USD, D) == Decimal(1) / Decimal(2800)
    with pytest.raises(NotFoundError):
        p.get_rate(USD, CDF, date(2026, 1, 1))


def test_rate_provider_constructor_and_invalid_rates() -> None:
    p = InMemoryRateProvider({(USD, CDF, D): Decimal(2500)})
    assert p.get_rate(USD, CDF, D) == Decimal(2500)
    with pytest.raises(ValidationError):
        p.set_rate(USD, CDF, D, Decimal(0))
    with pytest.raises(ValidationError):
        p.set_rate(USD, CDF, D, Decimal(-5))
    bad: Any = 2.5
    with pytest.raises(ValidationError):
        p.set_rate(USD, CDF, D, bad)


def test_convert_rounded_and_unrounded() -> None:
    p = InMemoryRateProvider({(USD, CDF, D): Decimal("2800.123456")})
    exact = convert(usd(1), CDF, D, p, round_result=False)
    assert exact.amount == Decimal("2800.123456")
    assert exact.currency == CDF
    rounded = convert(usd(1), CDF, D, p)
    assert rounded.amount == Decimal("2800.12")
    back = convert(Money(Decimal(2800), CDF), USD, D, InMemoryRateProvider({(USD, CDF, D): Decimal(2800)}))
    assert back.amount == Decimal("1.00")
