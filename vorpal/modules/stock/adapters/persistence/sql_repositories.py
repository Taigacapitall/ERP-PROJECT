"""Implementations SQLAlchemy des repositories du module Stock."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vorpal.core.money import Currency
from vorpal.modules.stock.adapters.persistence.mappers import (
    inventory_from_models,
    inventory_to_models,
    item_from_model,
    item_to_model,
    level_from_model,
    level_to_model,
    movement_from_model,
    movement_to_model,
    warehouse_from_model,
    warehouse_to_model,
)
from vorpal.modules.stock.adapters.persistence.models import (
    StockInventoryLineModel,
    StockInventoryModel,
    StockItemModel,
    StockLevelModel,
    StockMovementModel,
    StockWarehouseModel,
)
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
    WarehouseRepository,
)

_LOCK_ATTEMPTS = 5


class SqlStockItemRepository(StockItemRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, code: str) -> StockItem | None:
        model = self._session.get(StockItemModel, code)
        return item_from_model(model) if model is not None else None

    def add(self, item: StockItem) -> None:
        self._session.merge(item_to_model(item))

    def list_all(self) -> list[StockItem]:
        rows = self._session.scalars(select(StockItemModel).order_by(StockItemModel.code)).all()
        return [item_from_model(row) for row in rows]


class SqlWarehouseRepository(WarehouseRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, code: str) -> Warehouse | None:
        model = self._session.get(StockWarehouseModel, code)
        return warehouse_from_model(model) if model is not None else None

    def add(self, warehouse: Warehouse) -> None:
        self._session.merge(warehouse_to_model(warehouse))

    def list_all(self) -> list[Warehouse]:
        rows = self._session.scalars(
            select(StockWarehouseModel).order_by(StockWarehouseModel.code)
        ).all()
        return [warehouse_from_model(row) for row in rows]


class SqlStockLevelRepository(StockLevelRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, item_code: str, warehouse_code: str) -> StockLevel | None:
        model = self._session.get(StockLevelModel, (item_code, warehouse_code))
        return level_from_model(model) if model is not None else None

    def lock_and_get(self, item_code: str, warehouse_code: str, currency: Currency) -> StockLevel:
        """Verrouille le solde par un UPDATE atomique (verrou de ligne tenu jusqu'au commit).

        Meme principe que la numerotation de Ventes : aucune lecture avant le verrou, donc deux
        transactions concurrentes sur le meme solde s'executent l'une apres l'autre. Premiere
        ecriture d'un solde : insertion sous SAVEPOINT, nouvel essai si une autre transaction
        a cree la ligne entre-temps.
        """
        same_level = (
            StockLevelModel.item_code == item_code,
            StockLevelModel.warehouse_code == warehouse_code,
        )
        for _ in range(_LOCK_ATTEMPTS):
            bumped = self._session.execute(
                update(StockLevelModel)
                .where(*same_level)
                .values(lock_version=StockLevelModel.lock_version + 1)
                .returning(StockLevelModel.lock_version)
            ).scalar_one_or_none()
            if bumped is not None:
                model = self._session.get(
                    StockLevelModel, (item_code, warehouse_code), populate_existing=True
                )
                if model is None:
                    raise StockConflictError("Solde de stock disparu pendant le verrouillage.")
                return level_from_model(model)
            try:
                with self._session.begin_nested():
                    self._session.add(
                        level_to_model(
                            StockLevel(item_code, warehouse_code, currency), lock_version=1
                        )
                    )
                    self._session.flush()
            except IntegrityError:
                continue
            return StockLevel(item_code, warehouse_code, currency)
        raise StockConflictError("Impossible de verrouiller le solde : trop sollicite, reessayez.")

    def save(self, level: StockLevel) -> None:
        model = self._session.get(StockLevelModel, (level.item_code, level.warehouse_code))
        if model is None:
            self._session.add(level_to_model(level))
            return
        model.quantity = level.quantity
        model.value = level.value

    def list_by_item(self, item_code: str) -> list[StockLevel]:
        rows = self._session.scalars(
            select(StockLevelModel)
            .where(StockLevelModel.item_code == item_code)
            .order_by(StockLevelModel.warehouse_code)
        ).all()
        return [level_from_model(row) for row in rows]

    def list_by_warehouse(self, warehouse_code: str) -> list[StockLevel]:
        rows = self._session.scalars(
            select(StockLevelModel)
            .where(StockLevelModel.warehouse_code == warehouse_code)
            .order_by(StockLevelModel.item_code)
        ).all()
        return [level_from_model(row) for row in rows]

    def list_all(self) -> list[StockLevel]:
        rows = self._session.scalars(
            select(StockLevelModel).order_by(
                StockLevelModel.item_code, StockLevelModel.warehouse_code
            )
        ).all()
        return [level_from_model(row) for row in rows]


class SqlStockMovementRepository(StockMovementRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, movement: StockMovement) -> None:
        try:
            with self._session.begin_nested():
                self._session.add(movement_to_model(movement))
                self._session.flush()
        except IntegrityError as exc:
            raise StockConflictError(
                "Un mouvement identique (meme source ou meme numero) existe deja."
            ) from exc

    def get(self, movement_id: str) -> StockMovement | None:
        model = self._session.get(StockMovementModel, movement_id)
        return movement_from_model(model) if model is not None else None

    def find_by_key(
        self,
        source_type: str,
        source_ref: str,
        source_line_ref: str,
        movement_type: MovementType,
    ) -> StockMovement | None:
        model = self._session.scalar(
            select(StockMovementModel).where(
                StockMovementModel.source_type == source_type,
                StockMovementModel.source_ref == source_ref,
                StockMovementModel.source_line_ref == source_line_ref,
                StockMovementModel.movement_type == movement_type.value,
            )
        )
        return movement_from_model(model) if model is not None else None

    def list_by_item(
        self, item_code: str, warehouse_code: str | None = None
    ) -> list[StockMovement]:
        query = select(StockMovementModel).where(StockMovementModel.item_code == item_code)
        if warehouse_code is not None:
            query = query.where(StockMovementModel.warehouse_code == warehouse_code)
        rows = self._session.scalars(
            query.order_by(StockMovementModel.movement_date, StockMovementModel.number)
        ).all()
        return [movement_from_model(row) for row in rows]

    def list_by_source(self, source_type: str, source_ref: str) -> list[StockMovement]:
        rows = self._session.scalars(
            select(StockMovementModel)
            .where(
                StockMovementModel.source_type == source_type,
                StockMovementModel.source_ref == source_ref,
            )
            .order_by(StockMovementModel.number)
        ).all()
        return [movement_from_model(row) for row in rows]


class SqlInventoryRepository(InventoryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _load(self, header: StockInventoryModel) -> InventoryCount:
        lines = list(
            self._session.scalars(
                select(StockInventoryLineModel).where(
                    StockInventoryLineModel.count_id == header.id
                )
            ).all()
        )
        return inventory_from_models(header, lines)

    def get(self, count_id: str) -> InventoryCount | None:
        header = self._session.get(StockInventoryModel, count_id)
        return self._load(header) if header is not None else None

    def add(self, count: InventoryCount) -> None:
        header, lines = inventory_to_models(count)
        self._session.merge(header)
        existing = self._session.scalars(
            select(StockInventoryLineModel).where(StockInventoryLineModel.count_id == count.id)
        ).all()
        for row in existing:
            self._session.delete(row)
        self._session.flush()
        for line in lines:
            self._session.merge(line)

    def list_all(self) -> list[InventoryCount]:
        headers = self._session.scalars(
            select(StockInventoryModel).order_by(StockInventoryModel.id)
        ).all()
        return [self._load(h) for h in headers]


__all__ = [
    "SqlInventoryRepository",
    "SqlStockItemRepository",
    "SqlStockLevelRepository",
    "SqlStockMovementRepository",
    "SqlWarehouseRepository",
]
