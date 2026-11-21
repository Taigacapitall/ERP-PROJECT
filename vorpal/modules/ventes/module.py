"""Point d'assemblage (composition root) du module Ventes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from vorpal.core.context import AppContext
from vorpal.core.contracts.events import INVOICE_CERTIFIED, INVOICE_REJECTED
from vorpal.core.db import SchemaMigrationManager
from vorpal.core.module import BaseModule, ModuleManifest
from vorpal.modules.ventes.adapters.events import EventBusSalesPublisher
from vorpal.modules.ventes.adapters.persistence.migrations import MIGRATIONS
from vorpal.modules.ventes.adapters.persistence.unit_of_work import SqlSalesUnitOfWork
from vorpal.modules.ventes.manifest import VENTES_MANIFEST
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.services.certification_results import CertificationResultHandlers
from vorpal.modules.ventes.services.common import ServiceDependencies
from vorpal.modules.ventes.services.invoices import InvoiceService
from vorpal.modules.ventes.settings import SalesSettings


class VentesModule(BaseModule):
    """Module Ventes : persistance SQL, publication post-commit et reception des resultats DGI."""

    manifest: ModuleManifest = VENTES_MANIFEST

    def __init__(
        self,
        settings_path: Path | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._settings_path = settings_path
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._settings: SalesSettings | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self.uow_factory: Callable[[], SalesUnitOfWork] | None = None
        self.invoice_service: InvoiceService | None = None
        self.result_handlers: CertificationResultHandlers | None = None

    def install(self, ctx: AppContext) -> None:
        self._settings = SalesSettings.load(self._settings_path)
        SchemaMigrationManager(ctx.db).apply("ventes", MIGRATIONS)
        self._session_factory = sessionmaker(bind=ctx.db)

    def start(self, ctx: AppContext) -> None:
        if self._settings is None or self._session_factory is None:
            self.install(ctx)
        assert self._settings is not None
        assert self._session_factory is not None
        settings = self._settings
        factory = self._session_factory

        def uow_factory() -> SalesUnitOfWork:
            return SqlSalesUnitOfWork(factory, settings)

        deps = ServiceDependencies(
            uow_factory=uow_factory,
            publisher=EventBusSalesPublisher(ctx.event_bus, ctx.clock),
            audit=ctx.audit,
            authz=ctx.authz,
            clock=ctx.clock,
            logger=ctx.logger,
            settings=settings,
            id_factory=self._id_factory,
        )
        handlers = CertificationResultHandlers(deps)
        ctx.event_bus.subscribe(INVOICE_CERTIFIED, handlers.on_certified)
        ctx.event_bus.subscribe(INVOICE_REJECTED, handlers.on_rejected)
        self.uow_factory = uow_factory
        self.invoice_service = InvoiceService(deps)
        self.result_handlers = handlers

    def stop(self) -> None:
        self.invoice_service = None
        self.result_handlers = None
        self.uow_factory = None

    def healthcheck(self) -> bool:
        return self.invoice_service is not None and self.result_handlers is not None
