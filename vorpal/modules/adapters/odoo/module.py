"""Module Vorpal « comptabilité (Odoo) » : câblage EventBus réel du Core v1."""
from __future__ import annotations

from vorpal.core.context import AppContext
from vorpal.core.contracts.events import JOURNAL_ENTRY_POSTED
from vorpal.core.events import Event
from vorpal.core.module import BaseModule, ModuleManifest

from .handlers import EVENTS, AccountingEventHandlers
from .poster import OdooAccountingPoster

ODOO_MANIFEST = ModuleManifest(
    name="comptabilite_odoo", version="0.1.0", depends=[], provides=["accounting_posting"],
    subscribes=list(EVENTS), permissions=[])


class OdooAccountingModule(BaseModule):
    """``handlers`` est construit par l'appelant (client XML-RPC ou Fake, file SQLite, mapping).
    Le publisher `journal.entry.posted` est branché au démarrage sur ``ctx.event_bus``."""

    manifest = ODOO_MANIFEST

    def __init__(self, poster: OdooAccountingPoster, handlers: AccountingEventHandlers) -> None:
        self.poster = poster
        self.handlers = handlers
        self._running = False

    def install(self, ctx: AppContext) -> None:
        return None  # tables SQLite créées à la construction de la file/boîte ; rien dans le Core

    def start(self, ctx: AppContext) -> None:
        def publish(event_type: str, payload: dict[str, object]) -> None:
            assert event_type == JOURNAL_ENTRY_POSTED
            ctx.event_bus.publish(Event(
                type=JOURNAL_ENTRY_POSTED, payload=payload, occurred_at=ctx.clock(),
                correlation_id=str(payload["source_ref"]), source_module="comptabilite_odoo"))

        self.handlers._publish = publish  # noqa: SLF001 - câblage du composition root
        for event_type in EVENTS:
            ctx.event_bus.subscribe(event_type, self.handlers.handle)
        self._running = True

    def process_pending(self, limit: int = 100) -> int:
        return self.handlers.process_pending(limit)

    def stop(self) -> None:
        self._running = False

    def healthcheck(self) -> bool:
        return self._running
