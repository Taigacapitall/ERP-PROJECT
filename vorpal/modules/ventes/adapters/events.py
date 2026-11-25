"""Adaptateur EventBus du port SalesEventPublisher."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from vorpal.core.contracts import InvoiceDTO
from vorpal.core.contracts.events import INVOICE_READY_FOR_CERTIFICATION, PAYMENT_RECEIVED
from vorpal.core.events import Event, EventBus
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import SalesInvoice
from vorpal.modules.ventes.domain.payments import Payment
from vorpal.modules.ventes.ports.publisher import SalesEventPublisher

# Evenements propres a Ventes (hors catalogue Core, comme ceux d'Achats) : facturation classique.
INVOICE_ISSUED = "ventes.invoice.issued"
CREDIT_NOTE_ISSUED = "ventes.credit_note.issued"
PAYMENT_CANCELLED = "ventes.payment.cancelled"


def _document_payload(doc: SalesInvoice, customer: Customer) -> dict[str, object]:
    """Montants en chaines (jamais de float). TODO(A_VALIDER) : payload definitif."""
    if doc.number is None or doc.total_ht is None or doc.total_vat is None or doc.total_ttc is None:
        raise ValueError("Document non valide : numero ou totaux manquants.")
    return {
        "mode": "classique",
        "number": doc.number,
        "invoice_type": doc.invoice_type,
        "invoice_date": doc.invoice_date.isoformat(),
        "currency": doc.currency.value,
        "total_ht": str(doc.total_ht.amount),
        "total_vat": str(doc.total_vat.amount),
        "total_ttc": str(doc.total_ttc.amount),
        "customer_id": customer.id,
        "customer_name": customer.name,
        "customer_nif": customer.nif,
        "original_invoice_number": doc.original_invoice_number,
    }


class EventBusSalesPublisher(SalesEventPublisher):
    """Publie les evenements Ventes sur l'EventBus (a appeler uniquement apres commit)."""

    def __init__(self, event_bus: EventBus, clock: Callable[[], datetime]) -> None:
        self._bus = event_bus
        self._clock = clock

    def _publish(self, event_type: str, payload: dict[str, object], correlation_id: str) -> None:
        self._bus.publish(
            Event(
                type=event_type,
                payload=payload,
                occurred_at=self._clock(),
                correlation_id=correlation_id,
                source_module="ventes",
            )
        )

    def invoice_ready_for_certification(self, invoice: InvoiceDTO) -> None:
        self._publish(
            INVOICE_READY_FOR_CERTIFICATION, {"invoice": invoice.model_dump(mode="json")}, invoice.number
        )

    def payment_received(self, payment: Payment) -> None:
        # TODO(A_VALIDER): payload definitif de payment.received.
        self._publish(
            PAYMENT_RECEIVED,
            {
                "payment_id": payment.id,
                "customer_id": payment.customer_id,
                "amount": str(payment.amount.amount),
                "currency": payment.currency.value,
                "payment_date": payment.payment_date.isoformat(),
                "method": payment.method.value,
            },
            payment.id,
        )

    def invoice_issued(self, invoice: SalesInvoice, customer: Customer) -> None:
        self._publish(INVOICE_ISSUED, _document_payload(invoice, customer), str(invoice.number))

    def credit_note_issued(self, note: SalesInvoice, customer: Customer) -> None:
        self._publish(CREDIT_NOTE_ISSUED, _document_payload(note, customer), str(note.number))

    def payment_cancelled(self, payment: Payment) -> None:
        self._publish(
            PAYMENT_CANCELLED,
            {
                "payment_id": payment.id,
                "customer_id": payment.customer_id,
                "amount": str(payment.amount.amount),
                "currency": payment.currency.value,
            },
            payment.id,
        )
