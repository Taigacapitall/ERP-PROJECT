"""Service de certification fiscale DGI (implemente FiscalCertifier du core)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from vorpal.core.audit import AuditLog
from vorpal.core.contracts import (
    CertifiedInvoiceDTO as CoreCertifiedInvoiceDTO,
)
from vorpal.core.contracts import (
    FiscalCertifier,
)
from vorpal.core.contracts import (
    InvoiceDTO as CoreInvoiceDTO,
)
from vorpal.core.contracts.dto import PartyInfoDTO as CorePartyInfoDTO
from vorpal.core.errors import ComplianceError, ExternalServiceError
from vorpal.core.events import Event, EventBus
from vorpal.core.money import Money
from vorpal.modules.dgi.clients.base import DeviceResponse, FiscalDeviceClient
from vorpal.modules.dgi.credit_note import CreditNoteRequest
from vorpal.modules.dgi.errors_map import DgiErrorTranslator
from vorpal.modules.dgi.journal import FiscalJournal
from vorpal.modules.dgi.manifest import DGI_EVENTS_EMITTED
from vorpal.modules.dgi.models import (
    CertificationStatus,
    FiscalCertificationData,
    InvoiceDTO,
    PartyTaxInfo,
    ensure_invoice_valid_for_submission,
)
from vorpal.modules.dgi.models import (
    InvoiceDTO as DGIInvoiceDTO,
)
from vorpal.modules.dgi.models import (
    InvoiceLineDTO as DGIInvoiceLineDTO,
)
from vorpal.modules.dgi.outbox import OutboxEntry, OutboxRepository
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.state_machine import (
    ensure_not_certified_immutable,
    transition_invoice_status,
)
from vorpal.modules.dgi.tax_groups import TaxGroupTable

# Erreurs de communication traitees comme une indisponibilite temporaire (a confirmer avec le client HTTP).
_DEVICE_ERRORS: tuple[type[Exception], ...] = (ExternalServiceError, OSError)


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
        return self._clock() if self._clock is not None else datetime.now(UTC)

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

    @staticmethod
    def _stamp_problem(cert: FiscalCertificationData | None) -> str | None:
        """Retourne la raison pour laquelle le cachet fiscal est inexploitable (None si complet)."""
        if cert is None:
            return "Reponse acceptee sans donnees de certification."
        if not (cert.fiscal_device_id or "").strip():
            return "Identifiant du dispositif fiscal absent."
        if not (cert.authentication_code or "").strip():
            return "Code d'authentification absent."
        if not (cert.qr_code or "").strip():
            return "QR code de certification absent."
        if not cert.certified_at:
            return "Date de certification absente."
        try:
            datetime.fromisoformat(cert.certified_at)
        except (TypeError, ValueError):
            return "Date de certification invalide."
        return None

    def _certified_by_device(self, serial_number: str) -> DeviceResponse | None:
        """Interroge le dispositif : retourne sa reponse seulement s'il a deja certifie avec un cachet complet."""
        try:
            known = self._device.get_status(serial_number)
        except _DEVICE_ERRORS:
            return None
        if known.accepted and self._stamp_problem(known.certification) is None:
            return known
        return None

    def _submit_with_reconciliation(self, invoice: InvoiceDTO, prior_attempts: int) -> DeviceResponse:
        """Reconcilie via get_status apres une tentative precedente, sinon soumet ; une erreur reseau devient RETRY."""
        if prior_attempts > 0:
            known = self._certified_by_device(invoice.serial_number)
            if known is not None:
                return known
        try:
            return self._device.submit(invoice)
        except _DEVICE_ERRORS as exc:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_UNAVAILABLE",
                error_message=str(exc),
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
            prior = existing_entry.invoice
            if prior.total_ttc != invoice.total_ttc or prior.issue_date != invoice.issue_date:
                raise ComplianceError(
                    f"Le numero de serie '{invoice.serial_number}' est deja certifie pour "
                    "une facture de contenu different : cachet non reutilisable."
                )
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
        # La tentative est enregistree AVANT l'envoi : un arret en pleine soumission laisse une trace durable.
        entry.invoice = invoice
        self._outbox.save(entry)

        response = self._submit_with_reconciliation(invoice, prior_attempts=entry.attempts - 1)
        translated_msg = self._translator.translate(response.error_code, response.error_message or "")

        stamp_problem: str | None = None
        if response.accepted:
            stamp_problem = self._stamp_problem(response.certification)
        if (
            response.accepted
            and stamp_problem is not None
            and not self._retry_policy.can_retry(entry.attempts)
        ):
            # Dernier recours avant rejet : le dispositif a peut-etre deja certifie cette facture.
            known = self._certified_by_device(invoice.serial_number)
            if known is not None:
                response, stamp_problem = known, None

        if response.accepted and stamp_problem is None and response.certification is not None:
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
                    "certified_at": response.certification.certified_at,
                    # Donnees comptables : le consommateur (comptabilite) n'a ni a relire Ventes ni a memoriser le
                    # document. Montants en chaines. TODO(A_VALIDER) : payload definitif des evenements fiscaux.
                    "invoice_date": invoice.issue_date.isoformat(),
                    "currency": invoice.currency.value,
                    "total_ht": str(invoice.total_ht.amount),
                    "total_vat": str(invoice.total_vat.amount),
                    "total_ttc": str(invoice.total_ttc.amount),
                    "non_taxable_amount": str(invoice.non_taxable_amount.amount),
                    "other_taxes_amount": str(invoice.other_taxes_amount.amount),
                    "customer_name": invoice.customer.name,
                    "customer_nif": invoice.customer.tax_id,
                    "original_invoice_id": invoice.original_invoice_id,
                },
                correlation_id=invoice.serial_number,
            )
            return invoice

        # 3. Gestion coupure / reprise vs rejet definitif
        error_code = "INCOMPLETE_STAMP" if stamp_problem is not None else response.error_code
        if stamp_problem is not None:
            translated_msg = stamp_problem
        is_transient = (
            stamp_problem is not None
            or response.status_hint.upper() == "RETRY"
            or self._translator.is_retryable(response.error_code)
        )
        if is_transient and self._retry_policy.can_retry(entry.attempts):
            transition_invoice_status(invoice, CertificationStatus.RETRY)
            entry.invoice = invoice
            entry.status = CertificationStatus.RETRY
            entry.last_error_code = error_code or "DEVICE_UNAVAILABLE"
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
        entry.last_error_code = error_code or "REJECTED"
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
            if inv.status in (CertificationStatus.RETRY, CertificationStatus.SENT):
                inv.status = CertificationStatus.PENDING
            result = self.certify_invoice(inv)
            processed.append(result)
        return processed

    def certify_credit_note(self, request: CreditNoteRequest) -> InvoiceDTO:
        """Valide un avoir contre sa facture d'origine certifiee puis le certifie sur le meme circuit."""
        request.validate_against_original()
        return self.certify_invoice(request.credit_note_invoice)

    def certification_status(self, serial_number: str) -> CertificationStatus | None:
        """Statut connu de l'outbox pour ce numero de serie (None si jamais recu)."""
        entry = self._outbox.get(serial_number)
        return entry.status if entry is not None else None

    def refuse_invoice(self, serial_number: str, reason: str) -> None:
        """Refuse une facture intransmissible (ex. correspondance fiscale absente) et publie le rejet."""
        self._journal.append(
            invoice_id=serial_number,
            serial_number=serial_number,
            status=CertificationStatus.REJECTED,
            request_payload={"serial_number": serial_number},
            response_payload={"error": reason},
            timestamp=self._now(),
        )
        self._emit(
            DGI_EVENTS_EMITTED[1],
            {
                "invoice_id": serial_number,
                "serial_number": serial_number,
                "error_code": "NOT_TRANSMITTABLE",
                "reason": reason,
            },
            correlation_id=serial_number,
        )

    def reopen_rejected(self, serial_number: str, requested_at: datetime) -> bool:
        """Autorise la resoumission d'une facture REJECTED si la demande est posterieure au rejet.

        Un rejeu d'ancien evenement (date anterieure au rejet) ne rouvre rien.
        """
        entry = self._outbox.get(serial_number)
        if entry is None or entry.status != CertificationStatus.REJECTED:
            return False
        if entry.updated_at is not None and requested_at <= entry.updated_at:
            return False
        entry.status = CertificationStatus.PENDING
        entry.attempts = 0
        entry.next_attempt_at = self._now()
        entry.last_error_code = None
        entry.last_error_message = None
        entry.updated_at = self._now()
        self._outbox.save(entry)
        return True

    def get_invoice(self, invoice_id: str) -> InvoiceDTO | None:
        cached = self._invoices_by_id.get(invoice_id)
        if cached is not None:
            return cached
        for entry in self._outbox.list_all():
            if entry.invoice.invoice_id == invoice_id:
                return entry.invoice
        return None

    def convert_core_invoice(
        self,
        invoice: CoreInvoiceDTO,
    ) -> DGIInvoiceDTO:
        """Adapte le contrat Core au pipeline métier de certification DGI.

        Les modèles Core et DGI restent distincts.
        La certification métier reste exécutée par certify_invoice().
        """
        if not isinstance(invoice, CoreInvoiceDTO):
            raise ComplianceError(
                "Le service DGI attend un InvoiceDTO du Core."
            )

        if invoice.invoice_type == "avoir":
            raise ComplianceError(
                "Certification d'avoir impossible via le contrat Core : "
                "le motif obligatoire et la validation de la facture originale "
                "certifiée doivent être fournis par le workflow spécialisé."
            )

        if not invoice.number or not invoice.number.strip():
            raise ComplianceError("Le numéro de facture est obligatoire.")

        currency = invoice.currency
        decimal_places = Decimal("0.01")
        rounding = ROUND_HALF_UP

        def amount(value: Decimal) -> Decimal:
            if isinstance(value, (float, bool)) or not isinstance(value, Decimal):
                raise ComplianceError(
                    "Montant invalide : Decimal strict attendu."
                )
            return value.quantize(decimal_places, rounding=rounding)

        if amount(invoice.non_taxable_amount) != Decimal("0.00"):
            raise ComplianceError(
                "Certification Core impossible : le montant non taxable "
                "est incompatible avec le calcul TTC du Core."
            )

        if amount(invoice.other_taxes) != Decimal("0.00"):
            raise ComplianceError(
                "Certification Core impossible : les autres taxes "
                "sont incompatibles avec le calcul TTC du Core."
            )

        def party_to_dgi(party: CorePartyInfoDTO) -> PartyTaxInfo:
            return PartyTaxInfo(
                name=party.name,
                tax_id=party.nif,
                rccm=party.rccm,
                address=party.address,
            )

        dgi_lines = []
        computed_ht = Decimal("0")
        computed_vat = Decimal("0")

        for index, line in enumerate(invoice.lines, start=1):
            quantity = line.quantity
            unit_price = amount(line.unit_price_ht)
            vat_rate = line.vat_rate

            if isinstance(quantity, (float, bool)):
                raise ComplianceError(
                    f"Quantité invalide sur la ligne {index}."
                )

            if not isinstance(vat_rate, Decimal) or isinstance(vat_rate, bool):
                raise ComplianceError(
                    f"Taux de TVA invalide sur la ligne {index}."
                )

            try:
                group = self._tax_table.map_internal_tax(line.tax_group, invoice.invoice_date)
            except ComplianceError as exc:
                raise ComplianceError(
                    f"Correspondance fiscale absente ou inactive pour le groupe Ventes "
                    f"'{line.tax_group}' (ligne {index}) : declarer la correspondance "
                    f"dans la table DGI. Detail : {exc}"
                ) from exc

            if group.rate != vat_rate:
                raise ComplianceError(
                    f"Incohérence entre le taux TVA ({vat_rate}) et "
                    f"le groupe fiscal {line.tax_group} ({group.rate})."
                )

            line_ht = amount(quantity * unit_price)
            line_vat = amount(line_ht * vat_rate)
            line_ttc = amount(line_ht + line_vat)

            computed_ht += line_ht
            computed_vat += line_vat

            dgi_lines.append(
                DGIInvoiceLineDTO(
                    line_id=f"{invoice.number}:{index}",
                    designation=line.designation,
                    quantity=quantity,
                    unit_price_ht=Money(unit_price, currency),
                    line_total_ht=Money(line_ht, currency),
                    vat_rate=vat_rate,
                    vat_amount=Money(line_vat, currency),
                    line_total_ttc=Money(line_ttc, currency),
                    tax_group_code=group.code,
                    item_code=line.item_code,
                    out_of_scope=group.out_of_scope,
                    other_taxes_amount=Money(
                        Decimal("0.00"), currency
                    ),
                )
            )

        total_ht = amount(invoice.total_ht)
        total_vat = amount(invoice.total_vat)
        total_ttc = amount(invoice.total_ttc)

        if computed_ht != total_ht:
            raise ComplianceError(
                "Total HT Core différent du total calculé à partir des lignes."
            )

        if computed_vat != total_vat:
            raise ComplianceError(
                "Total TVA Core différent du total calculé à partir des lignes."
            )

        if total_ttc != amount(total_ht + total_vat):
            raise ComplianceError(
                "Total TTC Core incohérent : HT + TVA attendu."
            )

        dgi_invoice = DGIInvoiceDTO(
            invoice_id=invoice.number,
            serial_number=invoice.number,
            issue_date=invoice.invoice_date,
            currency=currency,
            seller=party_to_dgi(invoice.seller),
            customer=party_to_dgi(invoice.customer),
            lines=dgi_lines,
            total_ht=Money(total_ht, currency),
            total_vat=Money(total_vat, currency),
            non_taxable_amount=Money(
                amount(invoice.non_taxable_amount), currency
            ),
            total_ttc=Money(total_ttc, currency),
            other_taxes_amount=Money(
                amount(invoice.other_taxes), currency
            ),
            status=CertificationStatus.PENDING,
            certification=None,
            original_invoice_id=None,
            is_credit_note=False,
        )

        return dgi_invoice

    def certify(
        self,
        invoice: CoreInvoiceDTO,
    ) -> CoreCertifiedInvoiceDTO:
        """Contrat FiscalCertifier : convertit, certifie via le pipeline DGI et retourne le DTO Core."""
        dgi_invoice = self.convert_core_invoice(invoice)
        # Réutilise le pipeline DGI existant et son Outbox.
        result = self.certify_invoice(dgi_invoice)

        if result.status != CertificationStatus.CERTIFIED:
            raise ComplianceError(
                "La facture n'a pas été certifiée. "
                f"Statut DGI : {result.status.value}."
            )

        certification = result.certification
        if certification is None:
            raise ComplianceError(
                "Le dispositif DGI a certifié la facture sans "
                "retourner les données de certification."
            )

        if not certification.certified_at:
            raise ComplianceError(
                "Date de certification absente de la réponse DGI."
            )

        try:
            certified_at = datetime.fromisoformat(
                certification.certified_at
            )
        except (TypeError, ValueError) as exc:
            raise ComplianceError(
                "Date de certification DGI invalide."
            ) from exc

        if not certification.fiscal_device_id:
            raise ComplianceError("Identifiant du dispositif fiscal absent.")

        if not certification.authentication_code:
            raise ComplianceError("Code d'authentification absent.")

        if not certification.qr_code:
            raise ComplianceError("QR code de certification absent.")

        return CoreCertifiedInvoiceDTO(
            number=invoice.number,
            invoice_date=invoice.invoice_date,
            seller=invoice.seller,
            customer=invoice.customer,
            lines=invoice.lines,
            currency=invoice.currency,
            total_ht=invoice.total_ht,
            total_vat=invoice.total_vat,
            total_ttc=invoice.total_ttc,
            invoice_type=invoice.invoice_type,
            original_invoice_ref=invoice.original_invoice_ref,
            non_taxable_amount=invoice.non_taxable_amount,
            other_taxes=invoice.other_taxes,
            device_id=certification.fiscal_device_id,
            auth_code=certification.authentication_code,
            qr_code=certification.qr_code,
            certified_at=certified_at,
            counters={},
        )
