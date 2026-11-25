"""Mappers explicites entre objets du domaine Stock et modeles ORM SQLAlchemy."""

from __future__ import annotations

from datetime import datetime

from vorpal.core.money import Currency
from vorpal.modules.stock.adapters.persistence.models import (
    StockInventoryLineModel,
    StockInventoryModel,
    StockItemModel,
    StockLevelModel,
    StockMovementModel,
    StockWarehouseModel,
)
from vorpal.modules.stock.domain.inventory import InventoryCount, InventoryLine, InventoryStatus
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.levels import StockLevel
from vorpal.modules.stock.domain.movements import MovementType, StockMovement
from vorpal.modules.stock.domain.warehouses import Warehouse


def item_to_model(item: StockItem) -> StockItemModel:
    return StockItemModel(
        code=item.code,
        name=item.name,
        unit=item.unit,
        currency=item.currency.value,
        quantity_decimals=item.quantity_decimals,
        category=item.category,
        reorder_threshold=item.reorder_threshold,
        is_active=item.is_active,
    )


def item_from_model(model: StockItemModel) -> StockItem:
    return StockItem(
        code=model.code,
        name=model.name,
        unit=model.unit,
        currency=Currency(model.currency),
        quantity_decimals=model.quantity_decimals,
        category=model.category,
        reorder_threshold=model.reorder_threshold,
        is_active=model.is_active,
    )


def warehouse_to_model(warehouse: Warehouse) -> StockWarehouseModel:
    return StockWarehouseModel(
        code=warehouse.code, name=warehouse.name, is_active=warehouse.is_active
    )


def warehouse_from_model(model: StockWarehouseModel) -> Warehouse:
    return Warehouse(code=model.code, name=model.name, is_active=model.is_active)


def level_to_model(level: StockLevel, lock_version: int = 0) -> StockLevelModel:
    return StockLevelModel(
        item_code=level.item_code,
        warehouse_code=level.warehouse_code,
        currency=level.currency.value,
        quantity=level.quantity,
        value=level.value,
        lock_version=lock_version,
    )


def level_from_model(model: StockLevelModel) -> StockLevel:
    return StockLevel(
        item_code=model.item_code,
        warehouse_code=model.warehouse_code,
        currency=Currency(model.currency),
        quantity=model.quantity,
        value=model.value,
    )


def movement_to_model(movement: StockMovement) -> StockMovementModel:
    return StockMovementModel(
        id=movement.id,
        number=movement.number,
        movement_type=movement.movement_type.value,
        item_code=movement.item_code,
        warehouse_code=movement.warehouse_code,
        movement_date=movement.movement_date,
        quantity_delta=movement.quantity_delta,
        value_delta=movement.value_delta,
        currency=movement.currency.value,
        quantity_after=movement.quantity_after,
        value_after=movement.value_after,
        source_type=movement.source_type,
        source_ref=movement.source_ref,
        source_line_ref=movement.source_line_ref,
        reason=movement.reason,
        actor=movement.actor,
        created_at=movement.created_at.isoformat(),
    )


def movement_from_model(model: StockMovementModel) -> StockMovement:
    return StockMovement(
        id=model.id,
        number=model.number,
        movement_type=MovementType(model.movement_type),
        item_code=model.item_code,
        warehouse_code=model.warehouse_code,
        movement_date=model.movement_date,
        quantity_delta=model.quantity_delta,
        value_delta=model.value_delta,
        currency=Currency(model.currency),
        quantity_after=model.quantity_after,
        value_after=model.value_after,
        source_type=model.source_type,
        source_ref=model.source_ref,
        created_at=datetime.fromisoformat(model.created_at),
        source_line_ref=model.source_line_ref,
        reason=model.reason,
        actor=model.actor,
    )


def inventory_to_models(
    count: InventoryCount,
) -> tuple[StockInventoryModel, list[StockInventoryLineModel]]:
    header = StockInventoryModel(
        id=count.id,
        number=count.number,
        warehouse_code=count.warehouse_code,
        count_date=count.count_date,
        status=count.status.value,
        notes=count.notes,
    )
    lines = [
        StockInventoryLineModel(
            count_id=count.id,
            item_code=line.item_code,
            theoretical_quantity=line.theoretical_quantity,
            counted_quantity=line.counted_quantity,
            unit_cost=line.unit_cost,
        )
        for line in count.lines
    ]
    return header, lines


def inventory_from_models(
    header: StockInventoryModel, lines: list[StockInventoryLineModel]
) -> InventoryCount:
    return InventoryCount(
        id=header.id,
        warehouse_code=header.warehouse_code,
        count_date=header.count_date,
        status=InventoryStatus(header.status),
        number=header.number,
        notes=header.notes,
        lines=[
            InventoryLine(
                item_code=line.item_code,
                theoretical_quantity=line.theoretical_quantity,
                counted_quantity=line.counted_quantity,
                unit_cost=line.unit_cost,
            )
            for line in sorted(lines, key=lambda ln: ln.item_code)
        ],
    )
