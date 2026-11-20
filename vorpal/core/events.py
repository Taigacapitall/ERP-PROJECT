from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Callable
from uuid import uuid4

from vorpal.core.contracts.events import EVENT_HANDLER_FAILED


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
    source_module: str | None = None


EventHandler = Callable[[Event], None]


class EventStore(ABC):
    @abstractmethod
    def append(self, event: Event) -> None:
        """Persiste un événement."""

    @abstractmethod
    def list_events(self, event_type: str | None = None) -> list[Event]:
        """Retourne les événements persistés (filtrables par type)."""


class InMemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, event: Event) -> None:
        self._events.append(event)

    def list_events(self, event_type: str | None = None) -> list[Event]:
        if event_type is None:
            return list(self._events)
        return [e for e in self._events if e.type == event_type]


class EventBus:
    def __init__(
        self,
        store: EventStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._processed: dict[int, set[str]] = defaultdict(set)
        self._store = store
        self._logger = logger or logging.getLogger("vorpal.core.events")

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        if self._store is not None:
            self._store.append(event)

        candidates = list(self._handlers.get(event.type, ()))
        if event.type != "*":
            candidates.extend(self._handlers.get("*", ()))

        for handler in candidates:
            handler_key = id(handler)
            if event.id in self._processed[handler_key]:
                continue
            try:
                handler(event)
                self._processed[handler_key].add(event.id)
            except Exception as exc:
                self._logger.exception(
                    "Erreur dans le handler %r sur l'événement %s (%s): %s",
                    handler,
                    event.type,
                    event.id,
                    exc,
                )
                if event.type != EVENT_HANDLER_FAILED:
                    failed_event = Event(
                        type=EVENT_HANDLER_FAILED,
                        payload={
                            "failed_event_id": event.id,
                            "failed_event_type": event.type,
                            "handler_name": getattr(handler, "__qualname__", repr(handler)),
                            "error": str(exc),
                        },
                        correlation_id=event.correlation_id or event.id,
                        source_module="core.events",
                    )
                    self.publish(failed_event)

    def replay(self, event_type: str | None = None) -> int:
        if self._store is None:
            return 0
        events = self._store.list_events(event_type=event_type)
        for ev in events:
            for handler_key in list(self._processed.keys()):
                self._processed[handler_key].discard(ev.id)
            candidates = list(self._handlers.get(ev.type, ()))
            if ev.type != "*":
                candidates.extend(self._handlers.get("*", ()))
            for handler in candidates:
                handler_key = id(handler)
                try:
                    handler(ev)
                    self._processed[handler_key].add(ev.id)
                except Exception as exc:
                    self._logger.exception("Erreur lors du replay de %s: %s", ev.id, exc)
        return len(events)
