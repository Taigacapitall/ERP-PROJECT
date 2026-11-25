"""Modeles ORM SQLAlchemy du module Stock (tables prefixees stock_)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from vorpal.core.db import Base
from vorpal.modules.stock.adapters.persistence.types import DecimalText


class StockItemModel(Base):
    """Table des articles."""

    __tablename__ = "stock_items"
    __table_args__ = (Index("ix_stock_items_active", "is_active"),)

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity_decimals: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reorder_threshold: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StockWarehouseModel(Base):
    """Table des entrepots."""

    __tablename__ = "stock_warehouses"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StockLevelModel(Base):
    """Table des soldes (article x entrepot) ; `lock_version` sert a serialiser les ecritures."""

    __tablename__ = "stock_levels"
    __table_args__ = (Index("ix_stock_levels_warehouse", "warehouse_code"),)

    item_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("stock_items.code"), primary_key=True
    )
    warehouse_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("stock_warehouses.code"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    value: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StockMovementModel(Base):
    """Journal des mouvements (append-only) ; unicite de la source = idempotence."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        UniqueConstraint("number", name="uq_stock_mv_number"),
        UniqueConstraint(
            "source_type",
            "source_ref",
            "source_line_ref",
            "movement_type",
            name="uq_stock_mv_source",
        ),
        Index("ix_stock_mv_item", "item_code", "warehouse_code"),
        Index("ix_stock_mv_date", "movement_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    item_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("stock_items.code"), nullable=False
    )
    warehouse_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("stock_warehouses.code"), nullable=False
    )
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    value_delta: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity_after: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    value_after: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    source_line_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class StockInventoryModel(Base):
    """Table des fiches d'inventaire."""

    __tablename__ = "stock_inventory_counts"
    __table_args__ = (UniqueConstraint("number", name="uq_stock_inv_number"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    warehouse_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("stock_warehouses.code"), nullable=False
    )
    count_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class StockInventoryLineModel(Base):
    """Table des lignes de comptage."""

    __tablename__ = "stock_inventory_lines"

    count_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("stock_inventory_counts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("stock_items.code"), primary_key=True
    )
    theoretical_quantity: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    counted_quantity: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    unit_cost: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)


class StockSequenceModel(Base):
    """Compteurs sequentiels sans trou par cle et exercice."""

    __tablename__ = "stock_sequences"

    sequence_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
