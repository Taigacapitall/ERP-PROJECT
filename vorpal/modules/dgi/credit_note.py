"""Gestion des avoirs (CreditNote) lies a une facture d'origine certifiee."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vorpal.core.errors import ComplianceError
from vorpal.modules.dgi.models import CertificationStatus, InvoiceDTO


@dataclass(frozen=True)
class CreditNoteRequest:
    """Demande d'emission d'un avoir rattache a une facture d'origine."""

    credit_note_invoice: InvoiceDTO
    original_invoice: InvoiceDTO
    reason: str

    def validate_against_original(self) -> None:
        if not self.reason.strip():
            raise ComplianceError("Le motif de l'avoir est obligatoire.")
        if self.original_invoice.status != CertificationStatus.CERTIFIED:
            raise ComplianceError(
                f"Impossible d'emettre un avoir : la facture d'origine '{self.original_invoice.invoice_id}' "
                f"n'est pas certifiee (statut actuel : {self.original_invoice.status.value})."
            )
        if self.credit_note_invoice.currency != self.original_invoice.currency:
            raise ComplianceError(
                "La devise de l'avoir doit correspondre a la devise de la facture d'origine."
            )
        if self.credit_note_invoice.total_ttc.amount <= Decimal("0"):
            raise ComplianceError("Le montant TTC de l'avoir doit etre strictement positif.")
        if self.credit_note_invoice.total_ttc.amount > self.original_invoice.total_ttc.amount:
            raise ComplianceError(
                f"Montant de l'avoir ({self.credit_note_invoice.total_ttc.amount}) superieur au montant "
                f"de la facture d'origine ({self.original_invoice.total_ttc.amount})."
            )
        self.credit_note_invoice.is_credit_note = True
        self.credit_note_invoice.original_invoice_id = self.original_invoice.invoice_id
