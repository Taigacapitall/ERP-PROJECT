"""Adaptateur EventBus du port SalesEventPublisher."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from vorpal.core.contracts import InvoiceDTO
from vorpal.core.contracts.events import INVOICE_READY_FOR_CERTIFICATION, PAYMENT_RECEIVED
from vorpal.core.events import Event, EventBus
from vorpal.modules.ventes.domain.payments import Payment
from vorpal.modules.ventes.ports.publisher import SalesEventPublisher


class EventBusSalesPublisher(SalesEventPublisher):
    """Publie les evenements Ventes sur l'EventBus (a appeler uniquement apres commit)."""

    def __init__(self, event_bus: EventBus, clock: Callable[[], datetime]) -> None:
        self._bus = event_bus
        self._clock = clock

    def invoice_ready_for_certification(self, invoice: InvoiceDTO) -> None:
        self._bus.publish(
            Event(
                type=INVOICE_READY_FOR_CERTIFICATION,
                payload={"invoice": invoice.model_dump(mode="json")},
                occurred_at=self._clock(),
                correlation_id=invoice.number,
                source_module="ventes",
            )
        )

    def payment_received(self, payment: Payment) -> None:
        # TODO(A_VALIDER): payload definitif de payment.received.
        self._bus.publish(
            Event(
                type=PAYMENT_RECEIVED,
                payload={
                    "payment_id": payment.id,
                    "customer_id": payment.customer_id,
                    "amount": str(payment.amount.amount),
                    "currency": payment.currency.value,
                    "payment_date": payment.payment_date.isoformat(),
                    "method": payment.method.value,
                },
                occurred_at=self._clock(),
                correlation_id=payment.id,
                source_module="ventes",
            )
        )
