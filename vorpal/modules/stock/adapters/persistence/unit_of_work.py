"""Unit of Work SQLAlchemy pour le module Stock."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from vorpal.modules.stock.adapters.persistence.numbering import SqlStockNumberSequence
from vorpal.modules.stock.adapters.persistence.sql_repositories import (
    SqlInventoryRepository,
    SqlStockItemRepository,
    SqlStockLevelRepository,
    SqlStockMovementRepository,
    SqlWarehouseRepository,
)
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork


class SqlStockUnitOfWork(StockUnitOfWork):
    """Gere une transaction SQLAlchemy pour les operations du module Stock."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> Self:
        session = self._session_factory()
        self._session = session
        self.items = SqlStockItemRepository(session)
        self.warehouses = SqlWarehouseRepository(session)
        self.levels = SqlStockLevelRepository(session)
        self.movements = SqlStockMovementRepository(session)
        self.inventories = SqlInventoryRepository(session)
        self.sequences = SqlStockNumberSequence(session)
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
