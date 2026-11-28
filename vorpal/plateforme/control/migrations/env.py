"""Environnement Alembic : la connexion est fournie par ``control.migrate`` (ou l'URL de la config)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from vorpal.plateforme.control.models import ControlBase

config = context.config
target_metadata = ControlBase.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(config.get_main_option("sqlalchemy.url") or "")
    with engine.begin() as new_connection:
        _run(new_connection)


if context.is_offline_mode():
    raise RuntimeError("Le mode hors connexion (SQL seul) n'est pas pris en charge.")
run_migrations_online()
