from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vorpal.modules.adapters.odoo import (
    AccountingEventHandlers,
    AccountMapping,
    FakeOdooClient,
    OdooAccountingPoster,
    SqlitePendingQueue,
)

ACCOUNTS = {"411100": 1, "701100": 2, "706100": 3, "443100": 4, "661100": 5, "664100": 6,
            "422000": 7, "431300": 8, "447200": 9}


def make_client(**kw: Any) -> FakeOdooClient:
    c = FakeOdooClient(accounts=dict(ACCOUNTS), **kw)
    c.journals.update({"VTE": 10, "PAIE": 11, "CAU": 12, "CAF": 13, "BQU": 14, "BQF": 15})
    c.journal_accounts.update({"CAU": 21, "CAF": 22, "BQU": 23, "BQF": 24})
    return c


class Env:
    def __init__(self, client: FakeOdooClient | None = None) -> None:
        self.client = client or make_client()
        self.queue = SqlitePendingQueue()
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
        self.handlers = AccountingEventHandlers(
            OdooAccountingPoster(self.client), self.queue, AccountMapping.load(),
            publish=lambda n, p: self.published.append((n, p)), clock=lambda: self.now)


class Ev:
    """Événement minimal (duck typing du Event du Core : type, payload)."""

    def __init__(self, type: str, payload: dict[str, Any]) -> None:  # noqa: A002
        self.type, self.payload = type, payload


def doc(number: str = "F-2026-0001", currency: str = "USD", **over: Any) -> dict[str, Any]:
    """Forme de ``InvoiceDTO.model_dump(mode='json')`` (payload réel de Ventes)."""
    d: dict[str, Any] = {
        "number": number, "invoice_date": "2026-11-20", "currency": currency,
        "seller": {"name": "Vorpal SARL", "nif": "A0000001"},
        "customer": {"name": "Client SARL", "nif": "A1234567", "rccm": None, "address": None},
        "lines": [{"designation": "Article", "quantity": "1", "unit_price_ht": "150.00",
                   "tax_group": "B", "vat_rate": "16", "item_code": None}],
        "total_ht": "150.00", "total_vat": "24.00", "total_ttc": "174.00",
        "invoice_type": "vente", "original_invoice_ref": None,
        "non_taxable_amount": "0", "other_taxes": "0"}
    d.update(over)
    return d


def ready(d: dict[str, Any]) -> Ev:
    return Ev("invoice.ready_for_certification", {"invoice": d})


def certified(number: str = "F-2026-0001", etype: str = "invoice.certified", **over: Any) -> Ev:
    """Forme réelle du payload DGI (cf. CertificationService)."""
    p: dict[str, Any] = {
        "invoice_id": number, "serial_number": number, "is_credit_note": etype != "invoice.certified",
        "fiscal_device_id": "DEV-1", "authentication_code": "DGI-ABC", "qr_code": "QR-DATA",
        "certified_at": "2026-11-20T10:00:00+00:00"}
    p.update(over)
    return Ev(etype, p)
