"""Dépendances communes des services de comptabilité."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from vorpal.core.audit import AuditLog
from vorpal.core.contracts import ExchangeRateProviderProtocol
from vorpal.modules.comptabilite.domain.entries import PostedEntry
from vorpal.modules.comptabilite.ports.repositories import LedgerUnitOfWork
from vorpal.modules.comptabilite.settings import AccountingSettings


@dataclass(frozen=True)
class AccountingDependencies:
    """Dépendances injectées (jamais instanciées en silence)."""

    uow_factory: Callable[[], LedgerUnitOfWork]
    clock: Callable[[], datetime]
    id_factory: Callable[[], str]
    settings: AccountingSettings
    rate_provider: ExchangeRateProviderProtocol | None = None
    audit: AuditLog | None = None
    # Appelé APRÈS commit (publication d'événement). Peut être None.
    on_posted: Callable[[PostedEntry], None] | None = None
