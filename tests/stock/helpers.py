"""Construction d'un environnement Stock complet (sans SQLAlchemy) partage par les tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count
from typing import Any

from vorpal.core.audit import AuditLog
from vorpal.core.events import Event, EventBus, InMemoryEventStore
from vorpal.core.money import Currency, Money
from vorpal.core.security import AuthorizationService, Role, User
from vorpal.modules.stock.adapters.events import (
    EventBusStockPublisher,
    GoodsReceiptValidatedHandler,
    LandedCostAllocatedHandler,
)
from vorpal.modules.stock.manifest import (
    EVENT_GOODS_RECEIPT_VALIDATED,
    EVENT_LANDED_COST_ALLOCATED,
    STOCK_PERMISSIONS,
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

D = Decimal
DAY = date(2026, 11, 20)
FIXED_NOW = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)


def usd(amount: str) -> Money:
    return Money(Decimal(amount), Currency.USD)


def make_user(name: str, permissions: set[str]) -> User:
    return User(
        id=f"u-{name}",
        username=name,
        roles=(Role(name=f"role-{name}", permissions=frozenset(permissions)),),
    )


@dataclass
class Env:
    settings: StockSettings
    audit: AuditLog
    authz: AuthorizationService
    bus: EventBus
    store: InMemoryEventStore
    ledger: StockLedger
    items: ItemService
    warehouses: WarehouseService
    movements: MovementService
    inventory: InventoryService
    queries: StockQueryService
    ingestion: PurchaseIngestionService
    receipt_handler: GoodsReceiptValidatedHandler
    landed_handler: LandedCostAllocatedHandler
    admin: User
    clerk: User
    reader: User
    counter: User
    uow_factory: Callable[[], StockUnitOfWork]

    def events_of(self, event_type: str) -> list[Event]:
        """Evenements publies d'un type (le magasin dedoublonne par id : un rejeu n'en ajoute pas)."""
        return self.store.list_events(event_type)

    def seed(self, item: str = "SAC-CIMENT", warehouse: str = "MAIN", **item_kwargs: Any) -> None:
        """Cree un article et un entrepot (avec les parametres d'article facultatifs)."""
        self.items.create_item(self.admin, item, f"Article {item}", unit="sac", **item_kwargs)
        known = {w.code for w in self.queries.list_warehouses(self.admin, True)}
        if warehouse not in known:
            self.warehouses.create_warehouse(self.admin, warehouse, f"Entrepot {warehouse}")


def build_env(
    uow_factory: Callable[[], StockUnitOfWork],
    settings: StockSettings | None = None,
) -> Env:
    settings = settings or StockSettings()
    audit = AuditLog()
    authz = AuthorizationService()
    authz.register_module_permissions("stock", list(STOCK_PERMISSIONS))
    store = InMemoryEventStore()
    bus = EventBus(store=store)

    def clock() -> datetime:
        return FIXED_NOW

    counter = count(1)

    def id_factory() -> str:
        return f"ID-{next(counter):05d}"

    publisher = EventBusStockPublisher(bus, clock=clock)
    ledger = StockLedger(settings, id_factory, clock)
    ingestion = PurchaseIngestionService(uow_factory, settings, audit, ledger, publisher, clock)
    receipt_handler = GoodsReceiptValidatedHandler(ingestion)
    landed_handler = LandedCostAllocatedHandler(ingestion)
    bus.subscribe(EVENT_GOODS_RECEIPT_VALIDATED, receipt_handler)
    bus.subscribe(EVENT_LANDED_COST_ALLOCATED, landed_handler)
    return Env(
        settings=settings,
        audit=audit,
        authz=authz,
        bus=bus,
        store=store,
        ledger=ledger,
        items=ItemService(uow_factory, settings, audit, authz, clock),
        warehouses=WarehouseService(uow_factory, audit, authz, clock),
        movements=MovementService(
            uow_factory, settings, audit, authz, ledger, id_factory, publisher, clock
        ),
        inventory=InventoryService(uow_factory, settings, audit, authz, ledger, publisher, clock),
        queries=StockQueryService(uow_factory, authz),
        ingestion=ingestion,
        receipt_handler=receipt_handler,
        landed_handler=landed_handler,
        admin=make_user("admin", set(STOCK_PERMISSIONS)),
        clerk=make_user("clerk", {"stock.view", "stock.create"}),
        reader=make_user("reader", {"stock.view"}),
        counter=make_user("counter", {"stock.view", "stock.inventory"}),
        uow_factory=uow_factory,
    )


def receipt_payload(
    receipt_id: str = "REC-1",
    warehouse: str = "MAIN",
    lines: list[tuple[str, str, str, str]] | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Charge utile identique a celle de `achats.goods_receipt.validated` (voir services/receipts.py).

    `lines` : (line_id, item_code, quantite, cout unitaire HT).
    """
    rows = lines if lines is not None else [("L1", "SAC-CIMENT", "10", "5.00")]
    return {
        "goods_receipt_id": receipt_id,
        "number": f"REC-2026-{receipt_id[-5:]}",
        "purchase_order_id": "PO-1",
        "purchase_order_status": "RECEIVED",
        "supplier_id": "SUP-1",
        "warehouse_code": warehouse,
        "receipt_date": "2026-11-20",
        "currency": currency,
        "total_base_ht": "0.00",
        "lines": [
            {
                "line_id": line_id,
                "order_line_id": f"O-{line_id}",
                "item_code": item,
                "quantity_received": qty,
                "unit_cost_ht": cost,
                "base_line_ht": "0.00",
            }
            for line_id, item, qty, cost in rows
        ],
        "validated_at": "2026-11-20T10:00:00+00:00",
    }


def landed_payload(
    landed_cost_id: str = "LC-1",
    receipt_id: str = "REC-1",
    allocations: list[tuple[str, str, str]] | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Charge utile identique a celle de `achats.landed_cost.allocated` (voir services/landed_costs.py).

    `allocations` : (receipt_line_id, item_code, montant).
    """
    rows = allocations if allocations is not None else [("L1", "SAC-CIMENT", "20.00")]
    return {
        "landed_cost_id": landed_cost_id,
        "goods_receipt_id": receipt_id,
        "goods_receipt_number": "REC-2026-00001",
        "method": "BY_AMOUNT",
        "currency": currency,
        "total_landed_cost": "0.00",
        "allocations": [
            {"receipt_line_id": lid, "item_code": item, "allocated_amount": amount}
            for lid, item, amount in rows
        ],
        "allocated_at": "2026-11-20T10:00:00+00:00",
    }


def publish_achats(env: Env, event_type: str, payload: dict[str, Any]) -> Event:
    event = Event(type=event_type, payload=payload, source_module="achats")
    env.bus.publish(event)
    return event


# -- scenario de reference (calcule a la main, rejoue sur memoire ET sur SQL) ---------------------

SCENARIO_EXPECTED: dict[str, Any] = {
    # 10 @ 5.00 + 10 @ 7.00 -> 20 / 120.00 (cout moyen 6.00) ; sortie de 5 -> 15 / 90.00 ;
    # transfert de 5 vers DEPOT-B : -30.00 / +30.00 -> MAIN 10 / 60.00 ; inventaire MAIN : 12 compte
    # pour 10 theorique -> +2 au cout moyen 6.00 = +12.00 -> MAIN 12 / 72.00.
    "main": ("12.000", "72.00"),
    "depot_b": ("5.000", "30.00"),
    "valuation_usd": "102.00",
    "on_hand": "17.000",
    "numbers": [f"MVT-2026-{n:06d}" for n in range(1, 7)],
    "inventory_number": "INV-2026-00001",
    "types": ["RECEIPT", "RECEIPT", "ISSUE", "TRANSFER_OUT", "TRANSFER_IN", "ADJUSTMENT"],
}


def run_scenario(env: Env) -> dict[str, Any]:
    from vorpal.modules.stock.services.movements import QuantityLine, ReceiptLine

    env.seed(reorder_threshold=5)
    env.warehouses.create_warehouse(env.admin, "DEPOT-B", "Depot B")
    env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 10, usd("5.00"))])
    env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 10, usd("7.00"))])
    env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 5)])
    env.movements.transfer_stock(
        env.clerk, "MAIN", "DEPOT-B", DAY, [QuantityLine("SAC-CIMENT", 5)], reference="TR-1"
    )
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "12")
    result = env.inventory.validate_count(env.counter, "C1")
    main = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    depot = env.queries.get_level(env.reader, "SAC-CIMENT", "DEPOT-B")
    card = sorted(env.queries.stock_card(env.reader, "SAC-CIMENT"), key=lambda m: m.number)
    return {
        "main": (str(main.quantity), str(main.value)),
        "depot_b": (str(depot.quantity), str(depot.value)),
        "valuation_usd": str(env.queries.valuation(env.reader)[Currency.USD]),
        "on_hand": str(env.queries.on_hand(env.reader, "SAC-CIMENT")),
        "numbers": [m.number for m in card],
        "inventory_number": result.count.number,
        "types": [m.movement_type.value for m in card],
    }
