"""Implementations en memoire des ports de persistance du module Stock (tests, prototypes)."""

from __future__ import annotations

from copy import deepcopy

from vorpal.core.money import Currency
from vorpal.modules.stock.domain.errors import StockConflictError
from vorpal.modules.stock.domain.inventory import InventoryCount
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.levels import StockLevel
from vorpal.modules.stock.domain.movements import MovementType, StockMovement
from vorpal.modules.stock.domain.warehouses import Warehouse
from vorpal.modules.stock.ports.repositories import (
    InventoryRepository,
    StockItemRepository,
    StockLevelRepository,
    StockMovementRepository,
    StockNumberSequence,
    WarehouseRepository,
)
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork


class InMemoryStockItemRepository(StockItemRepository):
    def __init__(self) -> None:
        self._items: dict[str, StockItem] = {}

    def get(self, code: str) -> StockItem | None:
        item = self._items.get(code)
        return deepcopy(item) if item is not None else None

    def add(self, item: StockItem) -> None:
        self._items[item.code] = deepcopy(item)

    def list_all(self) -> list[StockItem]:
        return [deepcopy(item) for item in self._items.values()]


class InMemoryWarehouseRepository(WarehouseRepository):
    def __init__(self) -> None:
        self._items: dict[str, Warehouse] = {}

    def get(self, code: str) -> Warehouse | None:
        item = self._items.get(code)
        return deepcopy(item) if item is not None else None

    def add(self, warehouse: Warehouse) -> None:
        self._items[warehouse.code] = deepcopy(warehouse)

    def list_all(self) -> list[Warehouse]:
        return [deepcopy(item) for item in self._items.values()]


class InMemoryStockLevelRepository(StockLevelRepository):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], StockLevel] = {}

    def get(self, item_code: str, warehouse_code: str) -> StockLevel | None:
        level = self._items.get((item_code, warehouse_code))
        return deepcopy(level) if level is not None else None

    def lock_and_get(self, item_code: str, warehouse_code: str, currency: Currency) -> StockLevel:
        existing = self._items.get((item_code, warehouse_code))
        if existing is not None:
            return deepcopy(existing)
        return StockLevel(item_code=item_code, warehouse_code=warehouse_code, currency=currency)

    def save(self, level: StockLevel) -> None:
        self._items[(level.item_code, level.warehouse_code)] = deepcopy(level)

    def list_by_item(self, item_code: str) -> list[StockLevel]:
        return [deepcopy(v) for (code, _), v in sorted(self._items.items()) if code == item_code]

    def list_by_warehouse(self, warehouse_code: str) -> list[StockLevel]:
        return [deepcopy(v) for (_, wh), v in sorted(self._items.items()) if wh == warehouse_code]

    def list_all(self) -> list[StockLevel]:
        return [deepcopy(v) for _, v in sorted(self._items.items())]


class InMemoryStockMovementRepository(StockMovementRepository):
    def __init__(self) -> None:
        self._items: dict[str, StockMovement] = {}

    def add(self, movement: StockMovement) -> None:
        if movement.id in self._items:
            raise StockConflictError(f"Le mouvement '{movement.id}' existe deja.")
        key = movement.idempotency_key
        if any(m.idempotency_key == key for m in self._items.values()):
            raise StockConflictError("Un mouvement identique (meme source) existe deja.")
        self._items[movement.id] = movement

    def get(self, movement_id: str) -> StockMovement | None:
        return self._items.get(movement_id)

    def find_by_key(
        self,
        source_type: str,
        source_ref: str,
        source_line_ref: str,
        movement_type: MovementType,
    ) -> StockMovement | None:
        key = (source_type, source_ref, source_line_ref, movement_type.value)
        for movement in self._items.values():
            if movement.idempotency_key == key:
                return movement
        return None

    def list_by_item(self, item_code: str, warehouse_code: str | None = None) -> list[StockMovement]:
        rows = [
            m
            for m in self._items.values()
            if m.item_code == item_code
            and (warehouse_code is None or m.warehouse_code == warehouse_code)
        ]
        return sorted(rows, key=lambda m: (m.movement_date, m.number))

    def list_by_source(self, source_type: str, source_ref: str) -> list[StockMovement]:
        rows = [
            m
            for m in self._items.values()
            if m.source_type == source_type and m.source_ref == source_ref
        ]
        return sorted(rows, key=lambda m: m.number)


class InMemoryInventoryRepository(InventoryRepository):
    def __init__(self) -> None:
        self._items: dict[str, InventoryCount] = {}

    def get(self, count_id: str) -> InventoryCount | None:
        item = self._items.get(count_id)
        return deepcopy(item) if item is not None else None

    def add(self, count: InventoryCount) -> None:
        self._items[count.id] = deepcopy(count)

    def list_all(self) -> list[InventoryCount]:
        return [deepcopy(item) for item in self._items.values()]


class InMemoryStockNumberSequence(StockNumberSequence):
    def __init__(self) -> None:
        self._counters: dict[tuple[str, int], int] = {}

    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        key = (sequence_key, fiscal_year)
        nxt = self._counters.get(key, 0) + 1
        self._counters[key] = nxt
        return str(nxt)


class InMemoryStockUnitOfWork(StockUnitOfWork):
    """Unit of Work en memoire avec instantanes pour un rollback deterministe."""

    def __init__(self) -> None:
        self._items_repo = InMemoryStockItemRepository()
        self._warehouses_repo = InMemoryWarehouseRepository()
        self._levels_repo = InMemoryStockLevelRepository()
        self._movements_repo = InMemoryStockMovementRepository()
        self._inventories_repo = InMemoryInventoryRepository()
        self._sequences_repo = InMemoryStockNumberSequence()

        self.items = self._items_repo
        self.warehouses = self._warehouses_repo
        self.levels = self._levels_repo
        self.movements = self._movements_repo
        self.inventories = self._inventories_repo
        self.sequences = self._sequences_repo

        self._take_snapshot()

    def _take_snapshot(self) -> None:
        self._snap_items = deepcopy(self._items_repo._items)
        self._snap_warehouses = deepcopy(self._warehouses_repo._items)
        self._snap_levels = deepcopy(self._levels_repo._items)
        self._snap_movements = dict(self._movements_repo._items)
        self._snap_inventories = deepcopy(self._inventories_repo._items)
        self._snap_counters = dict(self._sequences_repo._counters)

    def commit(self) -> None:
        self._take_snapshot()

    def rollback(self) -> None:
        self._items_repo._items = deepcopy(self._snap_items)
        self._warehouses_repo._items = deepcopy(self._snap_warehouses)
        self._levels_repo._items = deepcopy(self._snap_levels)
        self._movements_repo._items = dict(self._snap_movements)
        self._inventories_repo._items = deepcopy(self._snap_inventories)
        self._sequences_repo._counters = dict(self._snap_counters)
