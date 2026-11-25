"""Service de consultation du stock (lecture seule, permission `stock.view`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from vorpal.core.money import Currency
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.stock.domain.errors import StockNotFoundError
from vorpal.modules.stock.domain.inventory import InventoryCount
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.levels import StockLevel
from vorpal.modules.stock.domain.movements import StockMovement
from vorpal.modules.stock.domain.values import ZERO, normalize_code
from vorpal.modules.stock.domain.warehouses import Warehouse
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import load_item, load_warehouse, require_permission


@dataclass(frozen=True)
class LowStockRow:
    item: StockItem
    quantity: Decimal
    threshold: Decimal


class StockQueryService:
    """Consultations : articles, entrepots, soldes, carte de stock, valorisation, alertes."""

    def __init__(self, uow_factory: Callable[[], StockUnitOfWork], authz: AuthorizationService) -> None:
        self._uow_factory = uow_factory
        self._authz = authz

    def _check(self, actor: User) -> None:
        require_permission(self._authz, actor, "stock.view")

    def get_item(self, actor: User, code: str) -> StockItem:
        self._check(actor)
        with self._uow_factory() as uow:
            return load_item(uow, code)

    def list_items(self, actor: User, include_inactive: bool = False) -> list[StockItem]:
        self._check(actor)
        with self._uow_factory() as uow:
            items = uow.items.list_all()
        return sorted(
            (i for i in items if include_inactive or i.is_active), key=lambda i: i.code
        )

    def get_warehouse(self, actor: User, code: str) -> Warehouse:
        self._check(actor)
        with self._uow_factory() as uow:
            return load_warehouse(uow, code)

    def list_warehouses(self, actor: User, include_inactive: bool = False) -> list[Warehouse]:
        self._check(actor)
        with self._uow_factory() as uow:
            rows = uow.warehouses.list_all()
        return sorted((w for w in rows if include_inactive or w.is_active), key=lambda w: w.code)

    def get_level(self, actor: User, item_code: str, warehouse_code: str) -> StockLevel:
        """Solde d'un article dans un entrepot (zero s'il n'y a jamais eu de mouvement)."""
        self._check(actor)
        with self._uow_factory() as uow:
            item = load_item(uow, item_code)
            warehouse = load_warehouse(uow, warehouse_code)
            level = uow.levels.get(item.code, warehouse.code)
        if level is None:
            return StockLevel(item.code, warehouse.code, item.currency)
        return level

    def item_levels(self, actor: User, item_code: str) -> list[StockLevel]:
        self._check(actor)
        with self._uow_factory() as uow:
            item = load_item(uow, item_code)
            return uow.levels.list_by_item(item.code)

    def warehouse_levels(self, actor: User, warehouse_code: str) -> list[StockLevel]:
        self._check(actor)
        with self._uow_factory() as uow:
            warehouse = load_warehouse(uow, warehouse_code)
            return uow.levels.list_by_warehouse(warehouse.code)

    def on_hand(self, actor: User, item_code: str) -> Decimal:
        """Quantite totale d'un article, tous entrepots confondus."""
        return sum((lv.quantity for lv in self.item_levels(actor, item_code)), ZERO)

    def stock_card(
        self, actor: User, item_code: str, warehouse_code: str | None = None
    ) -> list[StockMovement]:
        """Historique des mouvements d'un article, trie par date puis numero."""
        self._check(actor)
        with self._uow_factory() as uow:
            item = load_item(uow, item_code)
            wh = load_warehouse(uow, warehouse_code).code if warehouse_code is not None else None
            return uow.movements.list_by_item(item.code, wh)

    def valuation(self, actor: User, warehouse_code: str | None = None) -> dict[Currency, Decimal]:
        """Valeur totale du stock, par devise (des devises differentes ne s'additionnent pas)."""
        self._check(actor)
        with self._uow_factory() as uow:
            if warehouse_code is None:
                levels = uow.levels.list_all()
            else:
                levels = uow.levels.list_by_warehouse(load_warehouse(uow, warehouse_code).code)
        totals: dict[Currency, Decimal] = {}
        for level in levels:
            totals[level.currency] = totals.get(level.currency, ZERO) + level.value
        return totals

    def low_stock_items(self, actor: User) -> list[LowStockRow]:
        """Articles actifs dont le total est inferieur ou egal au seuil de reapprovisionnement."""
        self._check(actor)
        rows: list[LowStockRow] = []
        with self._uow_factory() as uow:
            for item in sorted(uow.items.list_all(), key=lambda i: i.code):
                if not item.is_active or item.reorder_threshold is None:
                    continue
                total = sum((lv.quantity for lv in uow.levels.list_by_item(item.code)), ZERO)
                if total <= item.reorder_threshold:
                    rows.append(LowStockRow(item, total, item.reorder_threshold))
        return rows

    def get_count(self, actor: User, count_id: str) -> InventoryCount:
        self._check(actor)
        with self._uow_factory() as uow:
            count = uow.inventories.get(normalize_code(count_id, "L'identifiant de la fiche"))
        if count is None:
            raise StockNotFoundError(f"Fiche d'inventaire '{count_id}' introuvable.")
        return count

    def list_counts(self, actor: User) -> list[InventoryCount]:
        self._check(actor)
        with self._uow_factory() as uow:
            return sorted(uow.inventories.list_all(), key=lambda c: c.id)
