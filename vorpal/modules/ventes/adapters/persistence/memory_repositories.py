"""Implementations en memoire avec snapshot/restauration pour tests et rollback."""

from __future__ import annotations

from dataclasses import dataclass

from vorpal.core import Currency
from vorpal.modules.ventes.domain.catalog import PriceList, SalesItem
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import SalesInvoice
from vorpal.modules.ventes.domain.orders import SalesOrder
from vorpal.modules.ventes.domain.payments import Payment, PaymentMethod
from vorpal.modules.ventes.domain.quotes import Quote
from vorpal.modules.ventes.domain.reminders import Reminder
from vorpal.modules.ventes.ports.repositories import (
    CatalogRepository,
    CustomerRepository,
    InvoiceRepository,
    OrderRepository,
    PaymentRepository,
    QuoteRepository,
    ReminderRepository,
)


@dataclass(frozen=True)
class SalesStoreSnapshot:
    """Instantane immutable des collections en memoire."""

    customers: dict[str, Customer]
    items: dict[str, SalesItem]
    price_lists: dict[str, PriceList]
    quotes: dict[str, Quote]
    orders: dict[str, SalesOrder]
    invoices: dict[str, SalesInvoice]
    payments: dict[str, Payment]
    reminders: dict[tuple[str, str], Reminder]
    sequences: dict[tuple[str, int], int]


class InMemorySalesStore:
    """Magasin partagé en memoire avec gestion transactionnelle par snapshot."""

    def __init__(self) -> None:
        self.customers: dict[str, Customer] = {}
        self.items: dict[str, SalesItem] = {}
        self.price_lists: dict[str, PriceList] = {}
        self.quotes: dict[str, Quote] = {}
        self.orders: dict[str, SalesOrder] = {}
        self.invoices: dict[str, SalesInvoice] = {}
        self.payments: dict[str, Payment] = {}
        self.reminders: dict[tuple[str, str], Reminder] = {}
        self.sequences: dict[tuple[str, int], int] = {}

    def snapshot(self) -> SalesStoreSnapshot:
        return SalesStoreSnapshot(
            customers=dict(self.customers),
            items=dict(self.items),
            price_lists=dict(self.price_lists),
            quotes=dict(self.quotes),
            orders=dict(self.orders),
            invoices=dict(self.invoices),
            payments=dict(self.payments),
            reminders=dict(self.reminders),
            sequences=dict(self.sequences),
        )

    def restore(self, snap: SalesStoreSnapshot) -> None:
        self.customers = dict(snap.customers)
        self.items = dict(snap.items)
        self.price_lists = dict(snap.price_lists)
        self.quotes = dict(snap.quotes)
        self.orders = dict(snap.orders)
        self.invoices = dict(snap.invoices)
        self.payments = dict(snap.payments)
        self.reminders = dict(snap.reminders)
        self.sequences = dict(snap.sequences)


class InMemoryCustomerRepository(CustomerRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get(self, customer_id: str) -> Customer | None:
        return self._store.customers.get(customer_id)

    def save(self, customer: Customer) -> None:
        self._store.customers[customer.id] = customer

    def list_all(self, active_only: bool = False) -> list[Customer]:
        items = list(self._store.customers.values())
        if active_only:
            items = [c for c in items if c.is_active]
        return sorted(items, key=lambda c: c.id)


class InMemoryCatalogRepository(CatalogRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get_item(self, code: str) -> SalesItem | None:
        return self._store.items.get(code)

    def save_item(self, item: SalesItem) -> None:
        self._store.items[item.code] = item

    def list_items(self, active_only: bool = False) -> list[SalesItem]:
        items = list(self._store.items.values())
        if active_only:
            items = [i for i in items if i.is_active]
        return sorted(items, key=lambda i: i.code)

    def get_price_list(self, price_list_id: str) -> PriceList | None:
        return self._store.price_lists.get(price_list_id)

    def save_price_list(self, price_list: PriceList) -> None:
        self._store.price_lists[price_list.id] = price_list

    def list_price_lists(self, currency: Currency | None = None, active_only: bool = True) -> list[PriceList]:
        lists = list(self._store.price_lists.values())
        if active_only:
            lists = [pl for pl in lists if pl.is_active]
        if currency is not None:
            lists = [pl for pl in lists if pl.currency == currency]
        return sorted(lists, key=lambda pl: pl.id)


class InMemoryQuoteRepository(QuoteRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get(self, quote_id: str) -> Quote | None:
        return self._store.quotes.get(quote_id)

    def save(self, quote: Quote) -> None:
        self._store.quotes[quote.id] = quote

    def list_all(self) -> list[Quote]:
        return sorted(self._store.quotes.values(), key=lambda q: q.id)


class InMemoryOrderRepository(OrderRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get(self, order_id: str) -> SalesOrder | None:
        return self._store.orders.get(order_id)

    def save(self, order: SalesOrder) -> None:
        self._store.orders[order.id] = order

    def list_all(self) -> list[SalesOrder]:
        return sorted(self._store.orders.values(), key=lambda o: o.id)


class InMemoryInvoiceRepository(InvoiceRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get(self, invoice_id: str) -> SalesInvoice | None:
        return self._store.invoices.get(invoice_id)

    def get_by_number(self, number: str) -> SalesInvoice | None:
        for inv in self._store.invoices.values():
            if inv.number == number:
                return inv
        return None

    def save(self, invoice: SalesInvoice) -> None:
        self._store.invoices[invoice.id] = invoice

    def list_all(self) -> list[SalesInvoice]:
        return sorted(self._store.invoices.values(), key=lambda i: i.id)

    def list_credit_notes_for_invoice(self, original_invoice_id: str) -> list[SalesInvoice]:
        cns = [
            inv
            for inv in self._store.invoices.values()
            if inv.invoice_type == "avoir" and inv.original_invoice_id == original_invoice_id
        ]
        return sorted(cns, key=lambda i: i.id)


class InMemoryPaymentRepository(PaymentRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get(self, payment_id: str) -> Payment | None:
        return self._store.payments.get(payment_id)

    def get_by_method_and_reference(self, method: PaymentMethod, reference: str) -> Payment | None:
        for pay in self._store.payments.values():
            if pay.method == method and pay.reference == reference:
                return pay
        return None

    def save(self, payment: Payment) -> None:
        self._store.payments[payment.id] = payment

    def list_by_customer(self, customer_id: str) -> list[Payment]:
        pays = [p for p in self._store.payments.values() if p.customer_id == customer_id]
        return sorted(pays, key=lambda p: p.id)

    def list_by_invoice(self, invoice_id: str) -> list[Payment]:
        matched = [
            p
            for p in self._store.payments.values()
            if any(alloc.invoice_id == invoice_id for alloc in p.allocations)
        ]
        return sorted(matched, key=lambda p: p.id)


class InMemoryReminderRepository(ReminderRepository):
    def __init__(self, store: InMemorySalesStore) -> None:
        self._store = store

    def get_by_invoice_and_level(self, invoice_id: str, level_code: str) -> Reminder | None:
        return self._store.reminders.get((invoice_id, level_code))

    def save(self, reminder: Reminder) -> None:
        self._store.reminders[(reminder.invoice_id, reminder.level_code)] = reminder

    def list_by_invoice(self, invoice_id: str) -> list[Reminder]:
        items = [r for (inv_id, _), r in self._store.reminders.items() if inv_id == invoice_id]
        return sorted(items, key=lambda r: r.id)
