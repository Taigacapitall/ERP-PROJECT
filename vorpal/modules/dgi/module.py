"""Point d'assemblage (composition root) du module DGI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from vorpal.core.context import AppContext
from vorpal.core.contracts.events import INVOICE_READY_FOR_CERTIFICATION
from vorpal.core.db import SchemaMigrationManager
from vorpal.core.module import BaseModule, ModuleManifest
from vorpal.modules.dgi.clients.base import FiscalDeviceClient
from vorpal.modules.dgi.event_handlers import InvoiceReadyForCertificationHandler
from vorpal.modules.dgi.manifest import DGI_MANIFEST
from vorpal.modules.dgi.models import InvoiceDTO
from vorpal.modules.dgi.persistence import DGI_MIGRATIONS, SqlFiscalJournal, SqlOutboxRepository
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.service import CertificationService
from vorpal.modules.dgi.tax_groups import TaxGroupTable


class DgiModule(BaseModule):
    """Module DGI : client fiscal et table de taxes injectes explicitement (jamais de Mock implicite).

    Outbox et journal sont persistes en SQL (tables dgi_*, migration additive).
    """

    manifest: ModuleManifest = DGI_MANIFEST

    def __init__(
        self,
        device_client: FiscalDeviceClient,
        tax_table: TaxGroupTable,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._device_client = device_client
        self._tax_table = tax_table
        self._retry_policy = retry_policy
        self._session_factory: sessionmaker[Session] | None = None
        self.service: CertificationService | None = None
        self._handler: InvoiceReadyForCertificationHandler | None = None

    def install(self, ctx: AppContext) -> None:
        SchemaMigrationManager(ctx.db).apply("dgi", DGI_MIGRATIONS)
        self._session_factory = sessionmaker(bind=ctx.db)

    def start(self, ctx: AppContext) -> None:
        if self._session_factory is None:
            self.install(ctx)
        assert self._session_factory is not None
        factory = self._session_factory
        service = CertificationService(
            device_client=self._device_client,
            tax_table=self._tax_table,
            outbox=SqlOutboxRepository(factory),
            journal=SqlFiscalJournal(factory),
            event_bus=ctx.event_bus,
            audit=ctx.audit,
            retry_policy=self._retry_policy,
            clock=ctx.clock,
        )
        handler = InvoiceReadyForCertificationHandler(service)
        ctx.event_bus.subscribe(INVOICE_READY_FOR_CERTIFICATION, handler)
        ctx.services.register(CertificationService, service, replace=True)
        self.service = service
        self._handler = handler

    def process_retries(self, now: datetime | None = None) -> list[InvoiceDTO]:
        """Rejoue la file de reprise (a appeler depuis un planificateur)."""
        if self.service is None:
            return []
        return self.service.process_retry_queue(now)

    def stop(self) -> None:
        if self._handler is not None:
            self._handler.enabled = False
        self._handler = None
        self.service = None

    def healthcheck(self) -> bool:
        return self.service is not None and self._handler is not None and self._handler.enabled
