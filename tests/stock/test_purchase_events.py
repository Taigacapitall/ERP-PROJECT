"""Integration par evenements : le bus transporte des charges utiles identiques a celles d'Achats."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from tests.stock.helpers import (
    DAY,
    Env,
    build_env,
    landed_payload,
    publish_achats,
    receipt_payload,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
from vorpal.core.money import Currency
from vorpal.modules.stock.adapters.persistence.memory_repositories import (
    InMemoryStockUnitOfWork,
)
from vorpal.modules.stock.domain.errors import StockNotFoundError, StockValidationError
from vorpal.modules.stock.domain.movements import MovementType
from vorpal.modules.stock.manifest import (
    EVENT_GOODS_RECEIPT_VALIDATED,
    EVENT_LANDED_COST_ALLOCATED,
    EVENT_LANDED_COST_NOT_CAPITALIZED,
    EVENT_MOVEMENT_RECORDED,
)
from vorpal.modules.stock.services.movements import QuantityLine
from vorpal.modules.stock.settings import StockSettings

D = Decimal


@pytest.fixture(autouse=True)
def quiet_bus_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def failures(env: Env) -> list[str]:
    return [str(e.payload["error"]) for e in env.events_of(EVENT_HANDLER_FAILED)]


def test_goods_receipt_event_enters_stock_at_line_cost(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload(lines=[("L1", "SAC-CIMENT", "10", "5.00")]))
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("10"), D("50.00"))
    move = env.queries.stock_card(env.reader, "SAC-CIMENT")[0]
    assert move.movement_type is MovementType.RECEIPT
    assert (move.source_type, move.source_ref, move.source_line_ref) == ("goods_receipt", "REC-1", "L1")
    assert move.actor == "system" and move.movement_date == DAY
    assert failures(env) == []
    assert any(e.user == "system:achats" and e.action == "RECEIVE" for e in env.audit.entries)


def test_replay_and_republication_never_duplicate_stock(env: Env) -> None:
    env.seed()
    event = publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, env_payload := receipt_payload())
    env.bus.publish(event)  # republication du meme evenement
    env.bus.replay()  # rejeu complet
    # Un NOUVEL evenement portant la meme reception (autre id d'evenement) est aussi ignore
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, dict(env_payload))
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")
    assert len(env.events_of(EVENT_MOVEMENT_RECORDED)) == 1  # pas de re-publication sur rejeu
    assert failures(env) == []


def test_multi_line_receipt_and_two_items(env: Env) -> None:
    env.seed()
    env.items.create_item(env.clerk, "VIS", "Vis", unit="boite", quantity_decimals=0)
    publish_achats(
        env, EVENT_GOODS_RECEIPT_VALIDATED,
        receipt_payload(lines=[("L1", "SAC-CIMENT", "10", "5.00"), ("L2", "VIS", "100", "0.10")]),
    )
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")
    assert env.queries.get_level(env.reader, "VIS", "MAIN").value == D("10.00")


def test_unknown_item_fails_visibly_then_succeeds_after_creation_and_replay(env: Env) -> None:
    env.warehouses.create_warehouse(env.admin, "MAIN", "Principal")
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    assert len(failures(env)) == 1 and "inconnu du stock" in failures(env)[0]
    assert env.queries.valuation(env.reader) == {}
    env.items.create_item(env.clerk, "SAC-CIMENT", "Ciment", unit="sac")
    env.bus.replay(EVENT_GOODS_RECEIPT_VALIDATED)
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")


def test_failed_receipt_is_all_or_nothing(env: Env) -> None:
    env.seed()
    publish_achats(
        env, EVENT_GOODS_RECEIPT_VALIDATED,
        receipt_payload(lines=[("L1", "SAC-CIMENT", "10", "5.00"), ("L2", "FANTOME", "1", "1.00")]),
    )
    assert len(failures(env)) == 1
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("0")


def test_unknown_warehouse_and_currency_mismatch_are_refused(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload("REC-A", warehouse="AILLEURS"))
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload("REC-B", currency="CDF"))
    msgs = failures(env)
    assert "Entrepot 'AILLEURS' inconnu" in msgs[0] and "valorise en USD" in msgs[1]
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("0")


def test_inactive_item_refuses_receipt(env: Env) -> None:
    env.seed()
    env.items.deactivate_item(env.admin, "SAC-CIMENT")
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    assert "desactive" in failures(env)[0]


def test_auto_create_missing_masters_when_enabled() -> None:
    shared = InMemoryStockUnitOfWork()
    env = build_env(lambda: shared, StockSettings(auto_create_missing_masters=True, default_quantity_decimals=0))
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload(warehouse="NEUF"))
    item = env.queries.get_item(env.admin, "SAC-CIMENT")
    assert (item.name, item.unit, item.currency, item.quantity_decimals) == ("SAC-CIMENT", "unite", Currency.USD, 0)
    assert env.queries.get_warehouse(env.admin, "NEUF").name == "NEUF"
    assert env.queries.on_hand(env.admin, "SAC-CIMENT") == D("10")


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda p: p.pop("goods_receipt_id"), "goods_receipt_id"),
        (lambda p: p.update(lines=[]), "lines"),
        (lambda p: p.update(lines=["x"]), "mal formee"),
        (lambda p: p.update(receipt_date="hier"), "date ISO"),
        (lambda p: p.update(currency="EUR"), "devise inconnue"),
        (lambda p: p["lines"][0].update(quantity_received="abc"), "n'est pas un nombre"),
        (lambda p: p["lines"][0].update(quantity_received=1.5), "type invalide"),
        (lambda p: p["lines"][0].update(quantity_received="0"), "strictement positive"),
        (lambda p: p["lines"][0].update(unit_cost_ht="-1"), "negatif"),
        (lambda p: p["lines"][0].pop("line_id"), "line_id"),
    ],
)
def test_malformed_payloads_are_rejected_without_effect(env: Env, mutate, expected) -> None:
    env.seed()
    payload = receipt_payload()
    mutate(payload)
    with pytest.raises(StockValidationError, match=expected):
        env.ingestion.ingest_goods_receipt(payload)
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("0")


# -- frais d'approche -----------------------------------------------------------------------


def test_landed_cost_capitalizes_on_remaining_stock(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())  # 10 @ 5 = 50
    publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload(allocations=[("L1", "SAC-CIMENT", "20.00")]))
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value, level.average_cost) == (D("10"), D("70.00"), D("7.0000"))
    reval = env.queries.stock_card(env.reader, "SAC-CIMENT")[-1]
    assert reval.movement_type is MovementType.REVALUATION
    assert (reval.quantity_delta, reval.value_delta, reval.value_after) == (D("0"), D("20.00"), D("70.00"))
    assert (reval.source_type, reval.source_ref, reval.source_line_ref) == ("landed_cost", "LC-1", "L1")
    assert failures(env) == []


def test_landed_cost_is_idempotent_and_documents_accumulate(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    ev = publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload("LC-1", allocations=[("L1", "SAC-CIMENT", "20.00")]))
    env.bus.publish(ev)
    env.bus.replay()
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("70.00")
    publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload("LC-2", allocations=[("L1", "SAC-CIMENT", "10.00")]))
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("80.00")  # cumulatif


def test_landed_cost_follows_the_warehouse_of_the_receipt(env: Env) -> None:
    env.seed()
    env.warehouses.create_warehouse(env.admin, "DEPOT-B", "B")
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload(warehouse="DEPOT-B"))
    publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload())
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "DEPOT-B").value == D("70.00")
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("0.00")


def test_landed_cost_before_receipt_fails_then_succeeds_on_replay(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload())
    assert "pas encore en stock" in failures(env)[0]
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    env.bus.replay(EVENT_LANDED_COST_ALLOCATED)
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("70.00")


def test_landed_cost_with_no_remaining_stock_is_flagged_not_lost_silently(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    env.movements.issue_stock(env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 10)])
    publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload())
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("0"), D("0.00"))
    reval = env.queries.stock_card(env.reader, "SAC-CIMENT")[-1]
    assert reval.value_delta == D("0.00") and reval.reason.startswith("NON_CAPITALISE")
    alert = env.events_of(EVENT_LANDED_COST_NOT_CAPITALIZED)
    assert len(alert) == 1 and alert[0].payload["landed_cost_id"] == "LC-1"
    env.bus.replay()  # le rejeu n'emet pas une seconde alerte
    assert len(env.events_of(EVENT_LANDED_COST_NOT_CAPITALIZED)) == 1


def test_landed_cost_guards(env: Env) -> None:
    env.seed()
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    with pytest.raises(StockValidationError, match="negatif"):
        env.ingestion.ingest_landed_cost(landed_payload(allocations=[("L1", "SAC-CIMENT", "-1.00")]))
    with pytest.raises(StockValidationError, match="valorise en USD"):
        env.ingestion.ingest_landed_cost(landed_payload(currency="CDF"))
    with pytest.raises(StockNotFoundError):
        env.ingestion.ingest_landed_cost(landed_payload(receipt_id="REC-INCONNUE"))
    assert env.ingestion.ingest_landed_cost(landed_payload("LC-0", allocations=[("L1", "SAC-CIMENT", "0")])) == []
    assert env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN").value == D("50.00")


def test_disabled_handlers_ignore_events_after_stop(env: Env) -> None:
    env.seed()
    env.receipt_handler.enabled = False
    env.landed_handler.enabled = False
    publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    publish_achats(env, EVENT_LANDED_COST_ALLOCATED, landed_payload())
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("0")
    assert failures(env) == []
