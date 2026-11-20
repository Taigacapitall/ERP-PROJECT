"""Tests des repositories en mémoire de Ventes."""

from types import SimpleNamespace

import pytest

from vorpal.core import Currency
from vorpal.modules.ventes.adapters.persistence.memory_repositories import (
    InMemoryCatalogRepository,
    InMemoryCustomerRepository,
    InMemoryInvoiceRepository,
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
    InMemoryQuoteRepository,
    InMemoryReminderRepository,
    InMemorySalesStore,
)
from vorpal.modules.ventes.domain.payments import PaymentMethod


def record(**attributes: object) -> SimpleNamespace:
    """Construit un objet léger exposant les attributs attendus."""
    return SimpleNamespace(**attributes)


@pytest.fixture
def store() -> InMemorySalesStore:
    return InMemorySalesStore()


@pytest.fixture
def customer_repo(store: InMemorySalesStore) -> InMemoryCustomerRepository:
    return InMemoryCustomerRepository(store)


@pytest.fixture
def catalog_repo(store: InMemorySalesStore) -> InMemoryCatalogRepository:
    return InMemoryCatalogRepository(store)


@pytest.fixture
def quote_repo(store: InMemorySalesStore) -> InMemoryQuoteRepository:
    return InMemoryQuoteRepository(store)


@pytest.fixture
def order_repo(store: InMemorySalesStore) -> InMemoryOrderRepository:
    return InMemoryOrderRepository(store)


@pytest.fixture
def invoice_repo(store: InMemorySalesStore) -> InMemoryInvoiceRepository:
    return InMemoryInvoiceRepository(store)


@pytest.fixture
def payment_repo(store: InMemorySalesStore) -> InMemoryPaymentRepository:
    return InMemoryPaymentRepository(store)


@pytest.fixture
def reminder_repo(store: InMemorySalesStore) -> InMemoryReminderRepository:
    return InMemoryReminderRepository(store)


# ---------------------------------------------------------------------------
# InMemorySalesStore
# ---------------------------------------------------------------------------


def test_store_initializes_all_collections_empty(store: InMemorySalesStore) -> None:
    assert store.customers == {}
    assert store.items == {}
    assert store.price_lists == {}
    assert store.quotes == {}
    assert store.orders == {}
    assert store.invoices == {}
    assert store.payments == {}
    assert store.reminders == {}
    assert store.sequences == {}


def test_snapshot_copies_all_collection_dictionaries(
    store: InMemorySalesStore,
) -> None:
    customer = record(id="C-1")
    item = record(code="ITEM-1")
    price_list = record(id="PL-1")
    quote = record(id="Q-1")
    order = record(id="O-1")
    invoice = record(id="I-1")
    payment = record(id="P-1")
    reminder = record(id="R-1")

    store.customers["C-1"] = customer
    store.items["ITEM-1"] = item
    store.price_lists["PL-1"] = price_list
    store.quotes["Q-1"] = quote
    store.orders["O-1"] = order
    store.invoices["I-1"] = invoice
    store.payments["P-1"] = payment
    store.reminders[("I-1", "LEVEL-1")] = reminder
    store.sequences[("INV", 2026)] = 7

    snapshot = store.snapshot()

    assert snapshot.customers == {"C-1": customer}
    assert snapshot.items == {"ITEM-1": item}
    assert snapshot.price_lists == {"PL-1": price_list}
    assert snapshot.quotes == {"Q-1": quote}
    assert snapshot.orders == {"O-1": order}
    assert snapshot.invoices == {"I-1": invoice}
    assert snapshot.payments == {"P-1": payment}
    assert snapshot.reminders == {("I-1", "LEVEL-1"): reminder}
    assert snapshot.sequences == {("INV", 2026): 7}

    assert snapshot.customers is not store.customers
    assert snapshot.items is not store.items
    assert snapshot.price_lists is not store.price_lists
    assert snapshot.quotes is not store.quotes
    assert snapshot.orders is not store.orders
    assert snapshot.invoices is not store.invoices
    assert snapshot.payments is not store.payments
    assert snapshot.reminders is not store.reminders
    assert snapshot.sequences is not store.sequences


