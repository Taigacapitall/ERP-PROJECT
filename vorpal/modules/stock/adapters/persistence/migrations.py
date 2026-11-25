"""Migrations versionnees du schema de persistance du module Stock (uniquement tables stock_*)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.engine import Engine

from vorpal.core.db import Base, SchemaMigrationManager
from vorpal.modules.stock.adapters.persistence import models as _models  # noqa: F401

STOCK_SCHEMA_VERSION = 1


def create_schema_v1(engine: Engine) -> None:
    """Cree uniquement les tables stock_* de maniere idempotente."""
    stock_tables = [
        table for table in Base.metadata.sorted_tables if table.name.startswith("stock_")
    ]
    Base.metadata.create_all(bind=engine, tables=stock_tables, checkfirst=True)


STOCK_MIGRATIONS: dict[int, Callable[[Engine], None]] = {
    1: create_schema_v1,
}


def apply_stock_migrations(engine: Engine) -> int:
    """Applique les migrations du module Stock via SchemaMigrationManager."""
    return SchemaMigrationManager(engine).apply("stock", STOCK_MIGRATIONS)
