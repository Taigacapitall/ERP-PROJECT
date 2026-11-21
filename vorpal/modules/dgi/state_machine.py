"""Machine a etats des factures soumises a la conformite DGI et regle d'immutabilite."""

from __future__ import annotations

from vorpal.core.errors import ComplianceError
from vorpal.modules.dgi.models import CertificationStatus, FiscalCertificationData, InvoiceDTO

ALLOWED_TRANSITIONS: dict[CertificationStatus, set[CertificationStatus]] = {
    CertificationStatus.PENDING: {
        CertificationStatus.SENT,
        CertificationStatus.RETRY,
        CertificationStatus.REJECTED,
    },
    CertificationStatus.SENT: {
        CertificationStatus.CERTIFIED,
        CertificationStatus.REJECTED,
        CertificationStatus.RETRY,
    },
    CertificationStatus.RETRY: {
        CertificationStatus.SENT,
        CertificationStatus.CERTIFIED,
        CertificationStatus.REJECTED,
    },
    CertificationStatus.REJECTED: {
        CertificationStatus.RETRY,
    },
    CertificationStatus.CERTIFIED: set(),
}


def ensure_not_certified_immutable(invoice: InvoiceDTO, operation: str = "modification") -> None:
    """Interdit toute modification directe d'une facture certifiee (correction via avoir)."""
    if invoice.status == CertificationStatus.CERTIFIED:
        raise ComplianceError(
            f"La facture '{invoice.invoice_id}' est CERTIFIED et immuable ({operation} interdite ; passer par un avoir)."
        )


def transition_invoice_status(
    invoice: InvoiceDTO,
    target_status: CertificationStatus,
    certification_data: FiscalCertificationData | None = None,
) -> InvoiceDTO:
    """Applique une transition d'etat validee ou leve ComplianceError."""
    if invoice.status == CertificationStatus.CERTIFIED:
        raise ComplianceError(
            f"Facture '{invoice.invoice_id}' deja CERTIFIED : aucune transition d'etat n'est autorisee."
        )

    allowed = ALLOWED_TRANSITIONS.get(invoice.status, set())
    if target_status not in allowed:
        raise ComplianceError(
            f"Transition d'etat interdite sur la facture '{invoice.invoice_id}' : "
            f"{invoice.status.value} -> {target_status.value}."
        )

    if target_status == CertificationStatus.CERTIFIED:
        if (
            certification_data is None
            or not certification_data.fiscal_device_id.strip()
            or not certification_data.authentication_code.strip()
            or not certification_data.qr_code.strip()
        ):
            raise ComplianceError(
                "Impossible de passer au statut CERTIFIED sans identifiant dispositif, code d'authentification et QR."
            )
        invoice.certification = certification_data

    invoice.status = target_status
    return invoice
