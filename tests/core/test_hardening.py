from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from vorpal.core.contracts.dto import (
    InvoiceDTO,
    InvoiceLineDTO,
    JournalEntryDTO,
    JournalLineDTO,
    PartyInfoDTO,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
from vorpal.core.errors import ConflictError, ValidationError
from vorpal.core.events import Event, EventBus, InMemoryEventStore
from vorpal.core.money import Currency, Money
from vorpal.core.services import ServiceRegistry


# ---------- Money ----------
@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), "NaN", "-Infinity", True])
def test_money_rejects_nan_infinity_bool(bad: object) -> None:
    with pytest.raises(ValidationError):
        Money(amount=bad, currency=Currency.USD)  # type: ignore[arg-type]


# ---------- ServiceRegistry ----------
class _Svc:
    pass


def test_service_registry_duplicate_conflict_and_replace() -> None:
    reg = ServiceRegistry()
    first, second = _Svc(), _Svc()
    reg.register(_Svc, first)
    with pytest.raises(ConflictError):
        reg.register(_Svc, second)
    assert reg.get(_Svc) is first
    reg.register(_Svc, second, replace=True)
    assert reg.get(_Svc) is second


# ---------- EventBus ----------
class _Counter:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def on_event(self, ev: Event) -> None:
        self.seen.append(ev.id)


def test_bus_idempotence_with_bound_methods_and_wildcard_dedup() -> None:
    bus = EventBus()
    counter = _Counter()
    bus.subscribe("demo.ping", counter.on_event)
    bus.subscribe("*", counter.on_event)  # même handler sur type + wildcard
    ev = Event(id="e1", type="demo.ping")
    bus.publish(ev)
    bus.publish(ev)
    assert counter.seen == ["e1"]  # exécuté une seule fois au total


def test_bus_replay_publishes_handler_failed() -> None:
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    failures: list[str] = []

    def boom(ev: Event) -> None:
        raise RuntimeError("x")

    bus.subscribe("demo.ping", boom)
    bus.subscribe(EVENT_HANDLER_FAILED, lambda ev: failures.append(str(ev.payload["failed_event_id"])))
    bus.publish(Event(id="e1", type="demo.ping"))
    assert failures == ["e1"]
    bus.replay("demo.ping")
    assert failures == ["e1", "e1"]  # le replay signale aussi l'échec


# ---------- DTO ----------
def _party(name: str) -> PartyInfoDTO:
    return PartyInfoDTO(name=name, nif="A123", rccm="CD/LSH/1", address="Lubumbashi")


def _invoice(**over: object) -> InvoiceDTO:
    data: dict[str, object] = {
        "number": "F-1",
        "invoice_date": date(2026, 11, 20),
        "seller": _party("Vendeur"),
        "customer": _party("Client"),
        "lines": [InvoiceLineDTO(designation="S", quantity=Decimal(2), unit_price_ht=Decimal("50.00"), vat_rate=Decimal("0.16"))],
        "currency": Currency.USD,
        "total_ht": Decimal("100.00"),
        "total_vat": Decimal("16.00"),
        "total_ttc": Decimal("116.00"),
    }
    data.update(over)
    return InvoiceDTO(**data)  # type: ignore[arg-type]


def test_invoice_valid() -> None:
    assert _invoice().total_ttc == Decimal("116.00")


def test_invoice_ttc_mismatch_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _invoice(total_ttc=Decimal("120.00"))


def test_invoice_ht_not_matching_lines_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _invoice(total_ht=Decimal("90.00"), total_vat=Decimal("16.00"), total_ttc=Decimal("106.00"))


def test_invoice_float_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _invoice(total_ht=100.0)


def _je(lines: list[JournalLineDTO]) -> JournalEntryDTO:
    return JournalEntryDTO(entry_date=date(2026, 11, 20), journal="VT", label="x", lines=lines, source_ref="r1")


def test_journal_balanced_per_currency() -> None:
    ok = _je([
        JournalLineDTO(account="411", debit=Decimal(10), currency=Currency.USD),
        JournalLineDTO(account="701", credit=Decimal(10), currency=Currency.USD),
        JournalLineDTO(account="411", debit=Decimal(500), currency=Currency.CDF),
        JournalLineDTO(account="701", credit=Decimal(500), currency=Currency.CDF),
    ])
    assert len(ok.lines) == 4


def test_journal_cross_currency_imbalance_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _je([
            JournalLineDTO(account="411", debit=Decimal(100), currency=Currency.USD),
            JournalLineDTO(account="701", credit=Decimal(100), currency=Currency.CDF),
        ])


def test_store_does_not_duplicate_republished_event() -> None:
    store = InMemoryEventStore()
    bus = EventBus(store=store)
    ev = Event(id="e1", type="demo.ping")
    bus.publish(ev)
    bus.publish(ev)
    assert len(store.list_events("demo.ping")) == 1
