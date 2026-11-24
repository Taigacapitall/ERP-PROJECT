"""Documents imprimables (PDF) et exports (CSV) du module Ventes.

Dependance optionnelle : `reportlab` (extra `pdf`). Le CSV n'utilise que la bibliotheque standard.
Les PDF produits sont des documents COMMERCIAUX : ils ne portent aucun cachet fiscal DGI.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from vorpal.core import Money
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.payments import Payment, PaymentStatus
from vorpal.modules.ventes.domain.quotes import Quote
from vorpal.modules.ventes.domain.totals import (
    LineCalculationInput,
    compute_document_totals,
    compute_line_amounts,
)
from vorpal.modules.ventes.settings import SellerSettings

if TYPE_CHECKING:
    from vorpal.modules.ventes.services.invoice_queries import InvoiceView

DEFAULT_FOOTER = "Document commercial — non certifié par la DGI (facture normalisée non applicable)."


@dataclass(frozen=True)
class DocLine:
    designation: str
    quantity: Decimal
    unit_price: Money
    discount_percent: Decimal
    vat_rate: Decimal
    total_ht: Money


@dataclass(frozen=True)
class DocumentModel:
    title: str
    number: str
    doc_date: date
    secondary_label: str
    secondary_date: date
    seller: SellerSettings
    customer_name: str
    customer_lines: tuple[str, ...]
    lines: tuple[DocLine, ...]
    total_ht: Money
    total_vat: Money
    total_ttc: Money
    notes: tuple[str, ...]
    footer: str


def fmt_amount(money: Money) -> str:
    """Montant avec separateur de milliers (espace) et code devise : « 1 234,50 USD »."""
    text = f"{money.amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} {money.currency.value}"


class _LineLike(Protocol):
    """Attributs communs a InvoiceLine et QuoteLine."""

    @property
    def designation(self) -> str: ...
    @property
    def quantity(self) -> Decimal: ...
    @property
    def unit_price_ht(self) -> Money: ...
    @property
    def discount_percent(self) -> Decimal: ...
    @property
    def vat_rate(self) -> Decimal: ...


def _doc_lines(lines: Sequence[_LineLike], currency_decimals: int) -> tuple[DocLine, ...]:
    out: list[DocLine] = []
    for line in lines:
        amounts = compute_line_amounts(
            LineCalculationInput(
                quantity=line.quantity,
                unit_price_ht=line.unit_price_ht.amount,
                discount_percent=line.discount_percent,
                vat_rate=line.vat_rate,
            ),
            currency=line.unit_price_ht.currency,
            decimals=currency_decimals,
        )
        out.append(
            DocLine(
                designation=line.designation,
                quantity=line.quantity,
                unit_price=line.unit_price_ht,
                discount_percent=line.discount_percent,
                vat_rate=line.vat_rate,
                total_ht=amounts.total_ht,
            )
        )
    return tuple(out)


def _customer_lines(customer: Customer) -> tuple[str, ...]:
    parts = [customer.name]
    if customer.address:
        parts.append(customer.address)
    if customer.nif:
        parts.append(f"NIF : {customer.nif}")
    if customer.rccm:
        parts.append(f"RCCM : {customer.rccm}")
    return tuple(parts)


def invoice_model(
    view: InvoiceView,
    customer: Customer,
    seller: SellerSettings,
    payments: Sequence[Payment] = (),
    *,
    decimals: int = 2,
    footer: str = DEFAULT_FOOTER,
) -> DocumentModel:
    """Construit le modele d'un document facture ou avoir a partir d'une vue de facture."""
    inv = view.invoice
    if inv.number is None:
        raise ValueError("Seule une facture emise (numerotee) peut etre imprimee.")
    is_credit = inv.invoice_type == "avoir"
    notes: list[str] = []
    if is_credit and inv.original_invoice_number:
        notes.append(f"Avoir se rapportant à la facture {inv.original_invoice_number}.")
    if not is_credit:
        for pay in payments:
            if pay.status != PaymentStatus.RECORDED:
                continue
            for alloc in pay.allocations:
                if alloc.invoice_id == inv.id:
                    ref = f" (réf. {pay.reference})" if pay.reference else ""
                    notes.append(f"Paiement du {alloc.allocated_at.isoformat()} · {pay.method.value}{ref} : {fmt_amount(alloc.amount)}")
        if view.credited.amount > 0:
            notes.append(f"Avoirs émis : {fmt_amount(view.credited)}")
        notes.append(f"Solde à payer : {fmt_amount(view.net_due)}")
        if view.cancelled:
            notes.append("FACTURE ANNULÉE (avoir total émis).")
    if inv.total_ht is None or inv.total_vat is None or inv.total_ttc is None:
        raise ValueError("Totaux de facture manquants : la facture doit etre emise.")
    return DocumentModel(
        title="AVOIR" if is_credit else "FACTURE",
        number=inv.number,
        doc_date=inv.invoice_date,
        secondary_label="Échéance",
        secondary_date=inv.due_date,
        seller=seller,
        customer_name=customer.name,
        customer_lines=_customer_lines(customer),
        lines=_doc_lines(inv.lines, decimals),
        total_ht=inv.total_ht,
        total_vat=inv.total_vat,
        total_ttc=inv.total_ttc,
        notes=tuple(notes),
        footer=footer,
    )


def quote_model(
    quote: Quote, customer: Customer, seller: SellerSettings, *, decimals: int = 2, footer: str = ""
) -> DocumentModel:
    """Construit le modele d'un devis."""
    totals = compute_document_totals(
        [LineCalculationInput(ql.quantity, ql.unit_price_ht.amount, ql.discount_percent, ql.vat_rate) for ql in quote.lines],
        currency=quote.currency,
        decimals=decimals,
    )
    return DocumentModel(
        title="DEVIS",
        number=quote.number,
        doc_date=quote.quote_date,
        secondary_label="Valable jusqu'au",
        secondary_date=quote.valid_until,
        seller=seller,
        customer_name=customer.name,
        customer_lines=_customer_lines(customer),
        lines=_doc_lines(quote.lines, decimals),
        total_ht=totals.total_ht,
        total_vat=totals.total_vat,
        total_ttc=totals.total_ttc,
        notes=(),
        footer=footer,
    )


