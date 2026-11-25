"""Fixtures : module Comptabilité réel (SQLite en mémoire, EventBus réel)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from vorpal.core import (
    AppConfig,
    AppContext,
    AuditLog,
    AuthorizationService,
    Event,
    EventBus,
    InMemoryEventStore,
    ModuleRegistry,
    ModuleState,
    ServiceRegistry,
    User,
    create_db_engine,
)
from vorpal.modules.comptabilite.domain.entries import DraftEntry, DraftLine
from vorpal.modules.comptabilite.module import ComptabiliteModule

NOW = datetime(2026, 11, 24, 10, 0, 0, tzinfo=UTC)
D = Decimal


@dataclass
class Env:
    ctx: AppContext
    module: ComptabiliteModule
    posted_events: list[Event]
    admin: User
    reader: User

    def post(self, ref: str, day: date, lines: list[tuple[str, str, str]], journal: str = "OD", currency: str = "USD"):
        assert self.module.posting is not None
        draft = DraftEntry(
            source_ref=ref,
            journal_code=journal,
            entry_date=day,
            currency=currency,
            lines=tuple(DraftLine(a, D(d), D(c)) for a, d, c in lines),
            label=ref,
        )
        return self.module.posting.post(draft)


def _build(clock: datetime = NOW, db_url: str = "sqlite:///:memory:") -> Env:
    engine = create_db_engine(db_url)
    bus = EventBus(store=InMemoryEventStore())
    seen: list[Event] = []
    bus.subscribe("journal.entry.posted", seen.append)
    ctx = AppContext(
        event_bus=bus,
        services=ServiceRegistry(),
        config=AppConfig(env="test", db_url=db_url),
        db=engine,
        audit=AuditLog(),
        authz=AuthorizationService(),
        clock=lambda: clock,
    )
    counter = iter(range(1, 10_000))
    prefix = uuid4().hex[:6]  # unique par instanciation : un redémarrage ne réutilise pas les identifiants
    module = ComptabiliteModule(id_factory=lambda: f"{prefix}-{next(counter):05d}")
    registry = ModuleRegistry()
    registry.register(module)
    registry.start_all(ctx)
    assert registry.get_state("comptabilite") == ModuleState.RUNNING
    admin = User(id="U1", username="admin", direct_permissions=frozenset({"compta.view", "compta.post", "compta.admin"}))
    reader = User(id="U2", username="reader", direct_permissions=frozenset({"compta.view"}))
    return Env(ctx, module, seen, admin, reader)


@pytest.fixture
def env() -> Env:
    return _build()
