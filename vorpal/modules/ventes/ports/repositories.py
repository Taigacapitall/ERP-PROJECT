"""Interfaces de persistance et de numerotation sequentielle."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vorpal.core import Currency
from vorpal.modules.ventes.domain.catalog import PriceList, SalesItem
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import SalesInvoice
from vorpal.modules.ventes.domain.orders import SalesOrder
from vorpal.modules.ventes.domain.payments import Payment, PaymentMethod
from vorpal.modules.ventes.domain.quotes import Quote
from vorpal.modules.ventes.domain.reminders import Reminder


class CustomerRepository(ABC):
    """Port de persistance des clients."""

    @abstractmethod
    def get(self, customer_id: str) -> Customer | None: ...

    @abstractmethod
    def save(self, customer: Customer) -> None: ...

    @abstractmethod
    def list_all(self, active_only: bool = False) -> list[Customer]: ...


class CatalogRepository(ABC):
    """Port de persistance des articles et listes de prix."""

    @abstractmethod
    def get_item(self, code: str) -> SalesItem | None: ...

    @abstractmethod
    def save_item(self, item: SalesItem) -> None: ...

    @abstractmethod
    def list_items(self, active_only: bool = False) -> list[SalesItem]: ...

    @abstractmethod
    def get_price_list(self, price_list_id: str) -> PriceList | None: ...

    @abstractmethod
    def save_price_list(self, price_list: PriceList) -> None: ...

    @abstractmethod
    def list_price_lists(self, currency: Currency | None = None, active_only: bool = True) -> list[PriceList]: ...


class QuoteRepository(ABC):
    """Port de persistance des devis."""

    @abstractmethod
    def get(self, quote_id: str) -> Quote | None: ...

    @abstractmethod
    def save(self, quote: Quote) -> None: ...

    @abstractmethod
    def list_all(self) -> list[Quote]: ...


class OrderRepository(ABC):
    """Port de persistance des commandes clients."""

    @abstractmethod
    def get(self, order_id: str) -> SalesOrder | None: ...

    @abstractmethod
    def save(self, order: SalesOrder) -> None: ...

    @abstractmethod
    def list_all(self) -> list[SalesOrder]: ...


class InvoiceRepository(ABC):
    """Port de persistance des factures et avoirs."""

    @abstractmethod
    def get(self, invoice_id: str) -> SalesInvoice | None: ...

    @abstractmethod
    def get_by_number(self, number: str) -> SalesInvoice | None: ...

    @abstractmethod
    def save(self, invoice: SalesInvoice) -> None: ...

    @abstractmethod
    def list_all(self) -> list[SalesInvoice]: ...

    @abstractmethod
    def list_credit_notes_for_invoice(self, original_invoice_id: str) -> list[SalesInvoice]: ...


class PaymentRepository(ABC):
    """Port de persistance des encaissements et lettrages."""

    @abstractmethod
    def get(self, payment_id: str) -> Payment | None: ...

    @abstractmethod
    def get_by_method_and_reference(self, method: PaymentMethod, reference: str) -> Payment | None: ...

    @abstractmethod
    def save(self, payment: Payment) -> None: ...

    @abstractmethod
    def list_by_customer(self, customer_id: str) -> list[Payment]: ...

    @abstractmethod
    def list_by_invoice(self, invoice_id: str) -> list[Payment]: ...


class ReminderRepository(ABC):
    """Port de persistance des relances d'impayes."""

    @abstractmethod
    def get_by_invoice_and_level(self, invoice_id: str, level_code: str) -> Reminder | None: ...

    @abstractmethod
    def save(self, reminder: Reminder) -> None: ...

    @abstractmethod
    def list_by_invoice(self, invoice_id: str) -> list[Reminder]: ...


class NumberSequence(ABC):
    """Port de numerotation continue sans trou par type de document et exercice."""

    @abstractmethod
    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        """Alloue le prochain numero dans la transaction courante."""
        ...
