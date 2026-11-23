"""Conformité aux contrats RÉELS du Core v1 (EventBus, Event, DTO, AccountingPoster).

Nécessite pydantic (dépendance du projet) : ignoré si absent. ÉCRIT MAIS NON EXÉCUTÉ lors de la
session d'intégration (environnement sans pydantic/SQLAlchemy/pytest).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic")

from vorpal.core.contracts import (  # noqa: E402
    AccountingPoster,
    InvoiceDTO,
    InvoiceLineDTO,
    JournalEntryDTO,
    JournalLineDTO,
    PartyInfoDTO,
)
from vorpal.core.events import Event, EventBus, InMemoryEventStore  # noqa: E402
from vorpal.core.money import Currency  # noqa: E402
from vorpal.modules.adapters.odoo.core_poster import CoreAccountingPoster  # noqa: E402
from vorpal.modules.adapters.odoo.module import OdooAccountingModule  # noqa: E402
from vorpal.modules.adapters.odoo.poster import OdooAccountingPoster  # noqa: E402

from .helpers import Env  # noqa: E402


def _invoice() -> InvoiceDTO:
    return InvoiceDTO(
        number="F-2026-0009", invoice_date=date(2026, 11, 20),
        seller=PartyInfoDTO(name="Vorpal SARL", nif="A1"),
        customer=PartyInfoDTO(name="Client SARL", nif="A2"),
        lines=[InvoiceLineDTO(designation="X", quantity=Decimal(1),
                              unit_price_ht=Decimal("100.00"), vat_rate=Decimal(16))],
        currency=Currency.USD, total_ht=Decimal("100.00"), total_vat=Decimal("16.00"),
        total_ttc=Decimal("116.00"))


def test_core_poster_satisfies_accounting_poster_protocol() -> None:
    assert isinstance(CoreAccountingPoster(OdooAccountingPoster(Env().client)), AccountingPoster)


def test_core_journal_entry_dto_is_posted_idempotently() -> None:
    env = Env()
    poster = CoreAccountingPoster(OdooAccountingPoster(env.client))
    entry = JournalEntryDTO(
        entry_date=date(2026, 11, 20), journal="VTE", label="Test", source_ref="core:1",
        lines=[JournalLineDTO(account="4111", debit=Decimal("10"), currency=Currency.USD),
               JournalLineDTO(account="7011", credit=Decimal("10"), currency=Currency.USD)])
    assert poster.post(entry) == poster.post(entry)
    assert len(env.client.moves) == 1


def test_eventbus_end_to_end_ready_then_certified() -> None:
    env = Env()
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    ctx = SimpleNamespace(event_bus=bus, clock=lambda: env.now)
    module = OdooAccountingModule(OdooAccountingPoster(env.client), env.handlers)
    module.start(ctx)  # type: ignore[arg-type]
    inv = _invoice()
    bus.publish(Event(type="invoice.ready_for_certification",
                      payload={"invoice": inv.model_dump(mode="json")}, correlation_id=inv.number))
    assert not env.client.moves  # jamais de comptabilisation avant le cachet DGI
    bus.publish(Event(type="invoice.certified", payload={
        "invoice_id": inv.number, "serial_number": inv.number, "is_credit_note": False,
        "fiscal_device_id": "D1", "authentication_code": "AUTH", "qr_code": "QR",
        "certified_at": "2026-11-20T10:00:00+00:00"}, correlation_id=inv.number))
    assert len(env.client.moves) == 1
    assert len(store.list_events("journal.entry.posted")) == 1
    bus.replay()  # rejeu complet : aucun doublon
    assert len(env.client.moves) == 1
