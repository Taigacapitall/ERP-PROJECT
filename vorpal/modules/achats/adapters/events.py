"""Adaptateur de publication d'evenements du module Achats vers EventBus."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from vorpal.core.events import Event, EventBus
from vorpal.modules.achats.ports.publisher import PurchaseEventPublisher


class EventBusPurchasePublisher(PurchaseEventPublisher):
    """Publie les evenements metier du module Achats via vorpal.core.EventBus."""

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
        event = Event(
            type=event_type,
            payload=dict(payload),
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            source_module="achats",
        )
        self._event_bus.publish(event)
