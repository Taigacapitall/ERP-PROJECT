"""Implementations SQLAlchemy 2.0 des repositories du module Ventes."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from vorpal.core import Currency
from vorpal.modules.ventes.adapters.persistence.mappers import (
    customer_from_model,
    customer_to_model,
    invoice_from_model,
    invoice_to_model,
    item_from_model,
    item_to_model,
    order_from_model,
    order_to_model,
    payment_from_model,
    payment_to_model,
    price_list_from_model,
    price_list_to_model,
    quote_from_model,
    quote_to_model,
    reminder_from_model,
    reminder_to_model,
)
from vorpal.modules.ventes.adapters.persistence.models import (
    CustomerModel,
    InvoiceLineModel,
    OrderLineModel,
    PaymentAllocationModel,
    PaymentModel,
    PriceListItemModel,
    PriceListModel,
    QuoteLineModel,
    QuoteModel,
    ReminderModel,
    SalesInvoiceModel,
    SalesItemModel,
    SalesOrderModel,
)
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


class SqlCustomerRepository(CustomerRepository):
    """Repository SQLAlchemy des clients."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, customer_id: str) -> Customer | None:
        model = self._session.get(CustomerModel, customer_id)
        return customer_from_model(model) if model is not None else None

    def save(self, customer: Customer) -> None:
        self._session.merge(customer_to_model(customer))
        self._session.flush()

    def list_all(self, active_only: bool = False) -> list[Customer]:
        stmt = select(CustomerModel)
        if active_only:
            stmt = stmt.where(CustomerModel.is_active.is_(True))
        stmt = stmt.order_by(CustomerModel.id.asc())
        return [customer_from_model(row) for row in self._session.scalars(stmt).all()]


class SqlCatalogRepository(CatalogRepository):
    """Repository SQLAlchemy des articles et listes de prix."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_item(self, code: str) -> SalesItem | None:
        model = self._session.get(SalesItemModel, code)
        return item_from_model(model) if model is not None else None

    def save_item(self, item: SalesItem) -> None:
        self._session.merge(item_to_model(item))
        self._session.flush()

    def list_items(self, active_only: bool = False) -> list[SalesItem]:
        stmt = select(SalesItemModel)
        if active_only:
            stmt = stmt.where(SalesItemModel.is_active.is_(True))
        stmt = stmt.order_by(SalesItemModel.code.asc())
        return [item_from_model(row) for row in self._session.scalars(stmt).all()]

    def get_price_list(self, price_list_id: str) -> PriceList | None:
        header = self._session.get(PriceListModel, price_list_id)
        if header is None:
            return None
        stmt = select(PriceListItemModel).where(PriceListItemModel.price_list_id == price_list_id)
        item_models = list(self._session.scalars(stmt).all())
        return price_list_from_model(header, item_models)

    def save_price_list(self, price_list: PriceList) -> None:
        header, item_models = price_list_to_model(price_list)
        self._session.merge(header)
        self._session.execute(
            delete(PriceListItemModel).where(PriceListItemModel.price_list_id == price_list.id)
        )
        for item_row in item_models:
            self._session.add(item_row)
        self._session.flush()

    def list_price_lists(self, currency: Currency | None = None, active_only: bool = True) -> list[PriceList]:
        stmt = select(PriceListModel)
        if active_only:
            stmt = stmt.where(PriceListModel.is_active.is_(True))
        if currency is not None:
            stmt = stmt.where(PriceListModel.currency == currency.value)
        stmt = stmt.order_by(PriceListModel.id.asc())
        headers = list(self._session.scalars(stmt).all())
        result: list[PriceList] = []
        for hdr in headers:
            items_stmt = select(PriceListItemModel).where(PriceListItemModel.price_list_id == hdr.id)
            item_rows = list(self._session.scalars(items_stmt).all())
            result.append(price_list_from_model(hdr, item_rows))
        return result


class SqlQuoteRepository(QuoteRepository):
    """Repository SQLAlchemy des devis."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, quote_id: str) -> Quote | None:
        header = self._session.get(QuoteModel, quote_id)
        if header is None:
            return None
        stmt = (
            select(QuoteLineModel)
            .where(QuoteLineModel.quote_id == quote_id)
            .order_by(QuoteLineModel.line_order.asc())
        )
        return quote_from_model(header, list(self._session.scalars(stmt).all()))

    def save(self, quote: Quote) -> None:
        header, line_models = quote_to_model(quote)
        self._session.merge(header)
        self._session.execute(delete(QuoteLineModel).where(QuoteLineModel.quote_id == quote.id))
        for line_row in line_models:
            self._session.add(line_row)
        self._session.flush()

    def list_all(self) -> list[Quote]:
        stmt = select(QuoteModel).order_by(QuoteModel.id.asc())
        quotes: list[Quote] = []
        for hdr in self._session.scalars(stmt).all():
            lines_stmt = (
                select(QuoteLineModel)
                .where(QuoteLineModel.quote_id == hdr.id)
                .order_by(QuoteLineModel.line_order.asc())
            )
            quotes.append(quote_from_model(hdr, list(self._session.scalars(lines_stmt).all())))
        return quotes


