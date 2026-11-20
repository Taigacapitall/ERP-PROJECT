"""Tests comportementaux du service de tarification Ventes."""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from vorpal.core import (
    AuditLog,
    AuthorizationService,
    Currency,
    Money,
    User,
)
from vorpal.modules.ventes.domain.catalog import PriceList, SalesItem
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.services.pricing import PricingService
from vorpal.modules.ventes.settings import RoundingSettings


class FakeCatalog:
    def __init__(self) -> None:
        self.items: dict[str, SalesItem] = {}
        self.price_lists: dict[str, PriceList] = {}

    def get_item(self, code: str) -> SalesItem | None:
        return self.items.get(code)

    def save_item(self, item: SalesItem) -> None:
        self.items[item.code] = item

    def list_price_lists(self, active_only: bool = True) -> list[PriceList]:
        lists = list(self.price_lists.values())
        if active_only:
            lists = [item for item in lists if item.is_active]
        return lists

    def get_price_list(self, list_id: str) -> PriceList | None:
        return self.price_lists.get(list_id)

    def save_price_list(self, price_list: PriceList) -> None:
        self.price_lists[price_list.id] = price_list


class FakeUow:
    def __init__(self, catalog: FakeCatalog) -> None:
        self.catalog = catalog

    def __enter__(self) -> "FakeUow":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakePublisher:
    def invoice_ready_for_certification(self, invoice: Any) -> None:
        pass

    def payment_received(self, payment: Any) -> None:
        pass


class FakeRegistry:
    def __init__(self, provider: Any = None) -> None:
        self.provider = provider

    def get_optional(self, interface: Any) -> Any:
        return self.provider


class FakeRateProvider:
    def __init__(self, rate: Any = Decimal("2800")) -> None:
        self.rate = rate
        self.calls: list[tuple[Currency, Currency, date]] = []

    def get_rate(
        self,
        source: Currency,
        target: Currency,
        on_date: date,
    ) -> Any:
        self.calls.append((source, target, on_date))
        if isinstance(self.rate, Exception):
            raise self.rate
        return self.rate


@pytest.fixture
def env() -> SimpleNamespace:
    catalog = FakeCatalog()
    audit = AuditLog()
    timestamp = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)

    settings = SimpleNamespace(
        rounding=RoundingSettings(USD=2, CDF=0),
        get_tax_group=lambda code: (
            SimpleNamespace(
                code=code,
                vat_rate=Decimal("0.16"),
                is_out_of_scope=False,
            )
            if code == "VAT16"
            else None
        ),
    )

    deps = SimpleNamespace(
        uow_factory=lambda: FakeUow(catalog),
        publisher=FakePublisher(),
        audit=audit,
        authz=AuthorizationService(),
        clock=lambda: timestamp,
        logger=logging.getLogger("test_pricing"),
        settings=settings,
        id_factory=lambda: "PL-GENERATED",
    )

    actor = User(
        id="USER-01",
        username="test-user",
        direct_permissions=frozenset({"ventes.admin"}),
    )

    service = PricingService(deps, FakeRegistry())

    return SimpleNamespace(
        catalog=catalog,
        audit=audit,
        deps=deps,
        actor=actor,
        service=service,
        timestamp=timestamp,
    )


def add_item(env: SimpleNamespace, active: bool = True) -> SalesItem:
    item = SalesItem(
        code="ITEM-01",
        designation="Article de test",
        is_active=active,
    )
    env.catalog.save_item(item)
    return item


def add_price_list(
    env: SimpleNamespace,
    list_id: str,
    currency: Currency,
    amount: str,
    active: bool = True,
) -> PriceList:
    price_list = PriceList(
        id=list_id,
        name=f"Liste {list_id}",
        currency=currency,
        prices={
            "ITEM-01": Money(Decimal(amount), currency),
        },
        is_active=active,
    )
    env.catalog.save_price_list(price_list)
    return price_list


def test_get_unit_price_returns_direct_price(env: SimpleNamespace) -> None:
    add_item(env)
    add_price_list(env, "PL-USD", Currency.USD, "125.456")

    result = env.service.get_unit_price("ITEM-01", Currency.USD)

    assert result.unit_price == Money(Decimal("125.46"), Currency.USD)
    assert result.converted is False
    assert result.rate == Decimal("1")


