"""Tests des migrations de schéma du module Ventes."""

from sqlalchemy import Column, Integer, Table, create_engine, inspect

from vorpal.core import Base
from vorpal.modules.ventes.adapters.persistence.migrations import (
    MIGRATIONS,
    create_schema_v1,
)


def test_create_schema_v1_creates_only_sales_tables() -> None:
    engine = create_engine("sqlite:///:memory:")

    unrelated_table = Table(
        "autre_module_test",
        Base.metadata,
        Column("id", Integer, primary_key=True),
    )

    try:
        create_schema_v1(engine)

        created_tables = set(inspect(engine).get_table_names())
        expected_sales_tables = {
            table.name
            for table in Base.metadata.sorted_tables
            if table.name.startswith("ventes_")
        }

        assert expected_sales_tables
        assert created_tables == expected_sales_tables
        assert "autre_module_test" not in created_tables

    finally:
        unrelated_table.metadata.remove(unrelated_table)
        engine.dispose()


def test_create_schema_v1_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")

    try:
        create_schema_v1(engine)
        first_result = set(inspect(engine).get_table_names())

        create_schema_v1(engine)
        second_result = set(inspect(engine).get_table_names())

        assert first_result
        assert second_result == first_result

    finally:
        engine.dispose()


def test_migrations_registers_version_one() -> None:
    assert 1 in MIGRATIONS
    assert MIGRATIONS[1] is create_schema_v1
    assert len(MIGRATIONS) == 1
