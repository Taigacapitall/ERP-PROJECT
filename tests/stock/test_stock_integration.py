"""StockModule dans un vrai ModuleRegistry, relie au VRAI module Achats par le bus d'evenements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from vorpal.core import (
    AppConfig,
    AppContext,
    AuditLog,
    AuthorizationService,
    Currency,
    Event,
    EventBus,
    InMemoryEventStore,
    ModuleRegistry,
    Money,
    ServiceRegistry,
    User,
    create_db_engine,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
from vorpal.core.module import ModuleState
from vorpal.modules.achats.domain.landed_costs import LandedCostComponent
from vorpal.modules.achats.manifest import ACHATS_PERMISSIONS
from vorpal.modules.achats.module import AchatsModule
from vorpal.modules.stock.manifest import (
    EVENT_MOVEMENT_RECORDED,
    STOCK_EVENTS_LISTENED,
    STOCK_PERMISSIONS,
)
from vorpal.modules.stock.module import StockModule
from vorpal.modules.stock.services.movements import QuantityLine, ReceiptLine

NOW = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
DAY = date(2026, 11, 20)
D = Decimal


@dataclass
class World:
    registry: ModuleRegistry
    ctx: AppContext
    store: InMemoryEventStore
    stock: StockModule
    achats: AchatsModule
    buyer: User
    keeper: User


def build_world(db_url: str = "sqlite:///:memory:", with_achats: bool = True) -> World:
    engine = create_db_engine(db_url)
    store = InMemoryEventStore()
    ctx = AppContext(
        event_bus=EventBus(store=store),
        services=ServiceRegistry(),
        config=AppConfig(env="test", db_url=db_url),
        db=engine,
        audit=AuditLog(),
        authz=AuthorizationService(),
        clock=lambda: NOW,
    )
    stock, achats = StockModule(), AchatsModule()
    registry = ModuleRegistry()
    registry.register(stock)
    if with_achats:
        registry.register(achats)
    registry.start_all(ctx)
    return World(
        registry, ctx, store, stock, achats,
        buyer=User(id="U-B", username="josue", direct_permissions=frozenset(ACHATS_PERMISSIONS)),
        keeper=User(id="U-K", username="magasinier", direct_permissions=frozenset(STOCK_PERMISSIONS)),
    )


def prepare_masters(w: World) -> None:
    assert w.stock.item_service and w.stock.warehouse_service
    w.stock.item_service.create_item(w.keeper, "CABLE-CU", "Cable cuivre 16mm", unit="metre")
    w.stock.warehouse_service.create_warehouse(w.keeper, "DEPOT-LSH", "Depot Lubumbashi")


def purchase_and_receive(w: World, quantity: str = "10", cost: str = "25.00") -> str:
    a = w.achats
    a.supplier_service.create_supplier(
        actor=w.buyer, supplier_id="SUP-10", name="Fournisseur RDC", nif="NIF-998877",
        rccm="CD/LSH/RCCM/25-A-010", address="Lubumbashi",
    )
    po = a.purchase_order_service.create_order(
        actor=w.buyer, order_id="PO-10", supplier_id="SUP-10", order_date=DAY
    )
    a.purchase_order_service.add_line(
        actor=w.buyer, order_id=po.id, line_id="POL-1", item_code="CABLE-CU",
        designation="Cable cuivre 16mm", quantity_ordered=D(quantity),
        unit_price_ht=Money(D(cost), Currency.USD),
    )
    confirmed = a.purchase_order_service.confirm_order(actor=w.buyer, order_id=po.id)
    receipt = a.goods_receipt_service.create_receipt(
        actor=w.buyer, receipt_id="REC-10", purchase_order_id=confirmed.id,
        receipt_date=DAY, warehouse_code="DEPOT-LSH",
    )
    a.goods_receipt_service.add_line(
        actor=w.buyer, receipt_id=receipt.id, line_id="GRL-1", order_line_id="POL-1",
        item_code="CABLE-CU", designation="Cable cuivre 16mm", quantity_received=D(quantity),
        unit_cost_ht=Money(D(cost), Currency.USD),
    )
    return receipt.id


def failures(w: World) -> list[Event]:
    return w.store.list_events(EVENT_HANDLER_FAILED)


# -- cycle de vie du module -------------------------------------------------------------------


def test_stock_module_lifecycle_permissions_and_health() -> None:
    w = build_world(with_achats=False)
    assert w.registry.get_state("stock") == ModuleState.RUNNING
    assert w.stock.healthcheck() is True
    for code in STOCK_PERMISSIONS:
        assert w.ctx.authz.is_permission_declared(code)
    assert w.stock.manifest.subscribes == list(STOCK_EVENTS_LISTENED)
    assert w.stock.manifest.depends == []  # aucune dependance : Stock fonctionne sans Achats
    w.registry.stop_all()
    assert w.registry.get_state("stock") == ModuleState.STOPPED
    assert w.stock.healthcheck() is False


def test_module_works_end_to_end_on_sql_through_its_own_services() -> None:
    w = build_world(with_achats=False)
    assert w.stock.item_service and w.stock.warehouse_service
    assert w.stock.movement_service and w.stock.query_service
    prepare_masters(w)
    w.stock.movement_service.receive_stock(
        w.keeper, "DEPOT-LSH", DAY, [ReceiptLine("CABLE-CU", 10, Money(D("25.00"), Currency.USD))]
    )
    w.stock.movement_service.issue_stock(w.keeper, "DEPOT-LSH", DAY, [QuantityLine("CABLE-CU", 4)])
    level = w.stock.query_service.get_level(w.keeper, "CABLE-CU", "DEPOT-LSH")
    assert (level.quantity, level.value) == (D("6"), D("150.00"))
    assert len(w.store.list_events(EVENT_MOVEMENT_RECORDED)) == 2
    assert w.ctx.audit.verify_chain() is True


def test_stopped_module_ignores_purchase_events() -> None:
    w = build_world()
    prepare_masters(w)
    receipt_id = purchase_and_receive(w)
    receipts = w.achats.goods_receipt_service
    assert receipts is not None
    w.registry.stop_all()  # les services des modules passent a None : on garde notre reference
    receipts.validate_receipt(actor=w.buyer, receipt_id=receipt_id)
    assert failures(w) == []
    assert w.store.list_events(EVENT_MOVEMENT_RECORDED) == []  # Stock arrete : rien n'est entre


# -- integration avec le vrai module Achats --------------------------------------------------


def test_real_achats_receipt_enters_stock_then_landed_cost_revalues_it() -> None:
    w = build_world()
    prepare_masters(w)
    receipt_id = purchase_and_receive(w)
    assert w.stock.query_service.on_hand(w.keeper, "CABLE-CU") == D("0")  # brouillon : pas de stock

    w.achats.goods_receipt_service.validate_receipt(actor=w.buyer, receipt_id=receipt_id)
    level = w.stock.query_service.get_level(w.keeper, "CABLE-CU", "DEPOT-LSH")
    assert (level.quantity, level.value) == (D("10"), D("250.00"))
    card = w.stock.query_service.stock_card(w.keeper, "CABLE-CU")
    assert [(m.source_type, m.source_ref, m.source_line_ref) for m in card] == [
        ("goods_receipt", "REC-10", "GRL-1")
    ]

    w.achats.landed_cost_service.allocate_for_receipt(
        actor=w.buyer, landed_cost_id="LC-10", goods_receipt_id=receipt_id, allocation_date=DAY,
        components=[
            LandedCostComponent(category="FREIGHT", label="Fret routier", amount=Money(D("50.00"), Currency.USD))
        ],
    )
    level = w.stock.query_service.get_level(w.keeper, "CABLE-CU", "DEPOT-LSH")
    assert (level.quantity, level.value, level.average_cost) == (D("10"), D("300.00"), D("30.0000"))
    assert failures(w) == []
    assert w.ctx.audit.verify_chain() is True


def test_receipt_for_unknown_item_is_reported_then_recovered_by_replay() -> None:
    w = build_world()
    assert w.stock.warehouse_service
    w.stock.warehouse_service.create_warehouse(w.keeper, "DEPOT-LSH", "Depot Lubumbashi")
    receipt_id = purchase_and_receive(w)
    w.achats.goods_receipt_service.validate_receipt(actor=w.buyer, receipt_id=receipt_id)
    assert len(failures(w)) == 1  # article inconnu : signale, sans bloquer Achats
    assert w.stock.query_service.valuation(w.keeper) == {}

    assert w.stock.item_service
    w.stock.item_service.create_item(w.keeper, "CABLE-CU", "Cable cuivre 16mm", unit="metre")
    w.ctx.event_bus.replay("achats.goods_receipt.validated")
    assert w.stock.query_service.on_hand(w.keeper, "CABLE-CU") == D("10")


def test_stock_data_survives_restart_and_replay_does_not_double_count(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'world.db'}"
    w = build_world(url)
    prepare_masters(w)
    receipt_id = purchase_and_receive(w)
    w.achats.goods_receipt_service.validate_receipt(actor=w.buyer, receipt_id=receipt_id)
    w.ctx.db.dispose()

    w2 = build_world(url)  # nouveau processus : base relue, bus vide
    assert w2.stock.query_service.on_hand(w2.keeper, "CABLE-CU") == D("10")
    w2.ctx.event_bus.publish(
        Event(
            type="achats.goods_receipt.validated",
            source_module="achats",
            payload={
                "goods_receipt_id": "REC-10", "warehouse_code": "DEPOT-LSH", "receipt_date": "2026-11-20",
                "currency": "USD",
                "lines": [{"line_id": "GRL-1", "item_code": "CABLE-CU", "quantity_received": "10", "unit_cost_ht": "25.00"}],
            },
        )
    )
    assert w2.stock.query_service.on_hand(w2.keeper, "CABLE-CU") == D("10")