def test_snapshot_is_not_changed_by_later_dictionary_mutations(
    store: InMemorySalesStore,
) -> None:
    original = record(id="C-1")
    store.customers["C-1"] = original

    snapshot = store.snapshot()
    store.customers["C-2"] = record(id="C-2")
    del store.customers["C-1"]

    assert snapshot.customers == {"C-1": original}


def test_restore_replaces_all_collections_with_snapshot_contents(
    store: InMemorySalesStore,
) -> None:
    original_values = {
        "customers": {"C-1": record(id="C-1")},
        "items": {"ITEM-1": record(code="ITEM-1")},
        "price_lists": {"PL-1": record(id="PL-1")},
        "quotes": {"Q-1": record(id="Q-1")},
        "orders": {"O-1": record(id="O-1")},
        "invoices": {"I-1": record(id="I-1")},
        "payments": {"P-1": record(id="P-1")},
        "reminders": {("I-1", "L1"): record(id="R-1")},
        "sequences": {("INV", 2026): 4},
    }

    for name, values in original_values.items():
        setattr(store, name, values)

    snapshot = store.snapshot()

    for name in original_values:
        setattr(store, name, {"changed": record(id="changed")})

    store.restore(snapshot)

    for name, values in original_values.items():
        assert getattr(store, name) == values
        assert getattr(store, name) is not values


def test_restore_does_not_alias_snapshot_dictionaries(
    store: InMemorySalesStore,
) -> None:
    store.customers["C-1"] = record(id="C-1")
    snapshot = store.snapshot()

    store.restore(snapshot)
    store.customers["C-2"] = record(id="C-2")

    assert "C-2" not in snapshot.customers


# ---------------------------------------------------------------------------
# InMemoryCustomerRepository
# ---------------------------------------------------------------------------


def test_customer_get_returns_none_for_unknown_id(
    customer_repo: InMemoryCustomerRepository,
) -> None:
    assert customer_repo.get("UNKNOWN") is None


def test_customer_save_and_get_return_same_object(
    customer_repo: InMemoryCustomerRepository,
) -> None:
    customer = record(id="C-1", is_active=True)

    customer_repo.save(customer)

    assert customer_repo.get("C-1") is customer


def test_customer_save_replaces_existing_customer(
    customer_repo: InMemoryCustomerRepository,
) -> None:
    first = record(id="C-1", is_active=True)
    replacement = record(id="C-1", is_active=False)

    customer_repo.save(first)
    customer_repo.save(replacement)

    assert customer_repo.get("C-1") is replacement
    assert customer_repo.list_all() == [replacement]


def test_customer_list_all_sorts_by_id(
    customer_repo: InMemoryCustomerRepository,
) -> None:
    customers = [
        record(id="C-3", is_active=True),
        record(id="C-1", is_active=True),
        record(id="C-2", is_active=True),
    ]
    for customer in customers:
        customer_repo.save(customer)

    assert [c.id for c in customer_repo.list_all()] == ["C-1", "C-2", "C-3"]


def test_customer_list_all_active_only_filters_inactive(
    customer_repo: InMemoryCustomerRepository,
) -> None:
    active = record(id="C-2", is_active=True)
    inactive = record(id="C-1", is_active=False)
    customer_repo.save(inactive)
    customer_repo.save(active)

    assert customer_repo.list_all(active_only=True) == [active]
    assert customer_repo.list_all() == [inactive, active]


def test_customer_list_all_returns_empty_list_when_store_is_empty(
    customer_repo: InMemoryCustomerRepository,
) -> None:
    assert customer_repo.list_all() == []
    assert customer_repo.list_all(active_only=True) == []


# ---------------------------------------------------------------------------
# InMemoryCatalogRepository
# ---------------------------------------------------------------------------


def test_catalog_item_get_save_and_replace(
    catalog_repo: InMemoryCatalogRepository,
) -> None:
    assert catalog_repo.get_item("UNKNOWN") is None

    first = record(code="ITEM-1", is_active=True)
    replacement = record(code="ITEM-1", is_active=False)

    catalog_repo.save_item(first)
    assert catalog_repo.get_item("ITEM-1") is first

    catalog_repo.save_item(replacement)
    assert catalog_repo.get_item("ITEM-1") is replacement
    assert catalog_repo.list_items() == [replacement]


