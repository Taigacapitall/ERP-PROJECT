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
from vorpal.core.errors import ConflictError, ValidationError
from vorpal.core.events import Event, EventBus
from vorpal.core.money import Currency, Money
from vorpal.core.services import ServiceRegistry


@pytest.mark.parametrize("bad", [1.5, True, Decimal("NaN"), Decimal("Infinity"), "NaN"])
def test_money_rejects_float_bool_nan_inf(bad: object) -> None:
    with pytest.raises(ValidationError):
        Money(bad, Currency.USD)  # type: ignore[arg-type]


def test_bound_method_on_two_subscriptions_runs_once() -> None:
    class Counter:
        n = 0

        def handle(self, ev: Event) -> None:
            self.n += 1

    c = Counter()
    bus = EventBus()
    bus.subscribe("a", c.handle)
    bus.subscribe("*", c.handle)
    bus.publish(Event(type="a"))
    assert c.n == 1


def test_replay_publishes_handler_failed() -> None:
    from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
    from vorpal.core.events import InMemoryEventStore

    bus = EventBus(store=InMemoryEventStore())
    failed: list[str] = []

    def bad(ev: Event) -> None:
        raise ValueError("boom")

    bus.subscribe("x", bad)
    bus.subscribe(EVENT_HANDLER_FAILED, lambda ev: failed.append(str(ev.payload["failed_event_id"])))
    bus.publish(Event(id="e1", type="x"))
    bus.replay("x")
    assert failed == ["e1", "e1"]


def test_service_registry_duplicate_conflict_and_replace() -> None:
    class Iface: ...

    reg = ServiceRegistry()
    reg.register(Iface, Iface())
    with pytest.raises(ConflictError):
        reg.register(Iface, Iface())
    reg.register(Iface, Iface(), replace=True)


def _invoice(**over: object) -> InvoiceDTO:
    data: dict[str, object] = {
        "number": "F-1",
        "invoice_date": date(2026, 11, 20),
        "seller": PartyInfoDTO(name="V", nif="N1", rccm="R1", address="Lubumbashi"),
        "customer": PartyInfoDTO(name="C", nif="N2"),
        "lines": [InvoiceLineDTO(designation="S", quantity=Decimal(1), unit_price_ht=Decimal("100.00"), vat_rate=Decimal("0.16"))],
        "currency": Currency.USD,
        "total_ht": Decimal("100.00"),
        "total_vat": Decimal("16.00"),
        "total_ttc": Decimal("116.00"),
    }
    data.update(over)
    return InvoiceDTO(**data)  # type: ignore[arg-type]


def test_invoice_totals_must_be_coherent_and_no_float() -> None:
    _invoice()
    with pytest.raises(PydanticValidationError):
        _invoice(total_ttc=Decimal("120.00"))
    with pytest.raises(PydanticValidationError):
        _invoice(total_ht=Decimal("500.00"), total_ttc=Decimal("516.00"))
    with pytest.raises(PydanticValidationError):
        _invoice(total_vat=16.0)


def test_journal_entry_balanced_per_currency() -> None:
    with pytest.raises(PydanticValidationError):
        JournalEntryDTO(
            entry_date=date(2026, 11, 20),
            journal="OD",
            label="x",
            source_ref="s1",
            lines=[
                JournalLineDTO(account="411", debit=Decimal(100), currency=Currency.USD),
                JournalLineDTO(account="701", credit=Decimal(100), currency=Currency.CDF),
            ],
        )
