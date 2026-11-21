"""Service de certification fiscale DGI (implemente FiscalCertifier du core)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from vorpal.core.audit import AuditLog
from vorpal.core.compliance import FiscalCertifier
from vorpal.core.errors import ComplianceError
from vorpal.core.events import Event, EventBus
from vorpal.modules.dgi.clients.base import FiscalDeviceClient
from vorpal.modules.dgi.credit_note import CreditNoteRequest
from vorpal.modules.dgi.errors_map import DgiErrorTranslator
from vorpal.modules.dgi.journal import FiscalJournal
from vorpal.modules.dgi.manifest import DGI_EVENTS_EMITTED
from vorpal.modules.dgi.models import (
    CertificationStatus,
    InvoiceDTO,
    ensure_invoice_valid_for_submission,
)
from vorpal.modules.dgi.outbox import OutboxEntry, OutboxRepository
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.state_machine import ensure_not_certified_immutable, transition_invoice_status
from vorpal.modules.dgi.tax_groups import TaxGroupTable


class CertificationService(FiscalCertifier):
    """Orchestre la validation, la certification, l'outbox/reprise, les avoirs et le journal DGI."""

    def __init__(
        self,
        device_client: FiscalDeviceClient,
        tax_table: TaxGroupTable,
        outbox: OutboxRepository,
        journal: FiscalJournal,
        event_bus: EventBus | None = None,
        audit: AuditLog | None = None,
        retry_policy: RetryPolicy | None = None,
        error_translator: DgiErrorTranslator | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._device = device_client
        self._tax_table = tax_table
        self._outbox = outbox
        self._journal = journal
        self._event_bus = event_bus
        self._audit = audit
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._translator = error_translator if error_translator is not None else DgiErrorTranslator()
        self._clock = clock
        self._invoices_by_id: dict[str, InvoiceDTO] = {}

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(timezone.utc)

    def _emit(self, event_type: str, payload: dict[str, object], correlation_id: str | None = None) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            Event(
                type=event_type,
                payload=payload,
                occurred_at=self._now(),
                correlation_id=correlation_id,
                source_module="dgi",
            )
        )

    def _record_audit(
        self,
        action: str,
        entity_id: str,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        if self._audit is None:
            return
        self._audit.record(
            user="system.dgi",
            action=action,
            entity="dgi_invoice",
            entity_id=entity_id,
            before=before,
            after=after,
            timestamp=self._now(),
        )

    def _validate_tax_groups(self, invoice: InvoiceDTO) -> None:
        for line in invoice.lines:
            group = self._tax_table.resolve_group(line.tax_group_code, invoice.issue_date)
            if group.out_of_scope != line.out_of_scope:
                raise ComplianceError(
                    f"Incoherence 'hors champ' sur la ligne '{line.line_id}' pour le groupe '{group.code}'."
                )
            if group.out_of_scope and not (line.item_code or "").strip():
                raise ComplianceError(
                    f"Ligne hors champ '{line.line_id}' exige un code article."
                )

    def enqueue_invoice(self, invoice: InvoiceDTO) -> OutboxEntry:
        """Enregistre une facture dans l'outbox de facon idempotente par numero de serie."""
        self._invoices_by_id[invoice.invoice_id] = invoice
        return self._outbox.enqueue(invoice, now=self._now())

    def certify_invoice(self, invoice: InvoiceDTO) -> InvoiceDTO:
        """Valide, soumet et certifie une facture (ou avoir), avec idempotence par numero de serie."""
        now = self._now()
        self._invoices_by_id[invoice.invoice_id] = invoice

        existing_entry = self._outbox.get(invoice.serial_number)
        if existing_entry is not None and existing_entry.status == CertificationStatus.CERTIFIED:
            invoice.status = CertificationStatus.CERTIFIED
            invoice.certification = existing_entry.invoice.certification
            return invoice

        entry = existing_entry if existing_entry is not None else self._outbox.enqueue(invoice, now=now)
        ensure_not_certified_immutable(entry.invoice, operation="certification")

        # 1. Validation des 12 mentions, coherence montants et groupes de taxe
        try:
            ensure_invoice_valid_for_submission(invoice)
            self._validate_tax_groups(invoice)
        except ComplianceError as exc:
            transition_invoice_status(invoice, CertificationStatus.REJECTED)
            entry.invoice = invoice
            entry.status = CertificationStatus.REJECTED
            entry.last_error_code = "VALIDATION_FAILED"
            entry.last_error_message = str(exc)
            entry.updated_at = now
            self._outbox.save(entry)
            self._journal.append(
                invoice_id=invoice.invoice_id,
                serial_number=invoice.serial_number,
                status=CertificationStatus.REJECTED,
                request_payload={"invoice_id": invoice.invoice_id, "serial_number": invoice.serial_number},
                response_payload={"error": str(exc)},
                timestamp=now,
            )
            self._emit(
                DGI_EVENTS_EMITTED[1],
                {
                    "invoice_id": invoice.invoice_id,
                    "serial_number": invoice.serial_number,
                    "reason": str(exc),
                },
                correlation_id=invoice.serial_number,
            )
            raise

        # 2. Passage a SENT et envoi au dispositif fiscal
        transition_invoice_status(invoice, CertificationStatus.SENT)
        entry.attempts += 1
        entry.updated_at = now

        response = self._device.submit(invoice)
        translated_msg = self._translator.translate(response.error_code, response.error_message or "")

        if response.accepted and response.certification is not None:
            transition_invoice_status(
                invoice,
                CertificationStatus.CERTIFIED,
                certification_data=response.certification,
            )
            entry.invoice = invoice
            entry.status = CertificationStatus.CERTIFIED
            entry.last_error_code = None
            entry.last_error_message = None
            entry.updated_at = now
            self._outbox.save(entry)

            self._journal.append(
                invoice_id=invoice.invoice_id,
                serial_number=invoice.serial_number,
                status=CertificationStatus.CERTIFIED,
                request_payload=response.raw_request or {"serial_number": invoice.serial_number},
                response_payload=response.raw_response or {
                    "fiscal_device_id": response.certification.fiscal_device_id,
                    "authentication_code": response.certification.authentication_code,
                    "qr_code": response.certification.qr_code,
                },
                timestamp=now,
            )
            self._record_audit(
                action="CERTIFY_INVOICE",
                entity_id=invoice.invoice_id,
                before={"status": "SENT"},
                after={
                    "status": "CERTIFIED",
                    "authentication_code": response.certification.authentication_code,
                },
            )

            event_name = DGI_EVENTS_EMITTED[2] if invoice.is_credit_note else DGI_EVENTS_EMITTED[0]
            self._emit(
                event_name,
                {
                    "invoice_id": invoice.invoice_id,
                    "serial_number": invoice.serial_number,
                    "is_credit_note": invoice.is_credit_note,
                    "fiscal_device_id": response.certification.fiscal_device_id,
                    "authentication_code": response.certification.authentication_code,
                    "qr_code": response.certification.qr_code,
                },
                correlation_id=invoice.serial_number,
            )
            return invoice

        # 3. Gestion coupure / reprise vs rejet definitif
        is_transient = response.status_hint.upper() == "RETRY" or self._translator.is_retryable(
            response.error_code
        )
        if is_transient and self._retry_policy.can_retry(entry.attempts):
            transition_invoice_status(invoice, CertificationStatus.RETRY)
            entry.invoice = invoice
            entry.status = CertificationStatus.RETRY
            entry.last_error_code = response.error_code or "DEVICE_UNAVAILABLE"
            entry.last_error_message = translated_msg
            entry.next_attempt_at = self._retry_policy.next_attempt_at(now, entry.attempts)
            entry.updated_at = now
            self._outbox.save(entry)
            self._journal.append(
                invoice_id=invoice.invoice_id,
                serial_number=invoice.serial_number,
                status=CertificationStatus.RETRY,
                request_payload=response.raw_request or {"serial_number": invoice.serial_number},
                response_payload=response.raw_response or {"error": translated_msg},
                timestamp=now,
            )
            return invoice

        transition_invoice_status(invoice, CertificationStatus.REJECTED)
        entry.invoice = invoice
        entry.status = CertificationStatus.REJECTED
        entry.last_error_code = response.error_code or "REJECTED"
        entry.last_error_message = translated_msg
        entry.updated_at = now
        self._outbox.save(entry)
        self._journal.append(
            invoice_id=invoice.invoice_id,
            serial_number=invoice.serial_number,
            status=CertificationStatus.REJECTED,
            request_payload=response.raw_request or {"serial_number": invoice.serial_number},
            response_payload=response.raw_response or {"error": translated_msg},
            timestamp=now,
        )
        self._emit(
            DGI_EVENTS_EMITTED[1],
            {
                "invoice_id": invoice.invoice_id,
                "serial_number": invoice.serial_number,
                "error_code": entry.last_error_code,
                "reason": translated_msg,
            },
            correlation_id=invoice.serial_number,
        )
        return invoice

    def process_retry_queue(self, now: datetime | None = None) -> list[InvoiceDTO]:
        """Rejoue automatiquement les factures en attente ou en reprise dues a l'instant `now`."""
        effective_now = now if now is not None else self._now()
        due_entries = self._outbox.list_due(effective_now)
        processed: list[InvoiceDTO] = []
        for entry in due_entries:
            if entry.status == CertificationStatus.CERTIFIED:
                continue
            inv = entry.invoice
            if inv.status == CertificationStatus.RETRY:
                inv.status = CertificationStatus.PENDING
            result = self.certify_invoice(inv)
            processed.append(result)
        return processed

    def certify_credit_note(self, request: CreditNoteRequest) -> InvoiceDTO:
        """Valide un avoir contre sa facture d'origine certifiee puis le certifie sur le meme circuit."""
        request.validate_against_original()
        return self.certify_invoice(request.credit_note_invoice)

    def get_invoice(self, invoice_id: str) -> InvoiceDTO | None:
        return self._invoices_by_id.get(invoice_id)

    def certify(self, document_id: str) -> dict[str, object]:
        """Implementation du contrat FiscalCertifier du core."""
        invoice = self._invoices_by_id.get(document_id)
        if invoice is None:
            raise ComplianceError(f"Document '{document_id}' introuvable pour certification fiscale.")
        certified = self.certify_invoice(invoice)
        cert = certified.certification
        return {
            "invoice_id": certified.invoice_id,
            "serial_number": certified.serial_number,
            "status": certified.status.value,
            "fiscal_device_id": cert.fiscal_device_id if cert else None,
            "authentication_code": cert.authentication_code if cert else None,
            "qr_code": cert.qr_code if cert else None,
        }