def test_get_unit_price_uses_sorted_active_price_lists(
    env: SimpleNamespace,
) -> None:
    add_item(env)
    add_price_list(env, "Z-LIST", Currency.USD, "200")
    add_price_list(env, "A-LIST", Currency.USD, "100")

    result = env.service.get_unit_price("ITEM-01", Currency.USD)

    assert result.unit_price == Money(Decimal("100.00"), Currency.USD)


def test_get_unit_price_ignores_inactive_price_lists(
    env: SimpleNamespace,
) -> None:
    add_item(env)
    add_price_list(env, "PL-USD", Currency.USD, "100", active=False)

    with pytest.raises(SalesNotFoundError):
        env.service.get_unit_price("ITEM-01", Currency.USD)


def test_get_unit_price_rejects_unknown_item(env: SimpleNamespace) -> None:
    with pytest.raises(SalesNotFoundError):
        env.service.get_unit_price("UNKNOWN", Currency.USD)


def test_get_unit_price_rejects_inactive_item(env: SimpleNamespace) -> None:
    add_item(env, active=False)

    with pytest.raises(SalesConflictError):
        env.service.get_unit_price("ITEM-01", Currency.USD)


def test_get_unit_price_reports_missing_prices_in_both_currencies(
    env: SimpleNamespace,
) -> None:
    add_item(env)

    with pytest.raises(SalesNotFoundError, match="ni en"):
        env.service.get_unit_price("ITEM-01", Currency.USD)


def test_get_unit_price_converts_cdf_to_usd(env: SimpleNamespace) -> None:
    add_item(env)
    add_price_list(env, "PL-CDF", Currency.CDF, "280000")
    provider = FakeRateProvider(Decimal("0.000357142857"))
    env.service = PricingService(env.deps, FakeRegistry(provider))

    result = env.service.get_unit_price(
        "ITEM-01",
        Currency.USD,
        date(2026, 11, 19),
    )

    assert result.converted is True
    assert result.rate == Decimal("0.000357142857")
    assert result.unit_price == Money(Decimal("100.00"), Currency.USD)
    assert provider.calls == [
        (Currency.CDF, Currency.USD, date(2026, 11, 19))
    ]


def test_get_unit_price_converts_usd_to_cdf(env: SimpleNamespace) -> None:
    add_item(env)
    add_price_list(env, "PL-USD", Currency.USD, "10")
    provider = FakeRateProvider(Decimal("2800"))
    env.service = PricingService(env.deps, FakeRegistry(provider))

    result = env.service.get_unit_price("ITEM-01", Currency.CDF)

    assert result.converted is True
    assert result.unit_price == Money(Decimal("28000"), Currency.CDF)
    assert provider.calls[0][0:2] == (Currency.USD, Currency.CDF)


def test_get_unit_price_requires_exchange_rate_provider(
    env: SimpleNamespace,
) -> None:
    add_item(env)
    add_price_list(env, "PL-CDF", Currency.CDF, "280000")

    with pytest.raises(RuntimeError, match="indisponible"):
        env.service.get_unit_price("ITEM-01", Currency.USD)


def test_get_unit_price_wraps_provider_error(env: SimpleNamespace) -> None:
    add_item(env)
    add_price_list(env, "PL-CDF", Currency.CDF, "280000")
    provider = FakeRateProvider(RuntimeError("provider failure"))
    env.service = PricingService(env.deps, FakeRegistry(provider))

    with pytest.raises(RuntimeError, match="Taux de change indisponible"):
        env.service.get_unit_price("ITEM-01", Currency.USD)


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-1")])
def test_get_unit_price_rejects_non_positive_rate(
    env: SimpleNamespace,
    rate: Decimal,
) -> None:
    add_item(env)
    add_price_list(env, "PL-CDF", Currency.CDF, "280000")
    env.service = PricingService(
        env.deps,
        FakeRegistry(FakeRateProvider(rate)),
    )

    with pytest.raises(SalesValidationError):
        env.service.get_unit_price("ITEM-01", Currency.USD)


def test_save_item_creates_item_and_records_audit(
    env: SimpleNamespace,
) -> None:
    item = env.service.save_item(
        env.actor,
        code="NEW-01",
        designation="Nouvel article",
        tax_group="VAT16",
    )

    assert item == env.catalog.get_item("NEW-01")
    assert item.vat_rate == Decimal("0.16")
    assert len(env.audit.entries) == 1
    assert env.audit.entries[0].action == "SAVE_CATALOG_ITEM"


