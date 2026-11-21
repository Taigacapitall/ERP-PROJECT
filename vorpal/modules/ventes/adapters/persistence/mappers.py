"""Conversions explicites entre entites domaine Ventes et modeles ORM SQLAlchemy."""

from __future__ import annotations

import json
from typing import Literal

from vorpal.core import Currency, Money, Partner
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
    decode_typed_counters,
    encode_typed_counters,
)
from vorpal.modules.ventes.domain.catalog import PriceList, SalesItem
from vorpal.modules.ventes.domain.customers import ContactPerson, Customer, PaymentTerms
from vorpal.modules.ventes.domain.errors import SalesComplianceError
from vorpal.modules.ventes.domain.invoices import (
    FiscalStamp,
    InvoiceLine,
    InvoiceStatus,
    SalesInvoice,
)
from vorpal.modules.ventes.domain.orders import OrderLine, OrderStatus, SalesOrder
from vorpal.modules.ventes.domain.payments import (
    Allocation,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from vorpal.modules.ventes.domain.quotes import Quote, QuoteLine, QuoteStatus
from vorpal.modules.ventes.domain.reminders import Reminder


def customer_to_model(customer: Customer) -> CustomerModel:
    contacts_payload = [
        {
            "name": c.name,
            "email": c.email,
            "phone": c.phone,
            "role": c.role,
        }
        for c in customer.contacts
    ]
    return CustomerModel(
        id=customer.id,
        name=customer.name,
        nif=customer.nif,
        rccm=customer.rccm,
        address=customer.address,
        contacts_json=json.dumps(contacts_payload, sort_keys=True),
        payment_terms_days=customer.payment_terms.days,
        payment_terms_label=customer.payment_terms.label,
        preferred_currency=customer.preferred_currency.value,
        is_active=customer.is_active,
    )


def customer_from_model(model: CustomerModel) -> Customer:
    raw_contacts = json.loads(model.contacts_json) if model.contacts_json else []
    contacts = tuple(
        ContactPerson(
            name=str(entry["name"]),
            email=entry.get("email"),
            phone=entry.get("phone"),
            role=entry.get("role"),
        )
        for entry in raw_contacts
        if isinstance(entry, dict)
    )
    partner = Partner(
        id=model.id,
        name=model.name,
        partner_type="client",
        nif=model.nif,
        rccm=model.rccm,
        address=model.address,
    )
    return Customer(
        partner=partner,
        contacts=contacts,
        payment_terms=PaymentTerms(days=model.payment_terms_days, label=model.payment_terms_label),
        preferred_currency=Currency(model.preferred_currency),
        is_active=model.is_active,
    )


def item_to_model(item: SalesItem) -> SalesItemModel:
    return SalesItemModel(
        code=item.code,
        designation=item.designation,
        tax_group=item.tax_group,
        vat_rate=item.vat_rate,
        is_out_of_scope=item.is_out_of_scope,
        is_active=item.is_active,
    )


def item_from_model(model: SalesItemModel) -> SalesItem:
    return SalesItem(
        code=model.code,
        designation=model.designation,
        tax_group=model.tax_group,
        vat_rate=model.vat_rate,
        is_out_of_scope=model.is_out_of_scope,
        is_active=model.is_active,
    )


def price_list_to_model(plist: PriceList) -> tuple[PriceListModel, list[PriceListItemModel]]:
    header = PriceListModel(
        id=plist.id,
        name=plist.name,
        currency=plist.currency.value,
        is_active=plist.is_active,
    )
    items = [
        PriceListItemModel(
            price_list_id=plist.id,
            item_code=code,
            unit_price_ht=money.amount,
            currency=money.currency.value,
        )
        for code, money in sorted(plist.prices.items(), key=lambda pair: pair[0])
    ]
    return header, items


def price_list_from_model(header: PriceListModel, item_models: list[PriceListItemModel]) -> PriceList:
    currency = Currency(header.currency)
    prices: dict[str, Money] = {}
    for row in sorted(item_models, key=lambda r: r.item_code):
        prices[row.item_code] = Money(row.unit_price_ht, Currency(row.currency))
    return PriceList(
        id=header.id,
        name=header.name,
        currency=currency,
        prices=prices,
        is_active=header.is_active,
    )


def quote_to_model(quote: Quote) -> tuple[QuoteModel, list[QuoteLineModel]]:
    header = QuoteModel(
        id=quote.id,
        number=quote.number,
        customer_id=quote.customer_id,
        quote_date=quote.quote_date,
        valid_until=quote.valid_until,
        currency=quote.currency.value,
        status=quote.status.value,
        converted_order_id=quote.converted_order_id,
    )
    lines = [
        QuoteLineModel(
            quote_id=quote.id,
            line_order=idx,
            item_code=line.item_code,
            designation=line.designation,
            quantity=line.quantity,
            unit_price_ht=line.unit_price_ht.amount,
            currency=line.unit_price_ht.currency.value,
            discount_percent=line.discount_percent,
            tax_group=line.tax_group,
            vat_rate=line.vat_rate,
            is_out_of_scope=line.is_out_of_scope,
        )
        for idx, line in enumerate(quote.lines)
    ]
    return header, lines


def quote_from_model(header: QuoteModel, line_models: list[QuoteLineModel]) -> Quote:
    ordered = sorted(line_models, key=lambda line: line.line_order)
    lines = tuple(
        QuoteLine(
            item_code=row.item_code,
            designation=row.designation,
            quantity=row.quantity,
            unit_price_ht=Money(row.unit_price_ht, Currency(row.currency)),
            discount_percent=row.discount_percent,
            tax_group=row.tax_group,
            vat_rate=row.vat_rate,
            is_out_of_scope=row.is_out_of_scope,
        )
        for row in ordered
    )
    return Quote(
        id=header.id,
        number=header.number,
        customer_id=header.customer_id,
        quote_date=header.quote_date,
        valid_until=header.valid_until,
        currency=Currency(header.currency),
        lines=lines,
        status=QuoteStatus(header.status),
        converted_order_id=header.converted_order_id,
    )


def order_to_model(order: SalesOrder) -> tuple[SalesOrderModel, list[OrderLineModel]]:
    header = SalesOrderModel(
        id=order.id,
        number=order.number,
        customer_id=order.customer_id,
        order_date=order.order_date,
        currency=order.currency.value,
        status=order.status.value,
        quote_id=order.quote_id,
        deposit_required_amount=order.deposit_required.amount if order.deposit_required else None,
        deposit_required_currency=order.deposit_required.currency.value if order.deposit_required else None,
    )
    lines = [
        OrderLineModel(
            line_id=line.line_id,
            order_id=order.id,
            line_order=idx,
            item_code=line.item_code,
            designation=line.designation,
            quantity=line.quantity,
            unit_price_ht=line.unit_price_ht.amount,
            currency=line.unit_price_ht.currency.value,
            discount_percent=line.discount_percent,
            tax_group=line.tax_group,
            vat_rate=line.vat_rate,
            is_out_of_scope=line.is_out_of_scope,
            invoiced_quantity=line.invoiced_quantity,
        )
        for idx, line in enumerate(order.lines)
    ]
    return header, lines


def order_from_model(header: SalesOrderModel, line_models: list[OrderLineModel]) -> SalesOrder:
    ordered = sorted(line_models, key=lambda line: line.line_order)
    lines = tuple(
        OrderLine(
            line_id=row.line_id,
            item_code=row.item_code,
            designation=row.designation,
            quantity=row.quantity,
            unit_price_ht=Money(row.unit_price_ht, Currency(row.currency)),
            discount_percent=row.discount_percent,
            tax_group=row.tax_group,
            vat_rate=row.vat_rate,
            is_out_of_scope=row.is_out_of_scope,
            invoiced_quantity=row.invoiced_quantity,
        )
        for row in ordered
    )
    deposit = None
    if header.deposit_required_amount is not None and header.deposit_required_currency is not None:
        deposit = Money(header.deposit_required_amount, Currency(header.deposit_required_currency))
    return SalesOrder(
        id=header.id,
        number=header.number,
        customer_id=header.customer_id,
        order_date=header.order_date,
        currency=Currency(header.currency),
        lines=lines,
        status=OrderStatus(header.status),
        quote_id=header.quote_id,
        deposit_required=deposit,
    )


def invoice_to_model(invoice: SalesInvoice) -> tuple[SalesInvoiceModel, list[InvoiceLineModel]]:
    stamp = invoice.fiscal_stamp
    header = SalesInvoiceModel(
        id=invoice.id,
        customer_id=invoice.customer_id,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency.value,
        invoice_type=invoice.invoice_type,
        number=invoice.number,
        status=invoice.status.value,
        order_id=invoice.order_id,
        original_invoice_id=invoice.original_invoice_id,
        original_invoice_number=invoice.original_invoice_number,
        total_ht=invoice.total_ht.amount if invoice.total_ht else None,
        total_vat=invoice.total_vat.amount if invoice.total_vat else None,
        total_ttc=invoice.total_ttc.amount if invoice.total_ttc else None,
        paid_amount=invoice.paid_amount.amount if invoice.paid_amount else None,
        stamp_device_id=stamp.device_id if stamp else None,
        stamp_auth_code=stamp.auth_code if stamp else None,
        stamp_qr_code=stamp.qr_code if stamp else None,
        stamp_certified_at=stamp.certified_at if stamp else None,
        stamp_counters_json=encode_typed_counters(dict(stamp.counters)) if stamp else None,
        rejection_reason=invoice.rejection_reason,
    )
    lines = [
        InvoiceLineModel(
            invoice_id=invoice.id,
            line_order=idx,
            item_code=line.item_code,
            designation=line.designation,
            quantity=line.quantity,
            unit_price_ht=line.unit_price_ht.amount,
            currency=line.unit_price_ht.currency.value,
            discount_percent=line.discount_percent,
            tax_group=line.tax_group,
            vat_rate=line.vat_rate,
            is_out_of_scope=line.is_out_of_scope,
            order_line_id=line.order_line_id,
        )
        for idx, line in enumerate(invoice.lines)
    ]
    return header, lines


def invoice_from_model(header: SalesInvoiceModel, line_models: list[InvoiceLineModel]) -> SalesInvoice:
    currency = Currency(header.currency)
    ordered = sorted(line_models, key=lambda line: line.line_order)
    lines = tuple(
        InvoiceLine(
            item_code=row.item_code,
            designation=row.designation,
            quantity=row.quantity,
            unit_price_ht=Money(row.unit_price_ht, Currency(row.currency)),
            discount_percent=row.discount_percent,
            tax_group=row.tax_group,
            vat_rate=row.vat_rate,
            is_out_of_scope=row.is_out_of_scope,
            order_line_id=row.order_line_id,
        )
        for row in ordered
    )
    stamp: FiscalStamp | None = None
    if (
        header.stamp_device_id is not None
        and header.stamp_auth_code is not None
        and header.stamp_qr_code is not None
        and header.stamp_certified_at is not None
    ):
        stamp = FiscalStamp(
            device_id=header.stamp_device_id,
            auth_code=header.stamp_auth_code,
            qr_code=header.stamp_qr_code,
            certified_at=header.stamp_certified_at,
            counters=decode_typed_counters(header.stamp_counters_json),
        )
    raw_stamp = (
        header.stamp_device_id,
        header.stamp_auth_code,
        header.stamp_qr_code,
        header.stamp_certified_at,
    )
    if header.status == InvoiceStatus.CERTIFIED.value and (
        stamp is None or not (stamp.device_id.strip() and stamp.auth_code.strip() and stamp.qr_code.strip())
    ):
        raise SalesComplianceError(
            f"Donnees incoherentes : la facture '{header.id}' est CERTIFIED sans cachet fiscal complet."
        )
    if stamp is None and any(value is not None for value in raw_stamp):
        raise SalesComplianceError(
            f"Donnees incoherentes : cachet fiscal partiel sur la facture '{header.id}'."
        )
    inv_type: Literal["vente", "avoir"] = "avoir" if header.invoice_type == "avoir" else "vente"
    return SalesInvoice(
        id=header.id,
        customer_id=header.customer_id,
        invoice_date=header.invoice_date,
        due_date=header.due_date,
        currency=currency,
        lines=lines,
        invoice_type=inv_type,
        number=header.number,
        status=InvoiceStatus(header.status),
        order_id=header.order_id,
        original_invoice_id=header.original_invoice_id,
        original_invoice_number=header.original_invoice_number,
        total_ht=Money(header.total_ht, currency) if header.total_ht is not None else None,
        total_vat=Money(header.total_vat, currency) if header.total_vat is not None else None,
        total_ttc=Money(header.total_ttc, currency) if header.total_ttc is not None else None,
        paid_amount=Money(header.paid_amount, currency) if header.paid_amount is not None else None,
        fiscal_stamp=stamp,
        rejection_reason=header.rejection_reason,
    )


def payment_to_model(payment: Payment) -> tuple[PaymentModel, list[PaymentAllocationModel]]:
    header = PaymentModel(
        id=payment.id,
        customer_id=payment.customer_id,
        amount=payment.amount.amount,
        currency=payment.currency.value,
        payment_date=payment.payment_date,
        method=payment.method.value,
        reference=payment.reference,
        order_id=payment.order_id,
        status=payment.status.value,
    )
    allocs = [
        PaymentAllocationModel(
            id=alloc.id,
            payment_id=payment.id,
            invoice_id=alloc.invoice_id,
            amount=alloc.amount.amount,
            currency=alloc.amount.currency.value,
            allocated_at=alloc.allocated_at,
        )
        for alloc in payment.allocations
    ]
    return header, allocs


def payment_from_model(header: PaymentModel, alloc_models: list[PaymentAllocationModel]) -> Payment:
    currency = Currency(header.currency)
    allocations = tuple(
        Allocation(
            id=row.id,
            payment_id=row.payment_id,
            invoice_id=row.invoice_id,
            amount=Money(row.amount, Currency(row.currency)),
            allocated_at=row.allocated_at,
        )
        for row in sorted(alloc_models, key=lambda a: a.id)
    )
    return Payment(
        id=header.id,
        customer_id=header.customer_id,
        amount=Money(header.amount, currency),
        payment_date=header.payment_date,
        method=PaymentMethod(header.method),
        reference=header.reference,
        order_id=header.order_id,
        status=PaymentStatus(header.status),
        allocations=allocations,
    )


def reminder_to_model(reminder: Reminder) -> ReminderModel:
    return ReminderModel(
        id=reminder.id,
        invoice_id=reminder.invoice_id,
        invoice_number=reminder.invoice_number,
        customer_id=reminder.customer_id,
        level_code=reminder.level_code,
        channel=reminder.channel,
        days_overdue=reminder.days_overdue,
        balance_due=reminder.balance_due.amount,
        currency=reminder.balance_due.currency.value,
        message=reminder.message,
        generated_at=reminder.generated_at,
        sent=reminder.sent,
    )


def reminder_from_model(model: ReminderModel) -> Reminder:
    return Reminder(
        id=model.id,
        invoice_id=model.invoice_id,
        invoice_number=model.invoice_number,
        customer_id=model.customer_id,
        level_code=model.level_code,
        channel=model.channel,
        days_overdue=model.days_overdue,
        balance_due=Money(model.balance_due, Currency(model.currency)),
        message=model.message,
        generated_at=model.generated_at,
        sent=model.sent,
    )
