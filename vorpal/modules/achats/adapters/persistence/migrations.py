"""Migrations versionnees du schema de persistance du module Achats."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from vorpal.core.db import Base, SchemaMigrationManager
from vorpal.modules.achats.adapters.persistence import models as _models  # noqa: F401

ACHATS_SCHEMA_VERSION = 1


def _migrate_v1(engine: Engine) -> None:
    Base.metadata.create_all(engine)


ACHATS_MIGRATIONS: dict[int, object] = {
    1: _migrate_v1,
}


def apply_achats_migrations(engine: Engine) -> int:
    """Applique les migrations du module Achats via SchemaMigrationManager."""
    manager = SchemaMigrationManager(engine)
    return manager.apply("achats", {1: _migrate_v1})