def test_save_item_updates_existing_item(env: SimpleNamespace) -> None:
    add_item(env)

    item = env.service.save_item(
        env.actor,
        code="ITEM-01",
        designation="Article modifié",
        tax_group="VAT16",
        is_active=False,
    )

    assert item.designation == "Article modifié"
    assert item.is_active is False
    assert env.audit.entries[0].before == {"is_active": "True"}


def test_save_item_requires_tax_group(env: SimpleNamespace) -> None:
    with pytest.raises(SalesValidationError, match="obligatoire"):
        env.service.save_item(
            env.actor,
            code="NEW-01",
            designation="Article",
        )


def test_save_item_rejects_unknown_tax_group(env: SimpleNamespace) -> None:
    with pytest.raises(SalesValidationError, match="inconnu"):
        env.service.save_item(
            env.actor,
            code="NEW-01",
            designation="Article",
            tax_group="UNKNOWN",
        )


def test_save_item_forces_zero_vat_out_of_scope(
    env: SimpleNamespace,
) -> None:
    item = env.service.save_item(
        env.actor,
        code="NEW-01",
        designation="Article hors champ",
        tax_group="VAT16",
        is_out_of_scope=True,
    )

    assert item.is_out_of_scope is True
    assert item.vat_rate == Decimal("0")


def test_save_item_requires_admin_permission(env: SimpleNamespace) -> None:
    unauthorized = User(id="U2", username="reader")

    with pytest.raises(SalesComplianceError):
        env.service.save_item(
            unauthorized,
            code="NEW-01",
            designation="Article",
            tax_group="VAT16",
        )


def test_create_price_list_uses_generated_id(env: SimpleNamespace) -> None:
    result = env.service.create_price_list(
        env.actor,
        name="Tarifs USD",
        currency=Currency.USD,
    )

    assert result.id == "PL-GENERATED"
    assert env.catalog.get_price_list(result.id) == result
    assert env.audit.entries[0].action == "CREATE_PRICE_LIST"


def test_create_price_list_uses_explicit_id_and_prices(
    env: SimpleNamespace,
) -> None:
    prices = {
        "ITEM-01": Money(Decimal("50"), Currency.USD),
    }

    result = env.service.create_price_list(
        env.actor,
        name="Tarifs spéciaux",
        currency=Currency.USD,
        prices=prices,
        price_list_id="CUSTOM-ID",
        is_active=False,
    )

    assert result.id == "CUSTOM-ID"
    assert result.prices == prices
    assert result.is_active is False


def test_create_price_list_rejects_duplicate_id(
    env: SimpleNamespace,
) -> None:
    add_price_list(env, "EXISTING", Currency.USD, "10")

    with pytest.raises(SalesConflictError):
        env.service.create_price_list(
            env.actor,
            name="Doublon",
            currency=Currency.USD,
            price_list_id="EXISTING",
        )


def test_set_unit_price_updates_price_and_records_audit(
    env: SimpleNamespace,
) -> None:
    add_item(env)
    add_price_list(env, "PL-USD", Currency.USD, "10")

    updated = env.service.set_unit_price(
        env.actor,
        "PL-USD",
        "ITEM-01",
        Money(Decimal("25.50"), Currency.USD),
    )

    assert updated.get_unit_price("ITEM-01") == Money(
        Decimal("25.50"), Currency.USD
    )
    assert env.audit.entries[0].action == "SET_UNIT_PRICE"
    assert env.audit.entries[0].before == {
        "item_code": "ITEM-01",
        "amount": "10",
    }


def test_set_unit_price_rejects_unknown_price_list(
    env: SimpleNamespace,
) -> None:
    with pytest.raises(SalesNotFoundError):
        env.service.set_unit_price(
            env.actor,
            "UNKNOWN",
            "ITEM-01",
            Money(Decimal("10"), Currency.USD),
        )


def test_set_unit_price_rejects_unknown_item(
    env: SimpleNamespace,
) -> None:
    add_price_list(env, "PL-USD", Currency.USD, "10")

    with pytest.raises(SalesNotFoundError):
        env.service.set_unit_price(
            env.actor,
            "PL-USD",
            "UNKNOWN",
            Money(Decimal("10"), Currency.USD),
        )


def test_set_unit_price_requires_admin_permission(
    env: SimpleNamespace,
) -> None:
    unauthorized = User(id="U2", username="reader")

    with pytest.raises(SalesComplianceError):
        env.service.set_unit_price(
            unauthorized,
            "PL-USD",
            "ITEM-01",
            Money(Decimal("10"), Currency.USD),
        )
