"""Service d'entree pour l'import de modeles de factures fournisseurs (CSV / Excel)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from vorpal.core.audit import AuditLog
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.achats.domain.errors import PurchaseValidationError
from vorpal.modules.achats.manifest import ACHATS_EVENTS_EMITTED
from vorpal.modules.achats.ports.extensions import (
    ImportedSupplierInvoiceTemplate,
    SupplierInvoiceTemplateImporter,
)
from vorpal.modules.achats.ports.publisher import PurchaseEventPublisher
from vorpal.modules.achats.services.common import (
    current_timestamp,
    record_audit,
    require_permission,
)


class SupplierInvoiceImportService:
    """Trace un modele de facture fournisseur importe (CSV/Excel) via le port configure."""

    def __init__(
        self,
        audit: AuditLog,
        authz: AuthorizationService,
        importer: SupplierInvoiceTemplateImporter | None = None,
        publisher: PurchaseEventPublisher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._audit = audit
        self._authz = authz
        self._importer = importer
        self._publisher = publisher
        self._clock = clock

    def import_template(
        self,
        actor: User,
        raw_content: bytes,
        filename: str,
        linked_purchase_order_number: str | None = None,
    ) -> ImportedSupplierInvoiceTemplate:
        require_permission(self._authz, actor, "achats.create")
        if not filename.strip():
            raise PurchaseValidationError("Le nom du fichier importe est obligatoire.")
        if not raw_content:
            raise PurchaseValidationError("Le contenu du fichier importe est vide.")
        if self._importer is None:
            # TODO(A_VALIDER): brancher le parseur CSV/Excel selon le modele de chaque organisation.
            raise PurchaseValidationError(
                "Aucun importateur de modele facture fournisseur (CSV/Excel) n'est configure."
            )

        imported = self._importer.import_template(
            raw_content=raw_content,
            filename=filename,
            linked_purchase_order_number=linked_purchase_order_number,
        )

        record_audit(
            self._audit,
            actor=actor,
            action="IMPORT_SUPPLIER_INVOICE_TEMPLATE",
            entity="supplier_invoice_template",
            entity_id=imported.source_filename,
            before=None,
            after={
                "source_filename": imported.source_filename,
                "file_format": imported.file_format,
                "headers_count": len(imported.headers),
                "rows_count": len(imported.rows),
                "linked_purchase_order_number": imported.linked_purchase_order_number,
            },
            clock=self._clock,
        )

        if self._publisher is not None:
            self._publisher.publish_event(
                event_type=ACHATS_EVENTS_EMITTED[3],
                payload={
                    "source_filename": imported.source_filename,
                    "file_format": imported.file_format,
                    "headers": imported.headers,
                    "rows_count": len(imported.rows),
                    "linked_purchase_order_number": imported.linked_purchase_order_number,
                    "imported_at": current_timestamp(self._clock).isoformat(),
                },
                correlation_id=imported.linked_purchase_order_number or imported.source_filename,
            )

        return imported
