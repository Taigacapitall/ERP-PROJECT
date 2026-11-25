"""Contrats (ABC) des repositories du module Stock."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vorpal.core.money import Currency
from vorpal.modules.stock.domain.inventory import InventoryCount
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.levels import StockLevel
from vorpal.modules.stock.domain.movements import MovementType, StockMovement
from vorpal.modules.stock.domain.warehouses import Warehouse


class StockItemRepository(ABC):
    """Port de persistance des articles."""

    @abstractmethod
    def get(self, code: str) -> StockItem | None:
        """Retourne un article par code ou None."""

    @abstractmethod
    def add(self, item: StockItem) -> None:
        """Ajoute ou met a jour un article."""

    @abstractmethod
    def list_all(self) -> list[StockItem]:
        """Liste tous les articles."""


class WarehouseRepository(ABC):
    """Port de persistance des entrepots."""

    @abstractmethod
    def get(self, code: str) -> Warehouse | None:
        """Retourne un entrepot par code ou None."""

    @abstractmethod
    def add(self, warehouse: Warehouse) -> None:
        """Ajoute ou met a jour un entrepot."""

    @abstractmethod
    def list_all(self) -> list[Warehouse]:
        """Liste tous les entrepots."""


class StockLevelRepository(ABC):
    """Port de persistance des soldes (article x entrepot)."""

    @abstractmethod
    def get(self, item_code: str, warehouse_code: str) -> StockLevel | None:
        """Lecture simple d'un solde, sans verrou."""

    @abstractmethod
    def lock_and_get(self, item_code: str, warehouse_code: str, currency: Currency) -> StockLevel:
        """Verrouille le solde jusqu'a la fin de la transaction et le retourne (zero s'il n'existe pas).

        Toute modification d'un solde doit passer par cette methode : c'est elle qui serialise les
        mouvements concurrents sur un meme article et un meme entrepot.
        """

    @abstractmethod
    def save(self, level: StockLevel) -> None:
        """Enregistre le solde modifie (apres `lock_and_get`)."""

    @abstractmethod
    def list_by_item(self, item_code: str) -> list[StockLevel]:
        """Liste les soldes d'un article."""

    @abstractmethod
    def list_by_warehouse(self, warehouse_code: str) -> list[StockLevel]:
        """Liste les soldes d'un entrepot."""

    @abstractmethod
    def list_all(self) -> list[StockLevel]:
        """Liste tous les soldes."""


class StockMovementRepository(ABC):
    """Port de persistance du journal des mouvements (append-only)."""

    @abstractmethod
    def add(self, movement: StockMovement) -> None:
        """Ajoute un mouvement ; leve StockConflictError si la cle d'idempotence existe deja."""

    @abstractmethod
    def get(self, movement_id: str) -> StockMovement | None:
        """Retourne un mouvement par identifiant ou None."""

    @abstractmethod
    def find_by_key(
        self,
        source_type: str,
        source_ref: str,
        source_line_ref: str,
        movement_type: MovementType,
    ) -> StockMovement | None:
        """Retourne le mouvement portant cette cle d'idempotence ou None."""

    @abstractmethod
    def list_by_item(self, item_code: str, warehouse_code: str | None = None) -> list[StockMovement]:
        """Liste les mouvements d'un article (carte de stock), tries par date puis numero."""

    @abstractmethod
    def list_by_source(self, source_type: str, source_ref: str) -> list[StockMovement]:
        """Liste les mouvements issus d'une meme source."""


class InventoryRepository(ABC):
    """Port de persistance des fiches d'inventaire."""

    @abstractmethod
    def get(self, count_id: str) -> InventoryCount | None:
        """Retourne une fiche par identifiant ou None."""

    @abstractmethod
    def add(self, count: InventoryCount) -> None:
        """Ajoute ou met a jour une fiche (lignes comprises)."""

    @abstractmethod
    def list_all(self) -> list[InventoryCount]:
        """Liste toutes les fiches."""


class StockNumberSequence(ABC):
    """Port de generation de numeros sequentiels continus (sans trou) par exercice."""

    @abstractmethod
    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        """Attribue et retourne le prochain numero brut pour la sequence et l'exercice."""
