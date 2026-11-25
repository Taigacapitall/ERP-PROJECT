"""Adaptateurs EventBus du module Stock : publication et ecoute des evenements Achats."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from vorpal.core.events import Event, EventBus
from vorpal.modules.stock.ports.publisher import StockEventPublisher
from vorpal.modules.stock.services.ingestion import PurchaseIngestionService


class EventBusStockPublisher(StockEventPublisher):
    """Publie les evenements metier du module Stock via vorpal.core.EventBus."""

    def __init__(
        self,
        event_bus: EventBus,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._clock = clock

    def publish_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        occurred_at = self._clock() if self._clock is not None else datetime.now(UTC)
        self._event_bus.publish(
            Event(
                type=event_type,
                payload=dict(payload),
                occurred_at=occurred_at,
                correlation_id=correlation_id,
                source_module="stock",
            )
        )


class GoodsReceiptValidatedHandler:
    """Handler de `achats.goods_receipt.validated` ; `enabled=False` le neutralise a l'arret.

    Le bus du Core n'offre pas de desabonnement : le module desactive donc son handler.
    Une exception est volontairement propagee : le bus la signale (`event.handler_failed`) et
    reessaiera au rejeu.
    """

    def __init__(self, service: PurchaseIngestionService) -> None:
        self._service = service
        self.enabled = True

    def __call__(self, event: Event) -> None:
        if self.enabled:
            self._service.ingest_goods_receipt(event.payload)


class LandedCostAllocatedHandler:
    """Handler de `achats.landed_cost.allocated` (meme principe que le handler de reception)."""

    def __init__(self, service: PurchaseIngestionService) -> None:
        self._service = service
        self.enabled = True

    def __call__(self, event: Event) -> None:
        if self.enabled:
            self._service.ingest_landed_cost(event.payload)