def render_pdf(doc: DocumentModel) -> bytes:
    """Genere le PDF A4 d'un document commercial (facture, avoir ou devis)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Flowable,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - depend de l'environnement
        raise RuntimeError("Le rendu PDF exige reportlab : pip install \"vorpal-business-manager[pdf]\".") from exc

    styles = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=styles["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=normal, fontSize=8, textColor=colors.grey)
    title = ParagraphStyle("t", parent=styles["Title"], alignment=2, fontSize=22, spaceAfter=2)
    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"{doc.title} {doc.number}", author=doc.seller.name,
    )

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    seller_block = [f"<b>{esc(doc.seller.name)}</b>"]
    if doc.seller.address:
        seller_block.append(esc(doc.seller.address))
    seller_block.append(f"NIF : {esc(doc.seller.nif)}")
    if doc.seller.rccm:
        seller_block.append(f"RCCM : {esc(doc.seller.rccm)}")
    head = Table(
        [[Paragraph("<br/>".join(seller_block), normal),
          [Paragraph(doc.title, title),
           Paragraph(f"N° {esc(doc.number)}<br/>Date : {doc.doc_date.isoformat()}<br/>"
                     f"{esc(doc.secondary_label)} : {doc.secondary_date.isoformat()}",
                     ParagraphStyle("r", parent=normal, alignment=2))]]],
        colWidths=[95 * mm, 85 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    client = Table([[Paragraph("<b>Client</b><br/>" + "<br/>".join(esc(x) for x in doc.customer_lines), normal)]],
                   colWidths=[100 * mm], hAlign="LEFT")
    client.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("PADDING", (0, 0), (-1, -1), 6)]))

    rows: list[list[object]] = [["Désignation", "Qté", "PU HT", "Rem. %", "TVA %", "Total HT"]]
    for ln in doc.lines:
        rows.append([
            Paragraph(esc(ln.designation), normal), f"{ln.quantity.normalize():f}", fmt_amount(ln.unit_price),
            f"{ln.discount_percent.normalize():f}", f"{(ln.vat_rate * 100).normalize():f}", fmt_amount(ln.total_ht),
        ])
    table = Table(rows, colWidths=[66 * mm, 14 * mm, 28 * mm, 14 * mm, 14 * mm, 34 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f2a85")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    totals = Table(
        [["Total HT", fmt_amount(doc.total_ht)], ["TVA", fmt_amount(doc.total_vat)], ["Total TTC", fmt_amount(doc.total_ttc)]],
        colWidths=[35 * mm, 40 * mm], hAlign="RIGHT",
    )
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story: list[Flowable] = [head, Spacer(1, 8 * mm), client, Spacer(1, 6 * mm), table, Spacer(1, 4 * mm), totals]
    if doc.notes:
        story.append(Spacer(1, 6 * mm))
        story.extend(Paragraph(esc(n), normal) for n in doc.notes)
    if doc.footer:
        story.extend([Spacer(1, 10 * mm), Paragraph(esc(doc.footer), small)])
    pdf.build(story)
    return buffer.getvalue()


def export_invoices_csv(views: Sequence[InvoiceView]) -> str:
    """Exporte une liste de factures/avoirs en CSV (UTF-8, separateur « ; », ouvrable dans Excel)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["numero", "type", "statut", "client", "date", "echeance", "devise",
                     "total_ht", "total_tva", "total_ttc", "avoirs", "paye", "solde_net", "jours_retard"])
    for v in views:
        i = v.invoice
        writer.writerow([
            i.number or "", i.invoice_type, v.label, v.customer_name, i.invoice_date.isoformat(), i.due_date.isoformat(),
            i.currency.value,
            i.total_ht.amount if i.total_ht else "", i.total_vat.amount if i.total_vat else "",
            i.effective_total_ttc.amount, v.credited.amount, v.paid.amount, v.net_due.amount, v.days_overdue,
        ])
    return out.getvalue()
