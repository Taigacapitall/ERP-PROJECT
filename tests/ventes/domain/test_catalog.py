from decimal import Decimal

import pytest

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.catalog import (
    PriceList,
    SalesItem,
    resolve_catalog_price,
)
from vorpal.modules.ventes.domain.errors import (
    SalesNotFoundError,
    SalesValidationError,
)


def money(amount: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(amount), currency)


class TestSalesItem:
    def test_creates_item_with_defaults(self) -> None:
        item = SalesItem(code="ART-001", designation="Ordinateur")

        assert item.code == "ART-001"
        assert item.designation == "Ordinateur"
        assert item.tax_group == "A"
        assert item.vat_rate == Decimal("0.16")
        assert item.is_active is True
        assert item.is_out_of_scope is False

    @pytest.mark.parametrize("code", ["", " ", "   "])
    def test_rejects_empty_code(self, code: str) -> None:
        with pytest.raises(SalesValidationError):
            SalesItem(code=code, designation="Ordinateur")

    @pytest.mark.parametrize("designation", ["", " ", "   "])
    def test_rejects_empty_designation(self, designation: str) -> None:
        with pytest.raises(SalesValidationError):
            SalesItem(code="ART-001", designation=designation)

    def test_rejects_negative_vat(self) -> None:
        with pytest.raises(SalesValidationError, match="negatif"):
            SalesItem(
                code="ART-001",
                designation="Ordinateur",
                vat_rate=Decimal("-0.01"),
            )

    def test_rejects_non_decimal_vat(self) -> None:
        with pytest.raises((SalesValidationError, TypeError)):
            SalesItem(
                code="ART-001",
                designation="Ordinateur",
                vat_rate=0.16,  # type: ignore[arg-type]
            )

    def test_out_of_scope_item_requires_zero_vat(self) -> None:
        with pytest.raises(SalesValidationError, match="hors champ"):
            SalesItem(
                code="ART-001",
                designation="Ordinateur",
                is_out_of_scope=True,
                vat_rate=Decimal("0.16"),
            )

    def test_accepts_out_of_scope_item_with_zero_vat(self) -> None:
        item = SalesItem(
            code="ART-002",
            designation="Produit hors champ",
            is_out_of_scope=True,
            vat_rate=Decimal("0"),
        )

        assert item.is_out_of_scope is True
        assert item.vat_rate == Decimal("0")


class TestPriceList:
    def test_creates_price_list(self) -> None:
        prices = {"ART-001": money("100.00")}
        price_list = PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices=prices,
        )

        assert price_list.id == "PL-001"
        assert price_list.name == "Tarif standard"
        assert price_list.currency == Currency.USD
        assert price_list.is_active is True
        assert price_list.get_unit_price("ART-001") == prices["ART-001"]

    @pytest.mark.parametrize("list_id", ["", " ", "   "])
    def test_rejects_empty_id(self, list_id: str) -> None:
        with pytest.raises(SalesValidationError):
            PriceList(
                id=list_id,
                name="Tarif standard",
                currency=Currency.USD,
                prices={},
            )

    @pytest.mark.parametrize("name", ["", " ", "   "])
    def test_rejects_empty_name(self, name: str) -> None:
        with pytest.raises(SalesValidationError):
            PriceList(
                id="PL-001",
                name=name,
                currency=Currency.USD,
                prices={},
            )

    def test_rejects_empty_item_code(self) -> None:
        with pytest.raises(SalesValidationError, match="code article"):
            PriceList(
                id="PL-001",
                name="Tarif standard",
                currency=Currency.USD,
                prices={" ": money("100")},
            )

    def test_rejects_price_in_wrong_currency(self) -> None:
        with pytest.raises(SalesValidationError, match="devise"):
            PriceList(
                id="PL-001",
                name="Tarif standard",
                currency=Currency.USD,
                prices={"ART-001": money("100", Currency.CDF)},
            )

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(SalesValidationError, match="negatif"):
            PriceList(
                id="PL-001",
                name="Tarif standard",
                currency=Currency.USD,
                prices={"ART-001": money("-1")},
            )

    def test_get_unit_price_returns_none_when_missing(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices={},
        )

        assert price_list.get_unit_price("UNKNOWN") is None

    def test_with_price_returns_new_list_without_mutating_original(self) -> None:
        original = PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices={"ART-001": money("100")},
        )

        updated = original.with_price("ART-001", money("125"))

        assert updated.get_unit_price("ART-001") == money("125")
        assert original.get_unit_price("ART-001") == money("100")
        assert updated is not original

    def test_with_price_adds_new_item(self) -> None:
        original = PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices={},
        )

        updated = original.with_price("ART-002", money("50"))

        assert updated.get_unit_price("ART-002") == money("50")
        assert original.get_unit_price("ART-002") is None

    def test_with_price_rejects_wrong_currency(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices={},
        )

        with pytest.raises(SalesValidationError, match="devise"):
            price_list.with_price("ART-001", money("100", Currency.CDF))

    def test_with_price_rejects_negative_price(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices={},
        )

        with pytest.raises(SalesValidationError, match="negatif"):
            price_list.with_price("ART-001", money("-1"))


class TestResolveCatalogPrice:
    def test_returns_price_from_matching_active_list(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif USD",
            currency=Currency.USD,
            prices={"ART-001": money("150")},
        )

        result = resolve_catalog_price(
            "ART-001",
            Currency.USD,
            [price_list],
        )

        assert result == money("150")

    def test_ignores_inactive_price_list(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif USD",
            currency=Currency.USD,
            prices={"ART-001": money("150")},
            is_active=False,
        )

        with pytest.raises(SalesNotFoundError):
            resolve_catalog_price("ART-001", Currency.USD, [price_list])

    def test_ignores_wrong_currency(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif CDF",
            currency=Currency.CDF,
            prices={"ART-001": money("150", Currency.CDF)},
        )

        with pytest.raises(SalesNotFoundError):
            resolve_catalog_price("ART-001", Currency.USD, [price_list])

    def test_ignores_list_without_requested_item(self) -> None:
        price_list = PriceList(
            id="PL-001",
            name="Tarif USD",
            currency=Currency.USD,
            prices={"ART-002": money("150")},
        )

        with pytest.raises(SalesNotFoundError):
            resolve_catalog_price("ART-001", Currency.USD, [price_list])

    def test_returns_first_matching_price(self) -> None:
        first = PriceList(
            id="PL-001",
            name="Tarif USD 1",
            currency=Currency.USD,
            prices={"ART-001": money("100")},
        )
        second = PriceList(
            id="PL-002",
            name="Tarif USD 2",
            currency=Currency.USD,
            prices={"ART-001": money("200")},
        )

        result = resolve_catalog_price(
            "ART-001",
            Currency.USD,
            [first, second],
        )

        assert result == money("100")

    def test_raises_not_found_when_no_lists_exist(self) -> None:
        with pytest.raises(SalesNotFoundError, match="Aucun prix direct"):
            resolve_catalog_price("ART-001", Currency.USD, [])
