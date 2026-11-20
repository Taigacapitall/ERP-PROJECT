from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from vorpal.core.audit import AuditLog
from vorpal.core.config import AppConfig
from vorpal.core.events import EventBus
from vorpal.core.security import AuthorizationService
from vorpal.core.services import ServiceRegistry

Clock = Callable[[], datetime]


def utc_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AppContext:
    event_bus: EventBus
    services: ServiceRegistry
    config: AppConfig
    db: Engine
    audit: AuditLog
    authz: AuthorizationService
    clock: Clock = utc_clock
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("vorpal"))
