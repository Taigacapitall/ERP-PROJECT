"""Tests des services Stock sur persistance memoire : articles, entrepots, mouvements, requetes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.stock.helpers import DAY, Env, usd
from vorpal.core.errors import ComplianceError
from vorpal.core.money import Currency, Money
from vorpal.modules.stock.domain.errors import (
    StockConflictError,
    StockNotFoundError,
    StockValidationError,
)
from vorpal.modules.stock.domain.movements import MovementType
from vorpal.modules.stock.manifest import (
    EVENT_LEVEL_LOW,
    EVENT_MOVEMENT_RECORDED,
)
from vorpal.modules.stock.services.movements import QuantityLine, ReceiptLine

D = Decimal


def receive(env: Env, qty: str = "10", cost: str = "5.00", item: str = "SAC-CIMENT", wh: str = "MAIN", ref: str = ""):
    return env.movements.receive_stock(
        env.clerk, wh, DAY, [ReceiptLine(item, qty, usd(cost))], reference=ref
    )


# -- articles et entrepots --------------------------------------------------------------------


def test_item_creation_defaults_conflict_and_audit(env: Env) -> None:
    item = env.items.create_item(env.clerk, " ART-1 ", "Ciment")
    assert (item.code, item.unit, item.currency, item.quantity_decimals) == (
        "ART-1", "unite", Currency.USD, 3,
    )
    with pytest.raises(StockConflictError):
        env.items.create_item(env.clerk, "ART-1", "Doublon")
    assert any(e.action == "CREATE" and e.entity_id == "ART-1" for e in env.audit.entries)
    assert env.audit.verify_chain()


def test_permissions_are_enforced(env: Env) -> None:
    with pytest.raises(ComplianceError):
        env.items.create_item(env.reader, "A", "x")
    with pytest.raises(ComplianceError):
        env.warehouses.create_warehouse(env.clerk, "MAIN", "x")  # reserve a stock.admin
    env.seed()
    with pytest.raises(ComplianceError):
        env.movements.receive_stock(env.reader, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, usd("1"))])
    with pytest.raises(ComplianceError):
        env.inventory.open_count(env.clerk, "C1", "MAIN", DAY)
    with pytest.raises(ComplianceError):
        env.queries.list_items(nobody())
    with pytest.raises(ComplianceError):
        env.items.deactivate_item(env.clerk, "SAC-CIMENT")


def nobody():
    from tests.stock.helpers import make_user

    return make_user("nobody", set())


def test_update_item_and_threshold(env: Env) -> None:
    env.seed(quantity_decimals=0)
    item = env.items.update_item(env.clerk, "SAC-CIMENT", name="Ciment 50kg", reorder_threshold=5)
    assert item.name == "Ciment 50kg" and item.reorder_threshold == D("5")
    with pytest.raises(StockValidationError):
        env.items.update_item(env.clerk, "SAC-CIMENT", reorder_threshold="2.5")
    assert env.queries.get_item(env.reader, "SAC-CIMENT").reorder_threshold == D("5")
    item = env.items.update_item(env.clerk, "SAC-CIMENT", clear_reorder_threshold=True)
    assert item.reorder_threshold is None
    with pytest.raises(StockNotFoundError):
        env.items.update_item(env.clerk, "INCONNU", name="x")


def test_cannot_deactivate_item_or_warehouse_holding_stock(env: Env) -> None:
    env.seed()
    receive(env)
    with pytest.raises(StockConflictError, match="en stock"):
        env.items.deactivate_item(env.admin, "SAC-CIMENT")
    with pytest.raises(StockConflictError, match="stock"):
        env.warehouses.deactivate_warehouse(env.admin, "MAIN")
    env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 10)])
    env.items.deactivate_item(env.admin, "SAC-CIMENT")
    env.warehouses.deactivate_warehouse(env.admin, "MAIN")
    with pytest.raises(StockConflictError, match="desactive"):
        receive(env)
    assert env.queries.list_items(env.reader) == []
    assert len(env.queries.list_items(env.reader, include_inactive=True)) == 1
    env.items.reactivate_item(env.admin, "SAC-CIMENT")
    env.warehouses.reactivate_warehouse(env.admin, "MAIN")
    receive(env)


def test_duplicate_warehouse_refused(env: Env) -> None:
    env.warehouses.create_warehouse(env.admin, "MAIN", "Principal")
    with pytest.raises(StockConflictError):
        env.warehouses.create_warehouse(env.admin, "MAIN", "Autre")


# -- entrees / sorties -----------------------------------------------------------------------


def test_receive_then_issue_weighted_average_flow(env: Env) -> None:
    env.seed()
    m1 = receive(env, "10", "5.00")[0]
    m2 = receive(env, "10", "7.00")[0]
    assert m1.number == "MVT-2026-000001" and m2.number == "MVT-2026-000002"
    assert (m2.quantity_after, m2.value_after) == (D("20"), D("120.00"))
    out = env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 5)], reason="Vente")[0]
    assert (out.movement_type, out.quantity_delta, out.value_delta) == (
        MovementType.ISSUE, D("-5.000"), D("-30.00"),
    )
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value, level.average_cost) == (D("15.000"), D("90.00"), D("6.0000"))
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("15")
    assert env.queries.valuation(env.reader) == {Currency.USD: D("90.00")}


def test_issue_beyond_stock_refused_and_nothing_written(env: Env) -> None:
    env.seed()
    receive(env, "2")
    before = len(env.queries.stock_card(env.reader, "SAC-CIMENT"))
    with pytest.raises(StockConflictError, match="insuffisant"):
        env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 3)])
    assert len(env.queries.stock_card(env.reader, "SAC-CIMENT")) == before
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("2")


def test_multi_line_operation_is_atomic(env: Env) -> None:
    env.seed()
    env.items.create_item(env.clerk, "VIS", "Vis", unit="boite")
    with pytest.raises(StockNotFoundError):
        env.movements.receive_stock(
            env.clerk, "MAIN", DAY,
            [ReceiptLine("SAC-CIMENT", 5, usd("1")), ReceiptLine("INCONNU", 1, usd("1"))],
        )
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("0")
    assert env.events_of(EVENT_MOVEMENT_RECORDED) == []  # rien publie sur echec
    # Numerotation sans trou apres l'echec
    assert receive(env)[0].number == "MVT-2026-000001"


def test_line_validation(env: Env) -> None:
    env.seed(quantity_decimals=0)
    with pytest.raises(StockValidationError, match="decimale"):
        receive(env, "1.5")
    with pytest.raises(StockValidationError, match="strictement positive"):
        receive(env, "0")
    with pytest.raises(StockValidationError, match="Au moins une ligne"):
        env.movements.receive_stock(env.clerk, "MAIN", DAY, [])
    with pytest.raises(StockValidationError, match="qu'une fois"):
        env.movements.receive_stock(
            env.clerk, "MAIN", DAY,
            [ReceiptLine("SAC-CIMENT", 1, usd("1")), ReceiptLine(" SAC-CIMENT", 1, usd("1"))],
        )
    with pytest.raises(StockValidationError, match="doit etre en USD"):
        env.movements.receive_stock(
            env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, Money(D("1"), Currency.CDF))]
        )
    with pytest.raises(StockValidationError, match="negatif"):
        receive(env, "1", "-1.00")
    with pytest.raises(StockNotFoundError):
        receive(env, wh="AILLEURS")


def test_reference_makes_operation_idempotent_by_refusal(env: Env) -> None:
    env.seed()
    receive(env, ref="BL-001")
    with pytest.raises(StockConflictError, match="deja enregistre"):
        receive(env, ref="BL-001")
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")
    receive(env, ref="BL-002")
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("20")


def test_reserved_and_malformed_source_types_refused(env: Env) -> None:
    env.seed()
    for source in ("goods_receipt", "landed_cost", "inventory", "Manuel", "a b", ""):
        with pytest.raises(StockValidationError):
            env.movements.receive_stock(
                env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, usd("1"))],
                reference="X", source_type=source,
            )
    receive(env)
    out = env.movements.issue_stock(
        env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 1)], reference="FAC-1", source_type="sale"
    )[0]
    assert (out.source_type, out.source_ref, out.source_line_ref) == ("sale", "FAC-1", "SAC-CIMENT")


# -- transferts ------------------------------------------------------------------------------


def test_transfer_preserves_value_and_pairs_movements(env: Env) -> None:
    env.seed()
    env.warehouses.create_warehouse(env.admin, "DEPOT-B", "Depot B")
    receive(env, "10", "5.00")
    receive(env, "10", "7.00")  # cout moyen 6.00
    moves = env.movements.transfer_stock(
        env.clerk, "MAIN", "DEPOT-B", DAY, [QuantityLine("SAC-CIMENT", 5)], reference="TR-1"
    )
    assert [m.movement_type for m in moves] == [MovementType.TRANSFER_OUT, MovementType.TRANSFER_IN]
    assert (moves[0].value_delta, moves[1].value_delta) == (D("-30.00"), D("30.00"))
    main = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    depot = env.queries.get_level(env.reader, "SAC-CIMENT", "DEPOT-B")
    assert (main.quantity, main.value) == (D("15"), D("90.00"))
    assert (depot.quantity, depot.value) == (D("5"), D("30.00"))
    assert env.queries.valuation(env.reader) == {Currency.USD: D("120.00")}  # valeur conservee
    assert env.queries.valuation(env.reader, "DEPOT-B") == {Currency.USD: D("30.00")}
    assert {lv.warehouse_code for lv in env.queries.item_levels(env.reader, "SAC-CIMENT")} == {"MAIN", "DEPOT-B"}
    assert [lv.item_code for lv in env.queries.warehouse_levels(env.reader, "DEPOT-B")] == ["SAC-CIMENT"]


def test_transfer_guards(env: Env) -> None:
    env.seed()
    env.warehouses.create_warehouse(env.admin, "DEPOT-B", "Depot B")
    receive(env, "2")
    with pytest.raises(StockValidationError, match="differer"):
        env.movements.transfer_stock(env.clerk, "MAIN", "MAIN", DAY, [QuantityLine("SAC-CIMENT", 1)])
    with pytest.raises(StockConflictError, match="insuffisant"):
        env.movements.transfer_stock(env.clerk, "MAIN", "DEPOT-B", DAY, [QuantityLine("SAC-CIMENT", 3)])
    with pytest.raises(StockNotFoundError):
        env.movements.transfer_stock(env.clerk, "MAIN", "NULLE-PART", DAY, [QuantityLine("SAC-CIMENT", 1)])
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "DEPOT-B").quantity == D("0")
    env.movements.transfer_stock(env.clerk, "MAIN", "DEPOT-B", DAY, [QuantityLine("SAC-CIMENT", 1)], reference="T")
    with pytest.raises(StockConflictError, match="deja enregistre"):
        env.movements.transfer_stock(env.clerk, "MAIN", "DEPOT-B", DAY, [QuantityLine("SAC-CIMENT", 1)], reference="T")


# -- evenements, alertes, requetes -----------------------------------------------------------


def test_events_are_published_after_commit_with_full_payload(env: Env) -> None:
    env.seed()
    receive(env, "10", "5.00", ref="BL-9")
    events = env.events_of(EVENT_MOVEMENT_RECORDED)
    assert len(events) == 1
    ev = events[0]
    assert ev.source_module == "stock" and ev.correlation_id == "BL-9"
    assert ev.payload["movement_type"] == "RECEIPT"
    assert ev.payload["quantity_delta"] == "10.000" and ev.payload["value_delta"] == "50.00"
    assert ev.payload["source_ref"] == "BL-9" and ev.payload["currency"] == "USD"


def test_low_stock_event_only_when_crossing_the_threshold(env: Env) -> None:
    env.seed(reorder_threshold=5)
    receive(env, "10")

    def out(n: int) -> None:
        env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", n)])

    out(3)  # 7 > 5 : pas d'alerte
    assert env.events_of(EVENT_LEVEL_LOW) == []
    out(2)  # 5 : franchit (7 > 5 >= 5)
    alerts = env.events_of(EVENT_LEVEL_LOW)
    assert len(alerts) == 1 and alerts[0].payload["item_code"] == "SAC-CIMENT"
    assert alerts[0].payload["quantity"] == "5.000" and alerts[0].payload["threshold"] == "5.000"
    out(1)  # deja sous le seuil : pas de nouvelle alerte
    assert len(env.events_of(EVENT_LEVEL_LOW)) == 1
    assert [r.item.code for r in env.queries.low_stock_items(env.reader)] == ["SAC-CIMENT"]
    receive(env, "20")
    assert env.queries.low_stock_items(env.reader) == []


def test_transfer_does_not_trigger_low_stock(env: Env) -> None:
    env.seed(reorder_threshold=5)
    env.warehouses.create_warehouse(env.admin, "B", "B")
    receive(env, "6")
    env.movements.transfer_stock(env.clerk, "MAIN", "B", DAY, [QuantityLine("SAC-CIMENT", 6)])
    assert env.events_of(EVENT_LEVEL_LOW) == []


def test_stock_card_is_sorted_and_filterable(env: Env) -> None:
    env.seed()
    env.warehouses.create_warehouse(env.admin, "B", "B")
    env.movements.receive_stock(env.clerk, "MAIN", date(2026, 11, 22), [ReceiptLine("SAC-CIMENT", 5, usd("1"))])
    env.movements.receive_stock(env.clerk, "B", date(2026, 11, 21), [ReceiptLine("SAC-CIMENT", 5, usd("1"))])
    card = env.queries.stock_card(env.reader, "SAC-CIMENT")
    assert [m.movement_date.day for m in card] == [21, 22]
    only_main = env.queries.stock_card(env.reader, "SAC-CIMENT", "MAIN")
    assert [m.warehouse_code for m in only_main] == ["MAIN"]
    with pytest.raises(StockNotFoundError):
        env.queries.stock_card(env.reader, "SAC-CIMENT", "ZZZ")


def test_get_level_of_untouched_pair_is_zero_and_valuation_empty(env: Env) -> None:
    env.seed()
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value, level.average_cost) == (D("0"), D("0.00"), None)
    assert env.queries.valuation(env.reader) == {}
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("0")
    assert [w.code for w in env.queries.list_warehouses(env.reader)] == ["MAIN"]
    assert env.queries.get_warehouse(env.reader, "MAIN").name == "Entrepot MAIN"
    with pytest.raises(StockNotFoundError):
        env.queries.get_warehouse(env.reader, "X")


def test_valuation_is_grouped_by_currency(env: Env) -> None:
    env.seed()
    env.items.create_item(env.clerk, "LOCAL", "Article en francs", unit="piece", currency=Currency.CDF)
    receive(env, "2", "10.00")
    env.movements.receive_stock(
        env.clerk, "MAIN", DAY, [ReceiptLine("LOCAL", 3, Money(D("1000.00"), Currency.CDF))]
    )
    assert env.queries.valuation(env.reader) == {Currency.USD: D("20.00"), Currency.CDF: D("3000.00")}


def test_audit_chain_stays_valid_after_activity(env: Env) -> None:
    env.seed()
    receive(env)
    env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 4)])
    assert env.audit.verify_chain()
    assert {e.action for e in env.audit.entries} >= {"CREATE", "RECEIVE", "ISSUE"}