class SqlOrderRepository(OrderRepository):
    """Repository SQLAlchemy des commandes clients."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, order_id: str) -> SalesOrder | None:
        header = self._session.get(SalesOrderModel, order_id)
        if header is None:
            return None
        stmt = (
            select(OrderLineModel)
            .where(OrderLineModel.order_id == order_id)
            .order_by(OrderLineModel.line_order.asc())
        )
        return order_from_model(header, list(self._session.scalars(stmt).all()))

    def save(self, order: SalesOrder) -> None:
        header, line_models = order_to_model(order)
        self._session.merge(header)
        self._session.execute(delete(OrderLineModel).where(OrderLineModel.order_id == order.id))
        for line_row in line_models:
            self._session.add(line_row)
        self._session.flush()

    def list_all(self) -> list[SalesOrder]:
        stmt = select(SalesOrderModel).order_by(SalesOrderModel.id.asc())
        orders: list[SalesOrder] = []
        for hdr in self._session.scalars(stmt).all():
            lines_stmt = (
                select(OrderLineModel)
                .where(OrderLineModel.order_id == hdr.id)
                .order_by(OrderLineModel.line_order.asc())
            )
            orders.append(order_from_model(hdr, list(self._session.scalars(lines_stmt).all())))
        return orders


class SqlInvoiceRepository(InvoiceRepository):
    """Repository SQLAlchemy des factures et avoirs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _hydrate(self, header: SalesInvoiceModel) -> SalesInvoice:
        lines_stmt = (
            select(InvoiceLineModel)
            .where(InvoiceLineModel.invoice_id == header.id)
            .order_by(InvoiceLineModel.line_order.asc())
        )
        return invoice_from_model(header, list(self._session.scalars(lines_stmt).all()))

    def get(self, invoice_id: str) -> SalesInvoice | None:
        header = self._session.get(SalesInvoiceModel, invoice_id)
        return self._hydrate(header) if header is not None else None

    def get_by_number(self, number: str) -> SalesInvoice | None:
        stmt = select(SalesInvoiceModel).where(SalesInvoiceModel.number == number)
        header = self._session.scalars(stmt).first()
        return self._hydrate(header) if header is not None else None

    def save(self, invoice: SalesInvoice) -> None:
        header, line_models = invoice_to_model(invoice)
        self._session.merge(header)
        self._session.execute(delete(InvoiceLineModel).where(InvoiceLineModel.invoice_id == invoice.id))
        for line_row in line_models:
            self._session.add(line_row)
        self._session.flush()

    def list_all(self) -> list[SalesInvoice]:
        stmt = select(SalesInvoiceModel).order_by(SalesInvoiceModel.id.asc())
        return [self._hydrate(hdr) for hdr in self._session.scalars(stmt).all()]

    def list_credit_notes_for_invoice(self, original_invoice_id: str) -> list[SalesInvoice]:
        stmt = (
            select(SalesInvoiceModel)
            .where(
                SalesInvoiceModel.invoice_type == "avoir",
                SalesInvoiceModel.original_invoice_id == original_invoice_id,
            )
            .order_by(SalesInvoiceModel.id.asc())
        )
        return [self._hydrate(hdr) for hdr in self._session.scalars(stmt).all()]


class SqlPaymentRepository(PaymentRepository):
    """Repository SQLAlchemy des encaissements et lettrages."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _hydrate(self, header: PaymentModel) -> Payment:
        alloc_stmt = (
            select(PaymentAllocationModel)
            .where(PaymentAllocationModel.payment_id == header.id)
            .order_by(PaymentAllocationModel.id.asc())
        )
        return payment_from_model(header, list(self._session.scalars(alloc_stmt).all()))

    def get(self, payment_id: str) -> Payment | None:
        header = self._session.get(PaymentModel, payment_id)
        return self._hydrate(header) if header is not None else None

    def get_by_method_and_reference(self, method: PaymentMethod, reference: str) -> Payment | None:
        stmt = select(PaymentModel).where(
            PaymentModel.method == method.value,
            PaymentModel.reference == reference,
        )
        header = self._session.scalars(stmt).first()
        return self._hydrate(header) if header is not None else None

    def save(self, payment: Payment) -> None:
        header, alloc_models = payment_to_model(payment)
        self._session.merge(header)
        self._session.execute(
            delete(PaymentAllocationModel).where(PaymentAllocationModel.payment_id == payment.id)
        )
        for alloc_row in alloc_models:
            self._session.add(alloc_row)
        self._session.flush()

    def list_by_customer(self, customer_id: str) -> list[Payment]:
        stmt = (
            select(PaymentModel)
            .where(PaymentModel.customer_id == customer_id)
            .order_by(PaymentModel.id.asc())
        )
        return [self._hydrate(hdr) for hdr in self._session.scalars(stmt).all()]

    def list_by_invoice(self, invoice_id: str) -> list[Payment]:
        pay_ids_stmt = (
            select(PaymentAllocationModel.payment_id)
            .where(PaymentAllocationModel.invoice_id == invoice_id)
            .distinct()
        )
        pay_ids = list(self._session.scalars(pay_ids_stmt).all())
        if not pay_ids:
            return []
        stmt = (
            select(PaymentModel)
            .where(PaymentModel.id.in_(pay_ids))
            .order_by(PaymentModel.id.asc())
        )
        return [self._hydrate(hdr) for hdr in self._session.scalars(stmt).all()]


class SqlReminderRepository(ReminderRepository):
    """Repository SQLAlchemy des relances d'impayes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_invoice_and_level(self, invoice_id: str, level_code: str) -> Reminder | None:
        stmt = select(ReminderModel).where(
            ReminderModel.invoice_id == invoice_id,
            ReminderModel.level_code == level_code,
        )
        model = self._session.scalars(stmt).first()
        return reminder_from_model(model) if model is not None else None

    def save(self, reminder: Reminder) -> None:
        self._session.merge(reminder_to_model(reminder))
        self._session.flush()

    def list_by_invoice(self, invoice_id: str) -> list[Reminder]:
        stmt = (
            select(ReminderModel)
            .where(ReminderModel.invoice_id == invoice_id)
            .order_by(ReminderModel.id.asc())
        )
        return [reminder_from_model(row) for row in self._session.scalars(stmt).all()]
