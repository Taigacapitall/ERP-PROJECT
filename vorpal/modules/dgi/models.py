"""Modeles du domaine Conformite DGI et validation des 12 mentions obligatoires."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum

from vorpal.core.errors import ComplianceError
from vorpal.core.money import Currency, Money


class CertificationStatus(str, Enum):
    """Etats du cycle de conformite fiscale d'une facture."""

    PENDING = "PENDING"
    SENT = "SENT"
    CERTIFIED = "CERTIFIED"
    REJECTED = "REJECTED"
    RETRY = "RETRY"


MANDATORY_MENTIONS_KEYS: tuple[str, ...] = (
    "seller_identity_and_tax",      # 1. RCCM + NIF + adresse vendeur
    "customer_identity_and_tax",    # 2. Identite + n° impot client
    "invoice_date_and_serial",      # 3. Date + n° de serie
    "item_designation_and_qty",     # 4. Designation et quantite
    "unit_and_line_price",          # 5. Prix unitaire et global
    "ht_vat_rate_and_vat_amount",   # 6. Prix HT, taux TVA, montant TVA
    "non_taxable_amount",           # 7. Montant non taxable
    "total_ttc",                    # 8. Total TTC
    "other_taxes",                  # 9. Autres impots/taxes
    "fiscal_device_id",             # 10. N° d'identification du dispositif
    "authentication_code",          # 11. Code d'authentification
    "qr_code",                      # 12. Code QR
)


def strict_decimal(value: Decimal | int | str, field_name: str = "montant") -> Decimal:
    """Convertit en Decimal en interdisant strictement float, bool, NaN et infini."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ComplianceError(f"{field_name} interdit les types float ou bool.")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ComplianceError(f"{field_name} ne peut pas etre vide.")
        try:
            dec = Decimal(stripped)
        except InvalidOperation as exc:
            raise ComplianceError(f"{field_name} n'est pas un nombre decimal valide.") from exc
    else:
        raise ComplianceError(f"{field_name} doit etre un Decimal, int ou str.")

    if dec.is_nan() or dec.is_infinite():
        raise ComplianceError(f"{field_name} doit etre un nombre fini.")
    return dec


@dataclass(frozen=True)
class PartyTaxInfo:
    """Informations d'identification et fiscales d'une partie (vendeur ou client)."""

    name: str
    tax_id: str | None = None
    rccm: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class InvoiceLineDTO:
    """Ligne d'une facture soumise a la conformite DGI."""

    line_id: str
    designation: str
    quantity: Decimal
    unit_price_ht: Money
    line_total_ht: Money
    vat_rate: Decimal
    vat_amount: Money
    line_total_ttc: Money
    tax_group_code: str
    item_code: str | None = None
    out_of_scope: bool = False
    other_taxes_amount: Money | None = None

    def __post_init__(self) -> None:
        qty = strict_decimal(self.quantity, "quantity")
        rate = strict_decimal(self.vat_rate, "vat_rate")
        if qty <= Decimal("0"):
            raise ComplianceError("La quantite de ligne doit etre strictement positive.")
        if rate < Decimal("0"):
            raise ComplianceError("Le taux de TVA ne peut pas etre negatif.")
        if self.out_of_scope and (self.item_code is None or not self.item_code.strip()):
            raise ComplianceError(
                f"Une ligne hors champ ('{self.designation}') exige un code article."
            )
        object.__setattr__(self, "quantity", qty)
        object.__setattr__(self, "vat_rate", rate)


@dataclass
class FiscalCertificationData:
    """Donnees delivrees par le dispositif fiscal une fois la facture certifiee."""

    fiscal_device_id: str
    authentication_code: str
    qr_code: str
    certified_at: str | None = None


@dataclass
class InvoiceDTO:
    """Representation normalisee d'une facture (ou avoir) pour la certification DGI."""

    invoice_id: str
    serial_number: str
    issue_date: date
    currency: Currency
    seller: PartyTaxInfo
    customer: PartyTaxInfo
    lines: list[InvoiceLineDTO]
    total_ht: Money
    total_vat: Money
    non_taxable_amount: Money
    total_ttc: Money
    other_taxes_amount: Money = field(default_factory=lambda: Money(Decimal("0.00"), Currency.USD))
    status: CertificationStatus = CertificationStatus.PENDING
    certification: FiscalCertificationData | None = None
    original_invoice_id: str | None = None
    is_credit_note: bool = False

    def __post_init__(self) -> None:
        if self.other_taxes_amount.currency != self.currency:
            object.__setattr__(
                self,
                "other_taxes_amount",
                Money(self.other_taxes_amount.amount, self.currency),
            )