def test_catalog_list_items_sorts_by_code(
    catalog_repo: InMemoryCatalogRepository,
) -> None:
    for code in ("Z-3", "A-1", "M-2"):
        catalog_repo.save_item(record(code=code, is_active=True))

    assert [item.code for item in catalog_repo.list_items()] == [
        "A-1",
        "M-2",
        "Z-3",
    ]


def test_catalog_list_items_active_only(
    catalog_repo: InMemoryCatalogRepository,
) -> None:
    active = record(code="A-1", is_active=True)
    inactive = record(code="B-2", is_active=False)
    catalog_repo.save_item(inactive)
    catalog_repo.save_item(active)

    assert catalog_repo.list_items(active_only=True) == [active]
    assert catalog_repo.list_items() == [active, inactive]


def test_catalog_price_list_get_save_and_replace(
    catalog_repo: InMemoryCatalogRepository,
) -> None:
    assert catalog_repo.get_price_list("UNKNOWN") is None

    first = record(id="PL-1", currency=Currency.USD, is_active=True)
    replacement = record(id="PL-1", currency=Currency.CDF, is_active=False)

    catalog_repo.save_price_list(first)
    assert catalog_repo.get_price_list("PL-1") is first

    catalog_repo.save_price_list(replacement)
    assert catalog_repo.get_price_list("PL-1") is replacement


def test_catalog_list_price_lists_sorts_and_filters(
    catalog_repo: InMemoryCatalogRepository,
) -> None:
    usd_active = record(id="PL-2", currency=Currency.USD, is_active=True)
    usd_inactive = record(id="PL-1", currency=Currency.USD, is_active=False)
    cdf_active = record(id="PL-3", currency=Currency.CDF, is_active=True)

    for price_list in (usd_active, usd_inactive, cdf_active):
        catalog_repo.save_price_list(price_list)

    # Par défaut, seules les listes actives sont retournées.
    assert catalog_repo.list_price_lists() == [usd_active, cdf_active]

    # Le filtre de devise s'applique en plus du filtre actif.
    assert catalog_repo.list_price_lists(currency=Currency.USD) == [usd_active]

    # active_only=False inclut les listes inactives.
    assert catalog_repo.list_price_lists(active_only=False) == [
        usd_inactive,
        usd_active,
        cdf_active,
    ]
    assert catalog_repo.list_price_lists(
        currency=Currency.USD,
        active_only=False,
    ) == [usd_inactive, usd_active]


def test_catalog_lists_return_empty_lists_when_store_is_empty(
    catalog_repo: InMemoryCatalogRepository,
) -> None:
    assert catalog_repo.list_items() == []
    assert catalog_repo.list_items(active_only=True) == []
    assert catalog_repo.list_price_lists() == []
    assert catalog_repo.list_price_lists(currency=Currency.CDF) == []


# ---------------------------------------------------------------------------
# InMemoryQuoteRepository
# ---------------------------------------------------------------------------


def test_quote_get_save_and_replace(
    quote_repo: InMemoryQuoteRepository,
) -> None:
    assert quote_repo.get("UNKNOWN") is None

    first = record(id="Q-1")
    replacement = record(id="Q-1", revision=2)

    quote_repo.save(first)
    assert quote_repo.get("Q-1") is first

    quote_repo.save(replacement)
    assert quote_repo.get("Q-1") is replacement


def test_quote_list_all_sorts_by_id(
    quote_repo: InMemoryQuoteRepository,
) -> None:
    for quote_id in ("Q-3", "Q-1", "Q-2"):
        quote_repo.save(record(id=quote_id))

    assert [quote.id for quote in quote_repo.list_all()] == [
        "Q-1",
        "Q-2",
        "Q-3",
    ]
    assert quote_repo.list_all() == quote_repo.list_all()


def test_quote_list_all_empty(quote_repo: InMemoryQuoteRepository) -> None:
    assert quote_repo.list_all() == []


# ---------------------------------------------------------------------------
# InMemoryOrderRepository
# ---------------------------------------------------------------------------


def test_order_get_save_and_replace(
    order_repo: InMemoryOrderRepository,
) -> None:
    assert order_repo.get("UNKNOWN") is None

    first = record(id="O-1")
    replacement = record(id="O-1", revision=2)

    order_repo.save(first)
    assert order_repo.get("O-1") is first

    order_repo.save(replacement)
    assert order_repo.get("O-1") is replacement


