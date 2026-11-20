"""Migrations de schema versionnees pour le module Ventes (uniquement tables ventes_*)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.engine import Engine

from vorpal.core import Base
from vorpal.modules.ventes.adapters.persistence import models as _models  # noqa: F401


def create_schema_v1(engine: Engine) -> None:
    """Cree uniquement les tables ventes_* de maniere idempotente."""
    ventes_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name.startswith("ventes_")
    ]
    Base.metadata.create_all(bind=engine, tables=ventes_tables, checkfirst=True)


MIGRATIONS: dict[int, Callable[[Engine], None]] = {
    1: create_schema_v1,
}
