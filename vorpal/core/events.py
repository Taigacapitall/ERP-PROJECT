from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from vorpal.core.contracts.events import EVENT_HANDLER_FAILED


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
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
        self._ids: set[str] = set()

    def append(self, event: Event) -> None:
        if event.id in self._ids:  # republier le même événement ne le stocke pas deux fois
            return
        self._ids.add(event.id)
        self._events.append(event)

    def list_events(self, event_type: str | None = None) -> list[Event]:
        if event_type is None:
            return list(self._events)
        return [e for e in self._events if e.type == event_type]


class EventBus:
    """Bus synchrone. Un handler en échec n'est pas marqué traité : il est réessayé
    à la republication ou au replay (chaque échec publie `event.handler_failed`)."""

    def __init__(
        self,
        store: EventStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        # Clé d'idempotence stable par handler (égalité, pas id()) : une méthode liée
        # abonnée à deux types d'événements reste UN seul handler.
        self._handler_keys: list[tuple[EventHandler, int]] = []
        self._processed: dict[int, set[str]] = defaultdict(set)
        self._store = store
        self._logger = logger or logging.getLogger("vorpal.core.events")

    def _key_of(self, handler: EventHandler) -> int:
        for known, key in self._handler_keys:
            if known == handler:
                return key
        key = len(self._handler_keys)
        self._handler_keys.append((handler, key))
        return key

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
        self._key_of(handler)

    def _candidates(self, event: Event) -> list[EventHandler]:
        found = list(self._handlers.get(event.type, ()))
        if event.type != "*":
            found.extend(self._handlers.get("*", ()))
        return found

    def _dispatch(self, event: Event) -> None:
        for handler in self._candidates(event):
            key = self._key_of(handler)
            if event.id in self._processed[key]:
                continue
            try:
                handler(event)
                self._processed[key].add(event.id)
            except Exception as exc:
                self._logger.exception(
                    "Erreur dans le handler %r sur l'événement %s (%s)",
                    handler,
                    event.type,
                    event.id,
                )
                if event.type != EVENT_HANDLER_FAILED:
                    self.publish(
                        Event(
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
                    )

    def publish(self, event: Event) -> None:
        if self._store is not None:
            self._store.append(event)
        self._dispatch(event)

    def replay(self, event_type: str | None = None) -> int:
        """Rejoue les événements persistés (sans les ré-enregistrer) vers tous les handlers."""
        if self._store is None:
            return 0
        events = self._store.list_events(event_type=event_type)
        for ev in events:
            for done in self._processed.values():
                done.discard(ev.id)
            self._dispatch(ev)
        return len(events)