def test_order_list_all_sorts_by_id(
    order_repo: InMemoryOrderRepository,
) -> None:
    for order_id in ("O-3", "O-1", "O-2"):
        order_repo.save(record(id=order_id))

    assert [order.id for order in order_repo.list_all()] == [
        "O-1",
        "O-2",
        "O-3",
    ]


def test_order_list_all_empty(order_repo: InMemoryOrderRepository) -> None:
    assert order_repo.list_all() == []


# ---------------------------------------------------------------------------
# InMemoryInvoiceRepository
# ---------------------------------------------------------------------------


def test_invoice_get_save_and_replace(
    invoice_repo: InMemoryInvoiceRepository,
) -> None:
    assert invoice_repo.get("UNKNOWN") is None

    first = record(id="I-1", number="INV-001")
    replacement = record(id="I-1", number="INV-002")

    invoice_repo.save(first)
    assert invoice_repo.get("I-1") is first

    invoice_repo.save(replacement)
    assert invoice_repo.get("I-1") is replacement


def test_invoice_get_by_number_returns_matching_invoice_or_none(
    invoice_repo: InMemoryInvoiceRepository,
) -> None:
    invoice = record(id="I-1", number="INV-001")
    invoice_repo.save(invoice)

    assert invoice_repo.get_by_number("INV-001") is invoice
    assert invoice_repo.get_by_number("UNKNOWN") is None


def test_invoice_list_all_sorts_by_id(
    invoice_repo: InMemoryInvoiceRepository,
) -> None:
    for invoice_id in ("I-3", "I-1", "I-2"):
        invoice_repo.save(record(id=invoice_id, number=f"INV-{invoice_id}"))

    assert [invoice.id for invoice in invoice_repo.list_all()] == [
        "I-1",
        "I-2",
        "I-3",
    ]


def test_invoice_list_credit_notes_filters_original_invoice_and_type(
    invoice_repo: InMemoryInvoiceRepository,
) -> None:
    credit_2 = record(
        id="I-3",
        invoice_type="avoir",
        original_invoice_id="I-1",
    )
    credit_1 = record(
        id="I-2",
        invoice_type="avoir",
        original_invoice_id="I-1",
    )
    unrelated_credit = record(
        id="I-4",
        invoice_type="avoir",
        original_invoice_id="I-9",
    )
    regular_invoice = record(
        id="I-5",
        invoice_type="vente",
        original_invoice_id="I-1",
    )

    for invoice in (credit_2, unrelated_credit, regular_invoice, credit_1):
        invoice_repo.save(invoice)

    assert invoice_repo.list_credit_notes_for_invoice("I-1") == [
        credit_1,
        credit_2,
    ]
    assert invoice_repo.list_credit_notes_for_invoice("UNKNOWN") == []


def test_invoice_list_all_empty(invoice_repo: InMemoryInvoiceRepository) -> None:
    assert invoice_repo.list_all() == []


# ---------------------------------------------------------------------------
# InMemoryPaymentRepository
# ---------------------------------------------------------------------------


def test_payment_get_save_and_replace(
    payment_repo: InMemoryPaymentRepository,
) -> None:
    assert payment_repo.get("UNKNOWN") is None

    first = record(id="P-1", customer_id="C-1", allocations=())
    replacement = record(id="P-1", customer_id="C-2", allocations=())

    payment_repo.save(first)
    assert payment_repo.get("P-1") is first

    payment_repo.save(replacement)
    assert payment_repo.get("P-1") is replacement


def test_payment_get_by_method_and_reference(
    payment_repo: InMemoryPaymentRepository,
) -> None:
    payment = record(
        id="P-1",
        method=PaymentMethod.MOBILE_MONEY,
        reference="TX-123",
        customer_id="C-1",
        allocations=(),
    )
    payment_repo.save(payment)

    assert (
        payment_repo.get_by_method_and_reference(
            PaymentMethod.MOBILE_MONEY,
            "TX-123",
        )
        is payment
    )
    assert (
        payment_repo.get_by_method_and_reference(
            PaymentMethod.CASH,
            "TX-123",
        )
        is None
    )
    assert (
        payment_repo.get_by_method_and_reference(
            PaymentMethod.MOBILE_MONEY,
            "UNKNOWN",
        )
        is None
    )


