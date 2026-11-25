"""Point d'assemblage (composition root) du module Stock."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from vorpal.core.context import AppContext
from vorpal.core.module import BaseModule, ModuleManifest
from vorpal.modules.stock.adapters.events import (
    EventBusStockPublisher,
    GoodsReceiptValidatedHandler,
    LandedCostAllocatedHandler,
)
from vorpal.modules.stock.adapters.persistence.migrations import apply_stock_migrations
from vorpal.modules.stock.adapters.persistence.unit_of_work import SqlStockUnitOfWork
from vorpal.modules.stock.manifest import (
    EVENT_GOODS_RECEIPT_VALIDATED,
    EVENT_LANDED_COST_ALLOCATED,
    STOCK_MANIFEST,
)
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.ingestion import PurchaseIngestionService
from vorpal.modules.stock.services.inventory import InventoryService
from vorpal.modules.stock.services.items import ItemService
from vorpal.modules.stock.services.ledger import StockLedger
from vorpal.modules.stock.services.movements import MovementService
from vorpal.modules.stock.services.queries import StockQueryService
from vorpal.modules.stock.services.warehouses import WarehouseService
from vorpal.modules.stock.settings import StockSettings


class StockModule(BaseModule):
    """Module Stock : articles, entrepots, mouvements valorises, inventaire, liaison Achats.

    Ne depend d'aucun autre module : il ecoute `achats.goods_receipt.validated` et
    `achats.landed_cost.allocated` sur le bus, et publie ses propres evenements `stock.*`.
    """

    manifest: ModuleManifest = STOCK_MANIFEST

    def __init__(
        self,
        settings_path: Path | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._settings_path = settings_path
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._settings: StockSettings | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._handlers: list[GoodsReceiptValidatedHandler | LandedCostAllocatedHandler] = []
        self.uow_factory: Callable[[], StockUnitOfWork] | None = None
        self.item_service: ItemService | None = None
        self.warehouse_service: WarehouseService | None = None
        self.movement_service: MovementService | None = None
        self.inventory_service: InventoryService | None = None
        self.query_service: StockQueryService | None = None
        self.ingestion_service: PurchaseIngestionService | None = None

    def install(self, ctx: AppContext) -> None:
        self._settings = StockSettings.from_json_file(self._settings_path)
        apply_stock_migrations(ctx.db)
        self._session_factory = sessionmaker(bind=ctx.db)

    def start(self, ctx: AppContext) -> None:
        if self._settings is None or self._session_factory is None:
            self.install(ctx)
        assert self._settings is not None
        assert self._session_factory is not None

        settings = self._settings
        factory = self._session_factory

        def uow_factory() -> StockUnitOfWork:
            return SqlStockUnitOfWork(factory)

        publisher = EventBusStockPublisher(ctx.event_bus, clock=ctx.clock)
        ledger = StockLedger(settings, self._id_factory, ctx.clock)

        self.uow_factory = uow_factory
        self.item_service = ItemService(uow_factory, settings, ctx.audit, ctx.authz, ctx.clock)
        self.warehouse_service = WarehouseService(uow_factory, ctx.audit, ctx.authz, ctx.clock)
        self.movement_service = MovementService(
            uow_factory,
            settings,
            ctx.audit,
            ctx.authz,
            ledger,
            self._id_factory,
            publisher,
            ctx.clock,
        )
        self.inventory_service = InventoryService(
            uow_factory, settings, ctx.audit, ctx.authz, ledger, publisher, ctx.clock
        )
        self.query_service = StockQueryService(uow_factory, ctx.authz)
        ingestion = PurchaseIngestionService(
            uow_factory, settings, ctx.audit, ledger, publisher, ctx.clock
        )
        self.ingestion_service = ingestion

        receipt_handler = GoodsReceiptValidatedHandler(ingestion)
        landed_handler = LandedCostAllocatedHandler(ingestion)
        ctx.event_bus.subscribe(EVENT_GOODS_RECEIPT_VALIDATED, receipt_handler)
        ctx.event_bus.subscribe(EVENT_LANDED_COST_ALLOCATED, landed_handler)
        self._handlers = [receipt_handler, landed_handler]

    def stop(self) -> None:
        for handler in self._handlers:
            handler.enabled = False
        self._handlers = []
        self.uow_factory = None
        self.item_service = None
        self.warehouse_service = None
        self.movement_service = None
        self.inventory_service = None
        self.query_service = None
        self.ingestion_service = None

    def healthcheck(self) -> bool:
        return (
            self._settings is not None
            and self._session_factory is not None
            and self.movement_service is not None
        )
