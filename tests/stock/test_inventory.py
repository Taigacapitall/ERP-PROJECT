"""Tests de l'inventaire physique (memoire)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.stock.helpers import DAY, Env, usd
from vorpal.core.money import Currency, Money
from vorpal.modules.stock.domain.errors import (
    StockConflictError,
    StockNotFoundError,
    StockValidationError,
)
from vorpal.modules.stock.domain.inventory import InventoryStatus
from vorpal.modules.stock.domain.movements import MovementType
from vorpal.modules.stock.manifest import (
    EVENT_INVENTORY_VALIDATED,
    EVENT_LEVEL_LOW,
    EVENT_MOVEMENT_RECORDED,
)
from vorpal.modules.stock.services.movements import QuantityLine, ReceiptLine

D = Decimal


def stocked(env: Env, qty: str = "10", cost: str = "5.00", **item_kwargs) -> None:
    env.seed(**item_kwargs)
    env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", qty, usd(cost))])


def test_open_count_snapshots_theoretical_stock(env: Env) -> None:
    stocked(env)
    env.items.create_item(env.clerk, "VIDE", "Sans stock")
    count = env.inventory.open_count(env.counter, "C1", "MAIN", DAY, notes="Annuel")
    assert [(ln.item_code, ln.theoretical_quantity, ln.counted_quantity) for ln in count.lines] == [
        ("SAC-CIMENT", D("10.000"), None)
    ]
    explicit = env.inventory.open_count(env.counter, "C2", "MAIN", DAY, item_codes=["VIDE", "SAC-CIMENT"])
    assert [ln.item_code for ln in explicit.lines] == ["SAC-CIMENT", "VIDE"]
    with pytest.raises(StockConflictError):
        env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    with pytest.raises(StockNotFoundError):
        env.inventory.open_count(env.counter, "C3", "MAIN", DAY, item_codes=["FANTOME"])
    with pytest.raises(StockNotFoundError):
        env.inventory.open_count(env.counter, "C4", "NULLE", DAY)


def test_validate_negative_variance_issues_at_average_cost(env: Env) -> None:
    stocked(env)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "8")
    result = env.inventory.validate_count(env.counter, "C1")
    assert result.count.status is InventoryStatus.VALIDATED and result.count.number == "INV-2026-00001"
    v = result.variances[0]
    assert (v.theoretical_quantity, v.counted_quantity, v.quantity_delta, v.value_delta) == (
        D("10"), D("8"), D("-2"), D("-10.00"),
    )
    move = result.movements[0]
    assert move.movement_type is MovementType.ADJUSTMENT
    assert (move.source_type, move.source_ref, move.source_line_ref) == ("inventory", "C1", "SAC-CIMENT")
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("8"), D("40.00"))
    assert env.events_of(EVENT_INVENTORY_VALIDATED)[0].payload["number"] == "INV-2026-00001"
    assert env.events_of(EVENT_INVENTORY_VALIDATED)[0].payload["variances"][0]["quantity_delta"] == "-2.000"
    assert env.audit.verify_chain()


def test_validate_positive_variance_uses_average_when_stock_exists(env: Env) -> None:
    stocked(env)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "12")
    result = env.inventory.validate_count(env.counter, "C1")
    assert result.variances[0].value_delta == D("10.00")
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("60.00")


def test_zero_variance_creates_no_movement_but_validates(env: Env) -> None:
    stocked(env)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "10")
    result = env.inventory.validate_count(env.counter, "C1")
    assert result.movements == [] and result.variances[0].quantity_delta == D("0")
    assert len(env.queries.stock_card(env.reader, "SAC-CIMENT")) == 1  # seulement la reception


def test_found_stock_on_empty_item_requires_unit_cost(env: Env) -> None:
    env.seed()
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY, item_codes=["SAC-CIMENT"])
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "4")
    with pytest.raises(StockValidationError, match="Cout unitaire requis"):
        env.inventory.validate_count(env.counter, "C1")
    assert env.queries.get_count(env.reader, "C1").status is InventoryStatus.DRAFT  # rien d'applique
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "4", unit_cost=usd("2.50"))
    result = env.inventory.validate_count(env.counter, "C1")
    assert result.variances[0].value_delta == D("10.00")
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("10.00")


def test_validation_compares_to_current_stock_not_opening_snapshot(env: Env) -> None:
    stocked(env)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)  # theorique 10
    env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 4)])  # reste 6
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "6")
    result = env.inventory.validate_count(env.counter, "C1")
    assert result.variances[0].theoretical_quantity == D("6")
    assert result.movements == []  # aucun ecart reel : la sortie intermediaire est respectee


def test_uncounted_lines_are_ignored_and_new_item_can_be_added(env: Env) -> None:
    stocked(env)
    env.items.create_item(env.clerk, "VIS", "Vis", unit="boite")
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "VIS", "3", unit_cost=usd("1.00"))  # hors fiche
    result = env.inventory.validate_count(env.counter, "C1")
    assert [v.item_code for v in result.variances] == ["VIS"]
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")  # non compte : inchange
    assert env.queries.on_hand(env.reader, "VIS") == D("3")


def test_count_guards(env: Env) -> None:
    stocked(env, quantity_decimals=0)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    with pytest.raises(StockValidationError, match=r"[Aa]ucune"):
        env.inventory.validate_count(env.counter, "C1")  # rien de compte
    with pytest.raises(StockValidationError, match="decimale"):
        env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "1.5")
    with pytest.raises(StockValidationError, match="negative"):
        env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "-1")
    with pytest.raises(StockValidationError, match="doit etre en USD"):
        env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "1", unit_cost=Money(D("1"), Currency.CDF))
    with pytest.raises(StockNotFoundError):
        env.inventory.record_count(env.counter, "NOPE", "SAC-CIMENT", "1")
    with pytest.raises(StockNotFoundError):
        env.inventory.validate_count(env.counter, "NOPE")
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "10")
    env.inventory.validate_count(env.counter, "C1")
    with pytest.raises(StockConflictError):
        env.inventory.validate_count(env.counter, "C1")  # deja validee
    with pytest.raises(StockConflictError):
        env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "9")
    with pytest.raises(StockConflictError):
        env.inventory.cancel_count(env.counter, "C1")


def test_cancel_count_applies_nothing(env: Env) -> None:
    stocked(env)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "1")
    assert env.inventory.cancel_count(env.counter, "C1").status is InventoryStatus.CANCELLED
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")
    with pytest.raises(StockConflictError):
        env.inventory.validate_count(env.counter, "C1")
    assert [c.id for c in env.queries.list_counts(env.reader)] == ["C1"]


def test_inventory_adjustment_can_trigger_low_stock_alert(env: Env) -> None:
    stocked(env, reorder_threshold=5)
    env.inventory.open_count(env.counter, "C1", "MAIN", DAY)
    env.inventory.record_count(env.counter, "C1", "SAC-CIMENT", "3")
    env.inventory.validate_count(env.counter, "C1")
    assert len(env.events_of(EVENT_LEVEL_LOW)) == 1
    assert len(env.events_of(EVENT_MOVEMENT_RECORDED)) == 2  # reception + ajustement


def test_numbers_of_counts_are_sequential(env: Env) -> None:
    stocked(env)
    for cid in ("C1", "C2"):
        env.inventory.open_count(env.counter, cid, "MAIN", DAY)
        env.inventory.record_count(env.counter, cid, "SAC-CIMENT", "10")
    assert env.inventory.validate_count(env.counter, "C2").count.number == "INV-2026-00001"
    assert env.inventory.validate_count(env.counter, "C1").count.number == "INV-2026-00002"
