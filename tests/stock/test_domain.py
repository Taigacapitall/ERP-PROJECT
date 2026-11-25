"""Tests du domaine pur : valeurs, articles, soldes (cout moyen), mouvements, inventaire, reglages."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from vorpal.core.errors import ValidationError
from vorpal.core.money import Currency
from vorpal.modules.stock.domain.errors import StockConflictError, StockValidationError
from vorpal.modules.stock.domain.inventory import InventoryCount, InventoryStatus
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.levels import StockLevel
from vorpal.modules.stock.domain.movements import MovementType, StockMovement
from vorpal.modules.stock.domain.values import ensure_decimal, ensure_quantity, round_amount
from vorpal.modules.stock.domain.warehouses import Warehouse
from vorpal.modules.stock.settings import SequenceConfig, StockSettings

D = Decimal


def level() -> StockLevel:
    return StockLevel("A", "MAIN", Currency.USD)


# -- valeurs ------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [1.5, True, "abc", "", "NaN", "Infinity", None, "1e13"])
def test_ensure_decimal_rejects_invalid(bad) -> None:
    with pytest.raises(StockValidationError):
        ensure_decimal(bad)


def test_ensure_decimal_accepts_int_str_decimal() -> None:
    assert ensure_decimal(3) == D("3")
    assert ensure_decimal(" 2.50 ") == D("2.5")
    assert ensure_decimal(D("1.1")) == D("1.1")


def test_ensure_quantity_scale_and_sign() -> None:
    assert ensure_quantity("2.5", 1) == D("2.5")
    assert str(ensure_quantity(3, 3)) == "3.000"
    with pytest.raises(StockValidationError, match="decimale"):
        ensure_quantity("2.5", 0)
    with pytest.raises(StockValidationError, match="strictement positive"):
        ensure_quantity(0, 0)
    with pytest.raises(StockValidationError, match="negative"):
        ensure_quantity(-1, 0, allow_zero=True)
    assert ensure_quantity(0, 0, allow_zero=True) == D("0")


def test_round_amount_half_up() -> None:
    assert round_amount(D("1.005")) == D("1.01")
    assert round_amount(D("1.004")) == D("1.00")


# -- articles et entrepots ------------------------------------------------------------------


def test_item_normalizes_and_validates() -> None:
    item = StockItem(code=" ART-1 ", name=" Sac ", unit=" sac ", currency=Currency.USD)
    assert (item.code, item.name, item.unit) == ("ART-1", "Sac", "sac")
    for kwargs in (
        {"code": " "},
        {"name": " "},
        {"unit": ""},
        {"quantity_decimals": 7},
        {"quantity_decimals": True},
        {"reorder_threshold": D("-1")},
    ):
        base = {"code": "A", "name": "n", "unit": "u", "currency": Currency.USD}
        with pytest.raises(StockValidationError):
            StockItem(**{**base, **kwargs})


def test_item_threshold_respects_item_precision() -> None:
    with pytest.raises(StockValidationError):
        StockItem("A", "n", "sac", Currency.USD, quantity_decimals=0, reorder_threshold=D("2.5"))
    item = StockItem("A", "n", "sac", Currency.USD, quantity_decimals=0, reorder_threshold=D("5"))
    assert item.reorder_threshold == D("5")


def test_item_update_details_is_atomic() -> None:
    item = StockItem("A", "Ancien", "sac", Currency.USD, quantity_decimals=0, reorder_threshold=D("2"))
    with pytest.raises(StockValidationError):
        item.update_details(name="Nouveau", reorder_threshold="1.5")
    assert item.name == "Ancien" and item.reorder_threshold == D("2")
    item.update_details(name="Nouveau", clear_reorder_threshold=True)
    assert item.name == "Nouveau" and item.reorder_threshold is None
    with pytest.raises(StockValidationError):
        item.update_details(reorder_threshold="3", clear_reorder_threshold=True)


def test_inactive_item_and_warehouse_refuse_activity() -> None:
    item = StockItem("A", "n", "u", Currency.USD)
    item.deactivate()
    with pytest.raises(StockConflictError):
        item.ensure_active()
    item.reactivate()
    item.ensure_active()
    wh = Warehouse("MAIN", "Principal")
    wh.deactivate()
    with pytest.raises(StockConflictError):
        wh.ensure_active()
    with pytest.raises(StockValidationError):
        Warehouse(" ", "x")


# -- soldes : cout moyen pondere ------------------------------------------------------------


def test_receive_recomputes_weighted_average() -> None:
    lv = level()
    assert lv.receive(D("10"), D("5.00")) == D("50.00")
    assert lv.receive(D("10"), D("7.00")) == D("70.00")
    assert (lv.quantity, lv.value, lv.average_cost) == (D("20"), D("120.00"), D("6.0000"))


def test_issue_uses_average_and_keeps_value_consistent() -> None:
    lv = level()
    lv.receive(D("10"), D("5.00"))
    lv.receive(D("10"), D("7.00"))
    assert lv.issue(D("5")) == D("30.00")
    assert (lv.quantity, lv.value) == (D("15"), D("90.00"))


def test_issuing_everything_zeroes_value_exactly_even_with_rounding() -> None:
    lv = level()
    lv.receive(D("3"), D("3.3333"))  # 10.00 arrondi
    out1 = lv.issue(D("1"))
    out2 = lv.issue(D("1"))
    out3 = lv.issue(D("1"))
    assert out1 + out2 + out3 == D("10.00")
    assert (lv.quantity, lv.value) == (D("0"), D("0.00"))


def test_issue_more_than_available_is_refused_without_side_effect() -> None:
    lv = level()
    lv.receive(D("2"), D("1.00"))
    with pytest.raises(StockConflictError, match="insuffisant"):
        lv.issue(D("3"))
    assert (lv.quantity, lv.value) == (D("2"), D("2.00"))
    with pytest.raises(StockValidationError):
        lv.issue(D("0"))


def test_revalue_changes_value_not_quantity() -> None:
    lv = level()
    lv.receive(D("10"), D("5.00"))
    lv.revalue(D("20.00"))
    assert (lv.quantity, lv.value, lv.average_cost) == (D("10"), D("70.00"), D("7.0000"))
    with pytest.raises(StockConflictError):
        level().revalue(D("1.00"))
    with pytest.raises(StockConflictError, match="negative"):
        lv.revalue(D("-100.00"))


def test_adjust_to_variances() -> None:
    lv = level()
    lv.receive(D("10"), D("5.00"))
    assert lv.adjust_to(D("10"), None) == (D("0"), D("0.00"))
    assert lv.adjust_to(D("8"), None) == (D("-2"), D("-10.00"))
    assert lv.adjust_to(D("12"), None) == (D("4"), D("20.00"))
    assert (lv.quantity, lv.value) == (D("12"), D("60.00"))


def test_adjust_up_from_empty_requires_a_cost() -> None:
    lv = level()
    with pytest.raises(StockValidationError, match="Cout unitaire requis"):
        lv.adjust_to(D("3"), None)
    assert lv.adjust_to(D("3"), D("2.00")) == (D("3"), D("6.00"))


def test_level_rejects_negative_state() -> None:
    with pytest.raises(StockValidationError):
        StockLevel("A", "M", Currency.USD, quantity=D("-1"))
    with pytest.raises(StockValidationError):
        StockLevel("A", "M", Currency.USD, value=D("-1"))


# -- mouvements ------------------------------------------------------------------------------


def movement(kind: MovementType, qty: str, value: str, **kw) -> StockMovement:
    base = {
        "id": "m1",
        "number": "MVT-2026-000001",
        "movement_type": kind,
        "item_code": "A",
        "warehouse_code": "MAIN",
        "movement_date": date(2026, 11, 20),
        "quantity_delta": D(qty),
        "value_delta": D(value),
        "currency": Currency.USD,
        "quantity_after": D("10"),
        "value_after": D("50.00"),
        "source_type": "manual",
        "source_ref": "R1",
        "created_at": datetime(2026, 11, 20, tzinfo=UTC),
    }
    return StockMovement(**{**base, **kw})


@pytest.mark.parametrize(
    "kind,qty,value",
    [
        (MovementType.RECEIPT, "-1", "1"),
        (MovementType.RECEIPT, "1", "-1"),
        (MovementType.TRANSFER_IN, "0", "0"),
        (MovementType.ISSUE, "1", "-1"),
        (MovementType.ISSUE, "-1", "1"),
        (MovementType.TRANSFER_OUT, "1", "0"),
        (MovementType.ADJUSTMENT, "0", "0"),
        (MovementType.ADJUSTMENT, "2", "-1"),
        (MovementType.ADJUSTMENT, "-2", "1"),
        (MovementType.REVALUATION, "1", "1"),
        (MovementType.REVALUATION, "0", "-1"),
    ],
)
def test_movement_rejects_incoherent_signs(kind, qty, value) -> None:
    with pytest.raises(StockValidationError):
        movement(kind, qty, value)


def test_movement_accepts_coherent_signs_and_exposes_cost_and_key() -> None:
    m = movement(MovementType.ISSUE, "-4", "-20.00")
    assert m.unit_cost == D("5")
    assert m.idempotency_key == ("manual", "R1", "", "ISSUE")
    assert movement(MovementType.ADJUSTMENT, "-2", "0").quantity_delta == D("-2")
    reval = movement(MovementType.REVALUATION, "0", "0")
    assert reval.unit_cost is None


def test_movement_requires_identity_fields_and_non_negative_balance() -> None:
    with pytest.raises(StockValidationError):
        movement(MovementType.RECEIPT, "1", "1", source_ref=" ")
    with pytest.raises(StockValidationError):
        movement(MovementType.RECEIPT, "1", "1", quantity_after=D("-1"))


# -- inventaire -----------------------------------------------------------------------------


def test_inventory_count_lifecycle() -> None:
    count = InventoryCount("C1", "MAIN", date(2026, 11, 20))
    with pytest.raises(StockValidationError, match="aucune ligne comptee"):
        count.mark_validated("INV-2026-00001")
    line = count.set_count("A", D("8"), D("10"))
    assert (line.theoretical_quantity, line.counted_quantity) == (D("10"), D("8"))
    count.set_count("A", D("9"), D("99"))  # correction : la ligne existante est conservee
    assert len(count.lines) == 1 and count.lines[0].counted_quantity == D("9")
    assert count.lines[0].theoretical_quantity == D("10")
    count.mark_validated("INV-2026-00001")
    assert (count.status, count.number) == (InventoryStatus.VALIDATED, "INV-2026-00001")
    for action in (lambda: count.set_count("B", D("1"), D("0")), count.cancel):
        with pytest.raises(StockConflictError):
            action()


def test_inventory_rejects_duplicate_lines_and_negative_count() -> None:
    from vorpal.modules.stock.domain.inventory import InventoryLine

    with pytest.raises(StockValidationError):
        InventoryCount(
            "C", "MAIN", date(2026, 1, 1),
            lines=[InventoryLine("A", D("1")), InventoryLine("A", D("2"))],
        )
    count = InventoryCount("C", "MAIN", date(2026, 1, 1))
    with pytest.raises(StockValidationError):
        count.set_count("A", D("-1"), D("0"))


# -- reglages -------------------------------------------------------------------------------


def test_settings_defaults() -> None:
    s = StockSettings.from_json_file(None)
    assert s.default_currency is Currency.USD and s.auto_create_missing_masters is False
    assert s.sequence("movement") == SequenceConfig("MVT", "-", 6)
    assert s.sequence("inventory").prefix == "INV"


def test_settings_json_overrides_and_rejects_typos(tmp_path: Path) -> None:
    ok = tmp_path / "ok.json"
    ok.write_text(
        json.dumps(
            {
                "default_currency": "CDF",
                "auto_create_missing_masters": True,
                "sequences": {"movement": {"prefix": "STK", "padding": 4}},
                "metadata": {"status": "A_VALIDER"},
            }
        ),
        encoding="utf-8",
    )
    s = StockSettings.from_json_file(ok)
    assert s.default_currency is Currency.CDF and s.auto_create_missing_masters is True
    assert s.sequence("movement") == SequenceConfig("STK", "-", 4)
    assert s.sequence("inventory").prefix == "INV"  # non surchargee : valeur par defaut

    def bad(content: object) -> Path:
        path = tmp_path / "bad.json"
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    for content in (
        "{not json",
        "[]",
        {"default_curency": "USD"},
        {"default_currency": "EUR"},
        {"default_quantity_decimals": True},
        {"default_quantity_decimals": 9},
        {"auto_create_missing_masters": "oui"},
        {"sequences": []},
        {"sequences": {"autre": {"prefix": "X"}}},
        {"sequences": {"movement": {"prefix": ""}}},
        {"sequences": {"movement": {"nope": 1}}},
    ):
        with pytest.raises(ValidationError):
            StockSettings.from_json_file(bad(content))
    with pytest.raises(ValidationError, match="introuvable"):
        StockSettings.from_json_file(tmp_path / "absent.json")
