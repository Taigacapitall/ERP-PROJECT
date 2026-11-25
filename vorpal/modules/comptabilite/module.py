"""Point d'assemblage (composition root) du module Comptabilité native."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from vorpal.core.context import AppContext
from vorpal.core.contracts import AccountingPoster, ExchangeRateProviderProtocol
from vorpal.core.contracts.events import (
    CREDIT_NOTE_CERTIFIED,
    INVOICE_CERTIFIED,
    JOURNAL_ENTRY_POSTED,
    PAYMENT_RECEIVED,
)
from vorpal.core.db import SchemaMigrationManager
from vorpal.core.events import Event
from vorpal.core.module import BaseModule, ModuleManifest
from vorpal.modules.comptabilite.adapters.core_poster import CoreLedgerPoster
from vorpal.modules.comptabilite.adapters.persistence.sql import MIGRATIONS, SqlLedgerUnitOfWork
from vorpal.modules.comptabilite.domain.entries import PostedEntry
from vorpal.modules.comptabilite.events import (
    CLASSIC_CREDIT_NOTE_ISSUED,
    CLASSIC_INVOICE_ISSUED,
    PAYMENT_CANCELLED,
    AccountingEventHandlers,
)
from vorpal.modules.comptabilite.manifest import COMPTA_MANIFEST
from vorpal.modules.comptabilite.mapping import AccountMapping
from vorpal.modules.comptabilite.ports.repositories import LedgerUnitOfWork
from vorpal.modules.comptabilite.services.accounting import AccountingService
from vorpal.modules.comptabilite.services.chart import ChartService
from vorpal.modules.comptabilite.services.closing import ClosingService
from vorpal.modules.comptabilite.services.common import AccountingDependencies
from vorpal.modules.comptabilite.services.posting import PostingService
from vorpal.modules.comptabilite.services.reports import ReportService
from vorpal.modules.comptabilite.settings import AccountingSettings


class ComptabiliteModule(BaseModule):
    """Grand livre natif. Le fournisseur de taux est injecté explicitement (optionnel)."""

    manifest: ModuleManifest = COMPTA_MANIFEST

    def __init__(
        self,
        settings: AccountingSettings | None = None,
        rate_provider: ExchangeRateProviderProtocol | None = None,
        id_factory: Callable[[], str] | None = None,
        mapping_path: Path | None = None,
    ) -> None:
        self._mapping_path = mapping_path
        self._settings = settings or AccountingSettings()
        self._rate_provider = rate_provider
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._session_factory: sessionmaker[Session] | None = None
        self.uow_factory: Callable[[], LedgerUnitOfWork] | None = None
        self.chart: ChartService | None = None
        self.posting: PostingService | None = None
        self.reports: ReportService | None = None
        self.closing: ClosingService | None = None
        self.service: AccountingService | None = None
        self.handlers: AccountingEventHandlers | None = None

    def _deps(self, ctx: AppContext) -> AccountingDependencies:
        if self._session_factory is None:
            self._session_factory = sessionmaker(bind=ctx.db)
        factory = self._session_factory

        def uow_factory() -> LedgerUnitOfWork:
            return SqlLedgerUnitOfWork(factory)

        def on_posted(entry: PostedEntry) -> None:
            ctx.event_bus.publish(
                Event(
                    type=JOURNAL_ENTRY_POSTED,
                    payload={
                        "source_ref": entry.source_ref,
                        "entry_id": entry.id,
                        "number": entry.number,
                        "journal_code": entry.journal_code,
                        "entry_date": entry.entry_date.isoformat(),
                    },
                    occurred_at=ctx.clock(),
                    correlation_id=entry.source_ref,
                    source_module="comptabilite",
                )
            )

        self.uow_factory = uow_factory
        return AccountingDependencies(
            uow_factory=uow_factory,
            clock=ctx.clock,
            id_factory=self._id_factory,
            settings=self._settings,
            rate_provider=self._rate_provider,
            audit=ctx.audit,
            on_posted=on_posted,
        )

    def install(self, ctx: AppContext) -> None:
        SchemaMigrationManager(ctx.db).apply("comptabilite", MIGRATIONS)
        ChartService(self._deps(ctx)).install_defaults()

    def start(self, ctx: AppContext) -> None:
        deps = self._deps(ctx)
        chart = ChartService(deps)
        posting = PostingService(deps)
        reports = ReportService(deps)
        closing = ClosingService(deps, posting)
        self.chart, self.posting, self.reports, self.closing = chart, posting, reports, closing
        self.service = AccountingService(deps, ctx.authz, posting, reports, chart, closing)
        ctx.services.register(cast(type[object], AccountingPoster), CoreLedgerPoster(posting, chart))
        handlers = AccountingEventHandlers(deps, posting, AccountMapping.load(self._mapping_path))
        for event_type in (INVOICE_CERTIFIED, CREDIT_NOTE_CERTIFIED):
            ctx.event_bus.subscribe(event_type, handlers.on_dgi_certified)
        for event_type in (CLASSIC_INVOICE_ISSUED, CLASSIC_CREDIT_NOTE_ISSUED):
            ctx.event_bus.subscribe(event_type, handlers.on_classic_document)
        ctx.event_bus.subscribe(PAYMENT_RECEIVED, handlers.on_payment_received)
        ctx.event_bus.subscribe(PAYMENT_CANCELLED, handlers.on_payment_cancelled)
        self.handlers = handlers

    def stop(self) -> None:
        self.chart = self.posting = self.reports = self.closing = self.service = self.handlers = None

    def healthcheck(self) -> bool:
        return self.service is not None
