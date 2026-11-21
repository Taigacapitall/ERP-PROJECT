"""Implementations SQLAlchemy des repositories du module Achats."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vorpal.modules.achats.adapters.persistence.mappers import (
    landed_cost_from_model,
    landed_cost_to_model,
    order_from_model,
    order_to_model,
    receipt_from_model,
    receipt_to_model,
    supplier_from_model,
    supplier_to_model,
)
from vorpal.modules.achats.adapters.persistence.models import (
    GoodsReceiptLineModel,
    GoodsReceiptModel,
    LandedCostModel,
    PurchaseOrderLineModel,
    PurchaseOrderModel,
    SupplierModel,
)
from vorpal.modules.achats.domain.landed_costs import LandedCostDocument
from vorpal.modules.achats.domain.purchase_orders import PurchaseOrder
from vorpal.modules.achats.domain.receipts import GoodsReceipt
from vorpal.modules.achats.domain.suppliers import Supplier
from vorpal.modules.achats.ports.repositories import (
    GoodsReceiptRepository,
    LandedCostRepository,
    PurchaseOrderRepository,
    SupplierRepository,
)


class SqlSupplierRepository(SupplierRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, supplier_id: str) -> Supplier | None:
        model = self._session.get(SupplierModel, supplier_id)
        return supplier_from_model(model) if model is not None else None

    def add(self, supplier: Supplier) -> None:
        self._session.merge(supplier_to_model(supplier))

    def list_all(self) -> list[Supplier]:
        rows = self._session.scalars(select(SupplierModel)).all()
        return [supplier_from_model(row) for row in rows]


class SqlPurchaseOrderRepository(PurchaseOrderRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _load_order(self, header: PurchaseOrderModel) -> PurchaseOrder:
        lines = list(
            self._session.scalars(
                select(PurchaseOrderLineModel).where(
                    PurchaseOrderLineModel.purchase_order_id == header.id
                )
            ).all()
        )
        return order_from_model(header, lines)

    def get(self, order_id: str) -> PurchaseOrder | None:
        header = self._session.get(PurchaseOrderModel, order_id)
        return self._load_order(header) if header is not None else None

    def get_by_number(self, number: str) -> PurchaseOrder | None:
        header = self._session.scalar(
            select(PurchaseOrderModel).where(PurchaseOrderModel.number == number)
        )
        return self._load_order(header) if header is not None else None

    def add(self, order: PurchaseOrder) -> None:
        header, lines = order_to_model(order)
        self._session.merge(header)
        existing_lines = self._session.scalars(
            select(PurchaseOrderLineModel).where(
                PurchaseOrderLineModel.purchase_order_id == order.id
            )
        ).all()
        for existing in existing_lines:
            self._session.delete(existing)
        self._session.flush()
        for line in lines:
            self._session.merge(line)

    def list_all(self) -> list[PurchaseOrder]:
        headers = self._session.scalars(select(PurchaseOrderModel)).all()
        return [self._load_order(h) for h in headers]


class SqlGoodsReceiptRepository(GoodsReceiptRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _load_receipt(self, header: GoodsReceiptModel) -> GoodsReceipt:
        lines = list(
            self._session.scalars(
                select(GoodsReceiptLineModel).where(
                    GoodsReceiptLineModel.goods_receipt_id == header.id
                )
            ).all()
        )
        return receipt_from_model(header, lines)

    def get(self, receipt_id: str) -> GoodsReceipt | None:
        header = self._session.get(GoodsReceiptModel, receipt_id)
        return self._load_receipt(header) if header is not None else None

    def get_by_number(self, number: str) -> GoodsReceipt | None:
        header = self._session.scalar(
            select(GoodsReceiptModel).where(GoodsReceiptModel.number == number)
        )
        return self._load_receipt(header) if header is not None else None

    def add(self, receipt: GoodsReceipt) -> None:
        header, lines = receipt_to_model(receipt)
        self._session.merge(header)
        existing_lines = self._session.scalars(
            select(GoodsReceiptLineModel).where(
                GoodsReceiptLineModel.goods_receipt_id == receipt.id
            )
        ).all()
        for existing in existing_lines:
            self._session.delete(existing)
        self._session.flush()
        for line in lines:
            self._session.merge(line)

    def list_by_order(self, purchase_order_id: str) -> list[GoodsReceipt]:
        headers = self._session.scalars(
            select(GoodsReceiptModel).where(
                GoodsReceiptModel.purchase_order_id == purchase_order_id
            )
        ).all()
        return [self._load_receipt(h) for h in headers]

    def list_all(self) -> list[GoodsReceipt]:
        headers = self._session.scalars(select(GoodsReceiptModel)).all()
        return [self._load_receipt(h) for h in headers]


class SqlLandedCostRepository(LandedCostRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, landed_cost_id: str) -> LandedCostDocument | None:
        model = self._session.get(LandedCostModel, landed_cost_id)
        return landed_cost_from_model(model) if model is not None else None

    def add(self, document: LandedCostDocument) -> None:
        self._session.merge(landed_cost_to_model(document))

    def list_by_receipt(self, goods_receipt_id: str) -> list[LandedCostDocument]:
        rows = self._session.scalars(
            select(LandedCostModel).where(LandedCostModel.goods_receipt_id == goods_receipt_id)
        ).all()
        return [landed_cost_from_model(row) for row in rows]

    def list_all(self) -> list[LandedCostDocument]:
        rows = self._session.scalars(select(LandedCostModel)).all()
        return [landed_cost_from_model(row) for row in rows]
