"""Tests de la persistance SQL (necessitent SQLAlchemy) : schema, parite memoire/SQL, concurrence."""

from __future__ import annotations

import threading
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from tests.stock.helpers import (
    DAY,
    SCENARIO_EXPECTED,
    Env,
    build_env,
    publish_achats,
    receipt_payload,
    run_scenario,
    usd,
)
from vorpal.core.db import create_db_engine
from vorpal.modules.stock.adapters.persistence.migrations import (
    STOCK_SCHEMA_VERSION,
    apply_stock_migrations,
)
from vorpal.modules.stock.adapters.persistence.unit_of_work import SqlStockUnitOfWork
from vorpal.modules.stock.domain.errors import StockConflictError, StockNotFoundError
from vorpal.modules.stock.domain.movements import MovementType, StockMovement
from vorpal.modules.stock.manifest import EVENT_GOODS_RECEIPT_VALIDATED
from vorpal.modules.stock.services.movements import QuantityLine, ReceiptLine

D = Decimal


def sql_env(url: str = "sqlite:///:memory:") -> tuple[Env, object]:
    engine = create_db_engine(url)
    apply_stock_migrations(engine)
    factory = sessionmaker(bind=engine)
    return build_env(lambda: SqlStockUnitOfWork(factory)), engine


def test_migrations_create_only_stock_tables_and_are_idempotent() -> None:
    engine = create_db_engine("sqlite:///:memory:")
    assert apply_stock_migrations(engine) == STOCK_SCHEMA_VERSION
    assert apply_stock_migrations(engine) == STOCK_SCHEMA_VERSION
    tables = {t for t in inspect(engine).get_table_names() if t != "vorpal_schema_versions"}
    assert tables == {
        "stock_items",
        "stock_warehouses",
        "stock_levels",
        "stock_movements",
        "stock_inventory_counts",
        "stock_inventory_lines",
        "stock_sequences",
    }


def test_reference_scenario_gives_identical_results_on_sql() -> None:
    env, _ = sql_env()
    assert run_scenario(env) == SCENARIO_EXPECTED
    assert env.audit.verify_chain()


def test_decimals_round_trip_exactly() -> None:
    env, _ = sql_env()
    env.seed(quantity_decimals=3)
    env.movements.receive_stock(
        env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", "2.125", usd("3.3333"))]
    )
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("2.125"), D("7.08"))
    card = env.queries.stock_card(env.reader, "SAC-CIMENT")
    assert card[0].created_at.tzinfo is not None  # le fuseau survit a la base


def test_failed_operation_rolls_everything_back_including_numbers() -> None:
    env, _ = sql_env()
    env.seed()
    with pytest.raises(StockNotFoundError):
        env.movements.receive_stock(
            env.clerk, "MAIN", DAY,
            [ReceiptLine("SAC-CIMENT", 5, usd("1")), ReceiptLine("INCONNU", 1, usd("1"))],
        )
    assert env.queries.valuation(env.reader) == {}
    assert env.queries.stock_card(env.reader, "SAC-CIMENT") == []
    first = env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, usd("1"))])
    assert first[0].number == "MVT-2026-000001"  # aucun trou de numerotation


def test_unique_constraint_blocks_duplicate_source_even_if_service_check_is_bypassed() -> None:
    env, _ = sql_env()
    env.seed()
    env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, usd("1"))], reference="R1")
    with env.uow_factory() as uow:
        original = uow.movements.find_by_key("manual", "R1", "SAC-CIMENT", MovementType.RECEIPT)
        assert original is not None
        clone = StockMovement(
            id="AUTRE-ID", number="MVT-2026-999999", movement_type=original.movement_type,
            item_code=original.item_code, warehouse_code=original.warehouse_code,
            movement_date=original.movement_date, quantity_delta=original.quantity_delta,
            value_delta=original.value_delta, currency=original.currency,
            quantity_after=original.quantity_after, value_after=original.value_after,
            source_type="manual", source_ref="R1", created_at=original.created_at,
            source_line_ref="SAC-CIMENT",
        )
        with pytest.raises(StockConflictError):
            uow.movements.add(clone)


def test_receipt_event_idempotent_on_sql() -> None:
    env, _ = sql_env()
    env.seed()
    event = publish_achats(env, EVENT_GOODS_RECEIPT_VALIDATED, receipt_payload())
    env.bus.publish(event)
    env.bus.replay()
    assert env.queries.on_hand(env.reader, "SAC-CIMENT") == D("10")
    assert len(env.queries.stock_card(env.reader, "SAC-CIMENT")) == 1


def test_data_and_numbering_survive_a_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'stock.db'}"
    env, engine = sql_env(url)
    env.seed()
    env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 4, usd("2.50"))])
    engine.dispose()  # type: ignore[attr-defined]

    env2, engine2 = sql_env(url)
    level = env2.queries.get_level(env2.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("4"), D("10.00"))
    nxt = env2.movements.receive_stock(
        env2.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, usd("2.50"))], reference="BL-2"
    )
    assert nxt[0].number == "MVT-2026-000002"
    engine2.dispose()  # type: ignore[attr-defined]


def _run_threads(count: int, job) -> list[Exception | None]:
    results: list[Exception | None] = [None] * count
    barrier = threading.Barrier(count)

    def worker(index: int) -> None:
        barrier.wait()
        try:
            job(index)
        except Exception as exc:
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_issues_never_oversell_and_numbers_stay_unique(tmp_path: Path) -> None:
    env, engine = sql_env(f"sqlite:///{tmp_path / 'race.db'}")
    env.seed(quantity_decimals=0)
    env.movements.receive_stock(env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 8, usd("5.00"))])

    results = _run_threads(
        12,
        lambda i: env.movements.issue_stock(
            env.clerk, "MAIN", DAY, [QuantityLine("SAC-CIMENT", 1)], reference=f"VENTE-{i}"
        ),
    )
    ok = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, StockConflictError)]
    unexpected = [r for r in results if r is not None and not isinstance(r, StockConflictError)]
    assert unexpected == []
    assert (len(ok), len(refused)) == (8, 4)
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("0"), D("0.00"))
    numbers = [m.number for m in env.queries.stock_card(env.reader, "SAC-CIMENT")]
    assert len(numbers) == len(set(numbers)) == 9
    engine.dispose()  # type: ignore[attr-defined]


def test_concurrent_first_receipts_on_a_new_level_do_not_collide(tmp_path: Path) -> None:
    env, engine = sql_env(f"sqlite:///{tmp_path / 'first.db'}")
    env.seed(quantity_decimals=0)
    results = _run_threads(
        6,
        lambda i: env.movements.receive_stock(
            env.clerk, "MAIN", DAY, [ReceiptLine("SAC-CIMENT", 1, usd("2.00"))], reference=f"BL-{i}"
        ),
    )
    assert [r for r in results if r is not None] == []
    level = env.queries.get_level(env.reader, "SAC-CIMENT", "MAIN")
    assert (level.quantity, level.value) == (D("6"), D("12.00"))
    engine.dispose()  # type: ignore[attr-defined]
