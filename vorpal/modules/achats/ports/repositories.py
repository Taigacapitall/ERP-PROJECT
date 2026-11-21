"""Contrats (ABC) des repositories du module Achats."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vorpal.modules.achats.domain.landed_costs import LandedCostDocument
from vorpal.modules.achats.domain.purchase_orders import PurchaseOrder
from vorpal.modules.achats.domain.receipts import GoodsReceipt
from vorpal.modules.achats.domain.suppliers import Supplier


class SupplierRepository(ABC):
    """Port de persistance des fournisseurs."""

    @abstractmethod
    def get(self, supplier_id: str) -> Supplier | None:
        """Retourne un fournisseur par identifiant ou None."""

    @abstractmethod
    def add(self, supplier: Supplier) -> None:
        """Ajoute ou met a jour un fournisseur."""

    @abstractmethod
    def list_all(self) -> list[Supplier]:
        """Liste tous les fournisseurs."""


class PurchaseOrderRepository(ABC):
    """Port de persistance des bons de commande."""

    @abstractmethod
    def get(self, order_id: str) -> PurchaseOrder | None:
        """Retourne un bon de commande par identifiant ou None."""

    @abstractmethod
    def get_by_number(self, number: str) -> PurchaseOrder | None:
        """Retourne un bon de commande par numero ou None."""

    @abstractmethod
    def add(self, order: PurchaseOrder) -> None:
        """Ajoute ou met a jour un bon de commande."""

    @abstractmethod
    def list_all(self) -> list[PurchaseOrder]:
        """Liste tous les bons de commande."""


class GoodsReceiptRepository(ABC):
    """Port de persistance des bons de reception."""

    @abstractmethod
    def get(self, receipt_id: str) -> GoodsReceipt | None:
        """Retourne un bon de reception par identifiant ou None."""

    @abstractmethod
    def get_by_number(self, number: str) -> GoodsReceipt | None:
        """Retourne un bon de reception par numero ou None."""

    @abstractmethod
    def add(self, receipt: GoodsReceipt) -> None:
        """Ajoute ou met a jour un bon de reception."""

    @abstractmethod
    def list_by_order(self, purchase_order_id: str) -> list[GoodsReceipt]:
        """Liste les bons de reception d'un bon de commande."""

    @abstractmethod
    def list_all(self) -> list[GoodsReceipt]:
        """Liste tous les bons de reception."""


class LandedCostRepository(ABC):
    """Port de persistance des documents de frais d'approche."""

    @abstractmethod
    def get(self, landed_cost_id: str) -> LandedCostDocument | None:
        """Retourne un document de frais d'approche par identifiant ou None."""

    @abstractmethod
    def add(self, document: LandedCostDocument) -> None:
        """Ajoute ou met a jour un document de frais d'approche."""

    @abstractmethod
    def list_by_receipt(self, goods_receipt_id: str) -> list[LandedCostDocument]:
        """Liste les documents de frais d'approche d'une reception."""

    @abstractmethod
    def list_all(self) -> list[LandedCostDocument]:
        """Liste tous les documents de frais d'approche."""


class PurchaseNumberSequence(ABC):
    """Port de generation de numeros sequentiels continus (sans trou) par exercice."""

    @abstractmethod
    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        """Attribue et retourne le prochain numero pour la sequence et l'exercice donnes."""
