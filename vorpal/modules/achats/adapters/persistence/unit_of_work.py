"""Unit of Work SQLAlchemy pour le module Achats."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from vorpal.modules.achats.adapters.persistence.numbering import SqlPurchaseNumberSequence
from vorpal.modules.achats.adapters.persistence.sql_repositories import (
    SqlGoodsReceiptRepository,
    SqlLandedCostRepository,
    SqlPurchaseOrderRepository,
    SqlSupplierRepository,
)
from vorpal.modules.achats.ports.unit_of_work import PurchaseUnitOfWork


class SqlPurchaseUnitOfWork(PurchaseUnitOfWork):
    """Gere une transaction SQLAlchemy pour les operations du module Achats."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> Self:
        session = self._session_factory()
        self._session = session
        self.suppliers = SqlSupplierRepository(session)
        self.purchase_orders = SqlPurchaseOrderRepository(session)
        self.goods_receipts = SqlGoodsReceiptRepository(session)
        self.landed_costs = SqlLandedCostRepository(session)
        self.sequences = SqlPurchaseNumberSequence(session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                self.rollback()
            else:
                self.commit()
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None

    def commit(self) -> None:
        if self._session is not None:
            self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
