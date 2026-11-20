"""Modeles ORM SQLAlchemy 2.0 du module Ventes (tables prefixees ventes_)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vorpal.core import Base
from vorpal.modules.ventes.adapters.persistence.types import DecimalText


def encode_typed_counters(counters: dict[str, str | int | Decimal]) -> str:
    """Encode un dictionnaire de compteurs avec etiquette de type explicite (str, int, Decimal)."""
    encoded: dict[str, dict[str, str]] = {}
    for key in sorted(counters.keys()):
        val = counters[key]
        if isinstance(val, bool):
            raise ValueError("Les booleens ne sont pas autorises dans les compteurs fiscaux.")  # noqa: TRY004
        if isinstance(val, int):
            encoded[key] = {"type": "int", "value": str(val)}
        elif isinstance(val, Decimal):
            if not val.is_finite():
                raise ValueError("Valeur Decimal non finie dans les compteurs fiscaux.")
            encoded[key] = {"type": "decimal", "value": str(val)}
        elif isinstance(val, str):
            encoded[key] = {"type": "str", "value": val}
        else:
            raise TypeError(f"Type de compteur non supporte : {type(val).__name__}")
    return json.dumps(encoded, sort_keys=True)


def decode_typed_counters(raw_json: str | None) -> dict[str, str | int | Decimal]:
    """Decode un JSON de compteurs en restaurant les types str, int et Decimal exacts."""
    if not raw_json or not raw_json.strip():
        return {}
    raw_obj = json.loads(raw_json)
    if not isinstance(raw_obj, dict):
        return {}
    result: dict[str, str | int | Decimal] = {}
    for key, entry in raw_obj.items():
        if isinstance(entry, dict) and "type" in entry and "value" in entry:
            kind = entry["type"]
            val_str = str(entry["value"])
            if kind == "int":
                result[str(key)] = int(val_str)
            elif kind == "decimal":
                result[str(key)] = Decimal(val_str)
            else:
                result[str(key)] = val_str
        else:
            result[str(key)] = str(entry)
    return result


class CustomerModel(Base):
    """Table des clients commerciaux."""

    __tablename__ = "ventes_customers"
    __table_args__ = (
        Index("ix_ventes_customers_is_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    nif: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rccm: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    contacts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    payment_terms_label: Mapped[str] = mapped_column(String(128), nullable=False, default="Net 30 jours")
    preferred_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SalesItemModel(Base):
    """Table des articles du catalogue de ventes."""

    __tablename__ = "ventes_items"
    __table_args__ = (
        Index("ix_ventes_items_is_active", "is_active"),
    )

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_group: Mapped[str] = mapped_column(String(16), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    is_out_of_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PriceListModel(Base):
    """Table des listes de prix par devise."""

    __tablename__ = "ventes_price_lists"
    __table_args__ = (
        Index("ix_ventes_price_lists_currency_active", "currency", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PriceListItemModel(Base):
    """Table des prix unitaires HT par liste de prix et article."""

    __tablename__ = "ventes_price_list_items"
    __table_args__ = (
        UniqueConstraint("price_list_id", "item_code", name="uq_ventes_price_list_item"),
        Index("ix_ventes_price_list_items_list_id", "price_list_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_list_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_price_lists.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_price_ht: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)


class QuoteModel(Base):
    """Table des devis clients."""

    __tablename__ = "ventes_quotes"
    __table_args__ = (
        UniqueConstraint("number", name="uq_ventes_quotes_number"),
        Index("ix_ventes_quotes_customer_id", "customer_id"),
        Index("ix_ventes_quotes_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_customers.id"),
        nullable=False,
    )
    quote_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    converted_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class QuoteLineModel(Base):
    """Table des lignes de devis."""

    __tablename__ = "ventes_quote_lines"
    __table_args__ = (
        Index("ix_ventes_quote_lines_quote_id", "quote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_quotes.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_order: Mapped[int] = mapped_column(Integer, nullable=False)
    item_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_price_ht: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    tax_group: Mapped[str] = mapped_column(String(16), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    is_out_of_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SalesOrderModel(Base):
    """Table des commandes clients."""

    __tablename__ = "ventes_orders"
    __table_args__ = (
        UniqueConstraint("number", name="uq_ventes_orders_number"),
        Index("ix_ventes_orders_customer_id", "customer_id"),
        Index("ix_ventes_orders_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_customers.id"),
        nullable=False,
    )
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deposit_required_amount: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    deposit_required_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)


class OrderLineModel(Base):
    """Table des lignes de commande client."""

    __tablename__ = "ventes_order_lines"
    __table_args__ = (
        Index("ix_ventes_order_lines_order_id", "order_id"),
    )

    line_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_order: Mapped[int] = mapped_column(Integer, nullable=False)
    item_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_price_ht: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    tax_group: Mapped[str] = mapped_column(String(16), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    is_out_of_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invoiced_quantity: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)


class SalesInvoiceModel(Base):
    """Table des factures de vente et avoirs."""

    __tablename__ = "ventes_invoices"
    __table_args__ = (
        UniqueConstraint("number", name="uq_ventes_invoices_number"),
        Index("ix_ventes_invoices_customer_id", "customer_id"),
        Index("ix_ventes_invoices_status", "status"),
        Index("ix_ventes_invoices_original_invoice_id", "original_invoice_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_customers.id"),
        nullable=False,
    )
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    invoice_type: Mapped[str] = mapped_column(String(16), nullable=False)
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_invoice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_ht: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    total_vat: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    total_ttc: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    paid_amount: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    stamp_device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stamp_auth_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stamp_qr_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    stamp_certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stamp_counters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class InvoiceLineModel(Base):
    """Table des lignes de facture et d'avoir."""

    __tablename__ = "ventes_invoice_lines"
    __table_args__ = (
        Index("ix_ventes_invoice_lines_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_order: Mapped[int] = mapped_column(Integer, nullable=False)
    item_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_price_ht: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    tax_group: Mapped[str] = mapped_column(String(16), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    is_out_of_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_line_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PaymentModel(Base):
    """Table des encaissements clients."""

    __tablename__ = "ventes_payments"
    __table_args__ = (
        UniqueConstraint("method", "reference", name="uq_ventes_payments_method_ref"),
        Index("ix_ventes_payments_customer_id", "customer_id"),
        Index("ix_ventes_payments_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_customers.id"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class PaymentAllocationModel(Base):
    """Table des allocations de lettrage entre encaissement et facture."""

    __tablename__ = "ventes_payment_allocations"
    __table_args__ = (
        Index("ix_ventes_allocations_payment_id", "payment_id"),
        Index("ix_ventes_allocations_invoice_id", "invoice_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payment_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_invoices.id"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    allocated_at: Mapped[date] = mapped_column(Date, nullable=False)


class ReminderModel(Base):
    """Table des relances d'impayes."""

    __tablename__ = "ventes_reminders"
    __table_args__ = (
        UniqueConstraint("invoice_id", "level_code", name="uq_ventes_reminders_inv_level"),
        Index("ix_ventes_reminders_customer_id", "customer_id"),
        Index("ix_ventes_reminders_invoice_id", "invoice_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_invoices.id"),
        nullable=False,
    )
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("ventes_customers.id"),
        nullable=False,
    )
    level_code: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    days_overdue: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_due: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[date] = mapped_column(Date, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class NumberSequenceModel(Base):
    """Table des sequences continues sans trou par type de document et exercice."""

    __tablename__ = "ventes_number_sequences"
    __table_args__ = (
        UniqueConstraint("sequence_key", "fiscal_year", name="uq_ventes_seq_key_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequence_key: Mapped[str] = mapped_column(String(32), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
