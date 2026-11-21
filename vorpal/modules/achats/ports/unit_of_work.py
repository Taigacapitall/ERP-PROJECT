"""Contrat d'unite de travail (Unit of Work) du module Achats."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from vorpal.modules.achats.ports.repositories import (
    GoodsReceiptRepository,
    LandedCostRepository,
    PurchaseNumberSequence,
    PurchaseOrderRepository,
    SupplierRepository,
)


class PurchaseUnitOfWork(ABC):
    """Coordonne les repositories et la transaction d'un cas d'usage Achats."""

    suppliers: SupplierRepository
    purchase_orders: PurchaseOrderRepository
    goods_receipts: GoodsReceiptRepository
    landed_costs: LandedCostRepository
    sequences: PurchaseNumberSequence

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()

    @abstractmethod
    def commit(self) -> None:
        """Valide la transaction courante."""

    @abstractmethod
    def rollback(self) -> None:
        """Annule la transaction courante."""
