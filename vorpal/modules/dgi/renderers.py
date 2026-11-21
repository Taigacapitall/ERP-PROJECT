"""Moteurs de rendu de facture normalisee avec controle bloquant des 12 mentions obligatoires."""

from __future__ import annotations

import html
from abc import ABC, abstractmethod

from vorpal.core.errors import ComplianceError
from vorpal.modules.dgi.models import InvoiceDTO, validate_mandatory_mentions


class InvoiceRenderer(ABC):
    """Contrat de rendu d'une facture normalisee DGI."""

    @abstractmethod
    def render(self, invoice: InvoiceDTO) -> str:
        """Produit la representation normalisee apres verification stricte des 12 mentions."""


def _assert_all_twelve_mentions_present(invoice: InvoiceDTO) -> None:
    missing = validate_mandatory_mentions(invoice, require_post_certification=True)
    if missing:
        raise ComplianceError(
            f"Rendu facture normalisee bloque pour '{invoice.invoice_id}' : "
            f"mentions obligatoires manquantes ({', '.join(missing)})."
        )


class TextRenderer(InvoiceRenderer):
    """Rendu texte normalise."""

    def render(self, invoice: InvoiceDTO) -> str:
        _assert_all_twelve_mentions_present(invoice)
        cert = invoice.certification
        assert cert is not None

        lines_block = "\n".join(
            f"- [{ln.tax_group_code}] {ln.designation} | Qte: {ln.quantity} | PU HT: {ln.unit_price_ht.amount} | "
            f"Total HT: {ln.line_total_ht.amount} | TVA ({ln.vat_rate}): {ln.vat_amount.amount} | "
            f"TTC: {ln.line_total_ttc.amount}"
            for ln in invoice.lines
        )
        doc_type = "AVOIR NORMALISE" if invoice.is_credit_note else "FACTURE NORMALISEE"
        return (
            f"=== {doc_type} DGI RDC ===\n"
            f"Vendeur: {invoice.seller.name} | NIF: {invoice.seller.tax_id} | RCCM: {invoice.seller.rccm} | Adresse: {invoice.seller.address}\n"
            f"Client: {invoice.customer.name} | NIF: {invoice.customer.tax_id}\n"
            f"Date: {invoice.issue_date.isoformat()} | N° Serie: {invoice.serial_number}\n"
            f"Lignes:\n{lines_block}\n"
            f"Total HT: {invoice.total_ht.amount} {invoice.currency.value}\n"
            f"Montant TVA: {invoice.total_vat.amount} {invoice.currency.value}\n"
            f"Montant non taxable: {invoice.non_taxable_amount.amount} {invoice.currency.value}\n"
            f"Autres impots/taxes: {invoice.other_taxes_amount.amount} {invoice.currency.value}\n"
            f"Total TTC: {invoice.total_ttc.amount} {invoice.currency.value}\n"
            f"Dispositif: {cert.fiscal_device_id} | Code Auth: {cert.authentication_code} | QR: {cert.qr_code}\n"
        )


class HtmlRenderer(InvoiceRenderer):
    """Rendu HTML normalise (base pour futur export PDF)."""

    def render(self, invoice: InvoiceDTO) -> str:
        _assert_all_twelve_mentions_present(invoice)
        cert = invoice.certification
        assert cert is not None

        rows_html = "\n".join(
            "<tr>"
            f"<td>{html.escape(ln.designation)}</td>"
            f"<td>{html.escape(str(ln.quantity))}</td>"
            f"<td>{html.escape(str(ln.unit_price_ht.amount))}</td>"
            f"<td>{html.escape(str(ln.line_total_ht.amount))}</td>"
            f"<td>{html.escape(str(ln.vat_rate))}</td>"
            f"<td>{html.escape(str(ln.vat_amount.amount))}</td>"
            f"<td>{html.escape(str(ln.line_total_ttc.amount))}</td>"
            "</tr>"
            for ln in invoice.lines
        )
        title = "Avoir Normalise DGI" if invoice.is_credit_note else "Facture Normalisee DGI"
        return (
            f"<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title></head>"
            f"<body><h1>{html.escape(title)}</h1>"
            f"<p><strong>Vendeur :</strong> {html.escape(invoice.seller.name)} | "
            f"NIF: {html.escape(str(invoice.seller.tax_id))} | "
            f"RCCM: {html.escape(str(invoice.seller.rccm))} | "
            f"Adresse: {html.escape(str(invoice.seller.address))}</p>"
            f"<p><strong>Client :</strong> {html.escape(invoice.customer.name)} | "
            f"NIF: {html.escape(str(invoice.customer.tax_id))}</p>"
            f"<p><strong>Date :</strong> {html.escape(invoice.issue_date.isoformat())} | "
            f"<strong>N° Serie :</strong> {html.escape(invoice.serial_number)}</p>"
            f"<table><thead><tr><th>Designation</th><th>Qte</th><th>PU HT</th><th>Total HT</th>"
            f"<th>Taux TVA</th><th>TVA</th><th>TTC</th></tr></thead><tbody>{rows_html}</tbody></table>"
            f"<p>Total HT: {html.escape(str(invoice.total_ht.amount))} {html.escape(invoice.currency.value)} | "
            f"TVA: {html.escape(str(invoice.total_vat.amount))} | "
            f"Non taxable: {html.escape(str(invoice.non_taxable_amount.amount))} | "
            f"Autres taxes: {html.escape(str(invoice.other_taxes_amount.amount))} | "
            f"<strong>Total TTC: {html.escape(str(invoice.total_ttc.amount))} {html.escape(invoice.currency.value)}</strong></p>"
            f"<p>Dispositif: {html.escape(cert.fiscal_device_id)} | "
            f"Code authentification: {html.escape(cert.authentication_code)} | "
            f"QR: {html.escape(cert.qr_code)}</p>"
            f"</body></html>"
        )