def test_payment_list_by_customer_filters_and_sorts(
    payment_repo: InMemoryPaymentRepository,
) -> None:
    p3 = record(id="P-3", customer_id="C-1", allocations=())
    p1 = record(id="P-1", customer_id="C-1", allocations=())
    p2 = record(id="P-2", customer_id="C-2", allocations=())

    for payment in (p3, p2, p1):
        payment_repo.save(payment)

    assert payment_repo.list_by_customer("C-1") == [p1, p3]
    assert payment_repo.list_by_customer("C-2") == [p2]
    assert payment_repo.list_by_customer("UNKNOWN") == []


def test_payment_list_by_invoice_matches_any_allocation_and_sorts(
    payment_repo: InMemoryPaymentRepository,
) -> None:
    p3 = record(
        id="P-3",
        customer_id="C-1",
        allocations=(record(invoice_id="I-1"),),
    )
    p1 = record(
        id="P-1",
        customer_id="C-2",
        allocations=(
            record(invoice_id="I-9"),
            record(invoice_id="I-1"),
        ),
    )
    p2 = record(
        id="P-2",
        customer_id="C-1",
        allocations=(record(invoice_id="I-2"),),
    )
    p4 = record(
        id="P-4",
        customer_id="C-1",
        allocations=(),
    )

    for payment in (p3, p2, p4, p1):
        payment_repo.save(payment)

    assert payment_repo.list_by_invoice("I-1") == [p1, p3]
    assert payment_repo.list_by_invoice("I-2") == [p2]
    assert payment_repo.list_by_invoice("UNKNOWN") == []


def test_payment_list_methods_return_empty_lists(
    payment_repo: InMemoryPaymentRepository,
) -> None:
    assert payment_repo.list_by_customer("C-1") == []
    assert payment_repo.list_by_invoice("I-1") == []


# ---------------------------------------------------------------------------
# InMemoryReminderRepository
# ---------------------------------------------------------------------------


def test_reminder_get_save_and_replace(
    reminder_repo: InMemoryReminderRepository,
) -> None:
    assert reminder_repo.get_by_invoice_and_level("I-1", "L1") is None

    first = record(
        id="R-1",
        invoice_id="I-1",
        level_code="L1",
    )
    replacement = record(
        id="R-2",
        invoice_id="I-1",
        level_code="L1",
    )

    reminder_repo.save(first)
    assert reminder_repo.get_by_invoice_and_level("I-1", "L1") is first

    reminder_repo.save(replacement)
    assert reminder_repo.get_by_invoice_and_level("I-1", "L1") is replacement


def test_reminder_repository_uses_invoice_and_level_as_composite_key(
    reminder_repo: InMemoryReminderRepository,
) -> None:
    first_level = record(id="R-1", invoice_id="I-1", level_code="L1")
    second_level = record(id="R-2", invoice_id="I-1", level_code="L2")
    other_invoice = record(id="R-3", invoice_id="I-2", level_code="L1")

    for reminder in (first_level, second_level, other_invoice):
        reminder_repo.save(reminder)

    assert reminder_repo.get_by_invoice_and_level("I-1", "L1") is first_level
    assert reminder_repo.get_by_invoice_and_level("I-1", "L2") is second_level
    assert reminder_repo.get_by_invoice_and_level("I-2", "L1") is other_invoice


def test_reminder_list_by_invoice_filters_and_sorts(
    reminder_repo: InMemoryReminderRepository,
) -> None:
    r3 = record(id="R-3", invoice_id="I-1", level_code="L3")
    r1 = record(id="R-1", invoice_id="I-1", level_code="L1")
    r2 = record(id="R-2", invoice_id="I-2", level_code="L1")

    for reminder in (r3, r2, r1):
        reminder_repo.save(reminder)

    assert reminder_repo.list_by_invoice("I-1") == [r1, r3]
    assert reminder_repo.list_by_invoice("I-2") == [r2]
    assert reminder_repo.list_by_invoice("UNKNOWN") == []


def test_reminder_list_by_invoice_empty(
    reminder_repo: InMemoryReminderRepository,
) -> None:
    assert reminder_repo.list_by_invoice("I-1") == []