def validate_mandatory_mentions(invoice: InvoiceDTO, require_post_certification: bool = False) -> list[str]:
    """Verifie la presence des 12 mentions obligatoires et retourne la liste des manquantes."""
    missing: list[str] = []

    # 1. RCCM + NIF + adresse vendeur
    if not (
        invoice.seller.name.strip()
        and (invoice.seller.tax_id or "").strip()
        and (invoice.seller.rccm or "").strip()
        and (invoice.seller.address or "").strip()
    ):
        missing.append("seller_identity_and_tax")

    # 2. Identite + n° impot client
    if not (invoice.customer.name.strip() and (invoice.customer.tax_id or "").strip()):
        missing.append("customer_identity_and_tax")

    # 3. Date + n° de serie
    if not (invoice.issue_date and invoice.serial_number.strip()):
        missing.append("invoice_date_and_serial")

    # 4, 5, 6, 9 au niveau des lignes
    if not invoice.lines:
        missing.extend(
            [
                "item_designation_and_qty",
                "unit_and_line_price",
                "ht_vat_rate_and_vat_amount",
            ]
        )
    else:
        if any(not ln.designation.strip() or ln.quantity <= Decimal("0") for ln in invoice.lines):
            missing.append("item_designation_and_qty")
        if any(
            ln.unit_price_ht.amount < Decimal("0") or ln.line_total_ht.amount < Decimal("0")
            for ln in invoice.lines
        ):
            missing.append("unit_and_line_price")
        if any(ln.vat_rate < Decimal("0") or ln.vat_amount.amount < Decimal("0") for ln in invoice.lines):
            missing.append("ht_vat_rate_and_vat_amount")

    # 7. Montant non taxable
    if invoice.non_taxable_amount.amount < Decimal("0"):
        missing.append("non_taxable_amount")

    # 8. Total TTC
    if invoice.total_ttc.amount <= Decimal("0"):
        missing.append("total_ttc")

    # 9. Autres impots/taxes
    if invoice.other_taxes_amount.amount < Decimal("0"):
        missing.append("other_taxes")

    # 10, 11, 12. Mentions issues du dispositif fiscal (verifiees apres certification ou a l'emission du rendu)
    cert = invoice.certification
    if require_post_certification:
        if cert is None or not cert.fiscal_device_id.strip():
            missing.append("fiscal_device_id")
        if cert is None or not cert.authentication_code.strip():
            missing.append("authentication_code")
        if cert is None or not cert.qr_code.strip():
            missing.append("qr_code")

    return missing


def ensure_invoice_valid_for_submission(invoice: InvoiceDTO) -> None:
    """Valide les 12 mentions pre-envoi et la coherence des montants."""
    missing = validate_mandatory_mentions(invoice, require_post_certification=False)
    if missing:
        raise ComplianceError(
            f"Facture '{invoice.invoice_id}' refusee avant envoi : mentions obligatoires manquantes ({', '.join(missing)})."
        )

    for ln in invoice.lines:
        if ln.out_of_scope and not (ln.item_code or "").strip():
            raise ComplianceError(
                f"Ligne hors champ '{ln.line_id}' sur facture '{invoice.invoice_id}' sans code article."
            )

    expected_ht = sum((ln.line_total_ht.amount for ln in invoice.lines), Decimal("0.00"))
    expected_vat = sum((ln.vat_amount.amount for ln in invoice.lines), Decimal("0.00"))
    expected_line_other = sum(
        ((ln.other_taxes_amount.amount if ln.other_taxes_amount else Decimal("0.00")) for ln in invoice.lines),
        Decimal("0.00"),
    )
    total_other_taxes = max(invoice.other_taxes_amount.amount, expected_line_other)

    if invoice.total_ht.amount != expected_ht:
        raise ComplianceError(
            f"Total HT incoherent sur facture '{invoice.invoice_id}' : {invoice.total_ht.amount} != {expected_ht}."
        )
    if invoice.total_vat.amount != expected_vat:
        raise ComplianceError(
            f"Total TVA incoherent sur facture '{invoice.invoice_id}' : {invoice.total_vat.amount} != {expected_vat}."
        )

    expected_ttc = (
        invoice.total_ht.amount
        + invoice.total_vat.amount
        + invoice.non_taxable_amount.amount
        + total_other_taxes
    )
    if invoice.total_ttc.amount != expected_ttc:
        raise ComplianceError(
            f"Total TTC incoherent sur facture '{invoice.invoice_id}' : {invoice.total_ttc.amount} != {expected_ttc}."
        )
