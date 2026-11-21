"""Modeles ORM SQLAlchemy du module Achats (prefixe achats_)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from vorpal.core.db import Base
from vorpal.core.money import Currency
from vorpal.modules.achats.adapters.persistence.types import DecimalText
from vorpal.modules.achats.domain.purchase_orders import PurchaseOrderStatus
from vorpal.modules.achats.domain.receipts import GoodsReceiptStatus
from vorpal.modules.achats.settings import LandedCostAllocationMethod


class SupplierModel(Base):
    """Table des fournisseurs."""

    __tablename__ = "achats_suppliers"
    __table_args__ = (Index("ix_achats_suppliers_active", "is_active"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    nif: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rccm: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    preferred_currency: Mapped[Currency] = mapped_column(
        String(8), nullable=False, default=Currency.USD
    )
    contacts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PurchaseOrderModel(Base):
    """Table des bons de commande fournisseurs."""

    __tablename__ = "achats_purchase_orders"
    __table_args__ = (
        UniqueConstraint("number", name="uq_achats_po_number"),
        Index("ix_achats_po_supplier", "supplier_id"),
        Index("ix_achats_po_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supplier_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("achats_suppliers.id"),
        nullable=False,
    )
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[Currency] = mapped_column(String(8), nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        String(32),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PurchaseOrderLineModel(Base):
    """Table des lignes de bon de commande."""

    __tablename__ = "achats_purchase_order_lines"
    __table_args__ = (Index("ix_achats_po_lines_order", "purchase_order_id"),)

    line_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purchase_order_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("achats_purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity_ordered: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_price_ht_amount: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_price_ht_currency: Mapped[Currency] = mapped_column(String(8), nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    discount_rate: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)


class GoodsReceiptModel(Base):
    """Table des bons de reception."""

    __tablename__ = "achats_goods_receipts"
    __table_args__ = (
        UniqueConstraint("number", name="uq_achats_gr_number"),
        Index("ix_achats_gr_order", "purchase_order_id"),
        Index("ix_achats_gr_supplier", "supplier_id"),
        Index("ix_achats_gr_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purchase_order_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("achats_purchase_orders.id"),
        nullable=False,
    )
    supplier_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("achats_suppliers.id"),
        nullable=False,
    )
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[Currency] = mapped_column(String(8), nullable=False)
    warehouse_code: Mapped[str] = mapped_column(String(64), nullable=False, default="MAIN")
    status: Mapped[GoodsReceiptStatus] = mapped_column(
        String(32),
        nullable=False,
        default=GoodsReceiptStatus.DRAFT,
    )


class GoodsReceiptLineModel(Base):
    """Table des lignes de bon de reception."""

    __tablename__ = "achats_goods_receipt_lines"
    __table_args__ = (Index("ix_achats_gr_lines_receipt", "goods_receipt_id"),)

    line_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goods_receipt_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("achats_goods_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_line_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_cost_ht_amount: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    unit_cost_ht_currency: Mapped[Currency] = mapped_column(String(8), nullable=False)
    landed_cost_allocated_amount: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    landed_cost_allocated_currency: Mapped[Currency] = mapped_column(String(8), nullable=False)


class LandedCostModel(Base):
    """Table des documents de frais d'approche."""

    __tablename__ = "achats_landed_costs"
    __table_args__ = (Index("ix_achats_lc_receipt", "goods_receipt_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goods_receipt_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("achats_goods_receipts.id"),
        nullable=False,
    )
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[Currency] = mapped_column(String(8), nullable=False)
    method: Mapped[LandedCostAllocationMethod] = mapped_column(String(32), nullable=False)
    components_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    allocations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class PurchaseSequenceModel(Base):
    """Table des compteurs sequentiels sans trou par cle et exercice."""

    __tablename__ = "achats_sequences"

    sequence_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
