"""Comptabilisation : idempotence, contrôles de période, devises, numérotation, extourne, permissions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from vorpal.core import Currency, ServiceRegistry
from vorpal.core.contracts import AccountingPoster, JournalEntryDTO, JournalLineDTO
from vorpal.modules.comptabilite.domain.accounts import Journal, JournalKind
from vorpal.modules.comptabilite.domain.entries import DraftEntry, DraftLine, EntryKind
from vorpal.modules.comptabilite.errors import (
    AccountingConflictError,
    AccountingNotFoundError,
    AccountingPermissionError,
    AccountingValidationError,
    PeriodClosedError,
    UnbalancedEntryError,
)

from .conftest import Env

D = Decimal
SALE = [("4111", "116", "0"), ("7011", "0", "100"), ("4431", "0", "16")]


def test_post_persists_complete_entry_and_publishes_event_after_commit(env: Env):
    posted = env.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    assert posted.number == "VTE-2026-000001"
    assert posted.total_debit == posted.total_credit == D("116.00")
    assert [e.payload["number"] for e in env.posted_events] == ["VTE-2026-000001"]
    assert env.module.uow_factory is not None
    with env.module.uow_factory() as uow:
        stored = uow.ledger.get_entry_by_source_ref("S1")
    assert stored is not None and stored.fiscal_year == "2026"
    assert [(ln.account_code, ln.debit, ln.credit) for ln in stored.lines] == [
        ("4111", D("116.00"), D("0.00")),
        ("7011", D("0.00"), D("100.00")),
        ("4431", D("0.00"), D("16.00")),
    ]


def test_replay_returns_same_entry_without_new_number_or_event(env: Env):
    first = env.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    again = env.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    assert again.id == first.id and again.number == first.number
    assert len(env.posted_events) == 1
    nxt = env.post("S2", date(2026, 11, 3), SALE, journal="VTE")
    assert nxt.number == "VTE-2026-000002"


def test_same_source_ref_with_different_content_is_a_conflict(env: Env):
    env.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    other = [("4111", "200", "0"), ("7011", "0", "200")]
    with pytest.raises(AccountingConflictError):
        env.post("S1", date(2026, 11, 2), other, journal="VTE")


def test_numbering_is_per_journal_and_not_consumed_by_failures(env: Env):
    with pytest.raises(UnbalancedEntryError):
        env.post("BAD", date(2026, 11, 2), [("4111", "10", "0"), ("7011", "0", "9")], journal="VTE")
    with pytest.raises(AccountingNotFoundError):
        env.post("BAD2", date(2026, 11, 2), [("999999", "10", "0"), ("7011", "0", "10")], journal="VTE")
    assert env.post("A", date(2026, 11, 2), SALE, journal="VTE").number == "VTE-2026-000001"
    assert env.post("B", date(2026, 11, 2), SALE, journal="OD").number == "OD-2026-000001"


def test_unknown_journal_and_inactive_account_are_refused(env: Env):
    with pytest.raises(AccountingNotFoundError):
        env.post("X", date(2026, 11, 2), SALE, journal="ZZZ")
    assert env.module.chart is not None
    env.module.chart.set_account_active("4431", False)
    with pytest.raises(AccountingNotFoundError):
        env.post("Y", date(2026, 11, 2), SALE, journal="VTE")


def test_no_fiscal_year_or_locked_period_refuses_posting(env: Env):
    with pytest.raises(PeriodClosedError):
        env.post("OLD", date(2025, 6, 1), SALE, journal="VTE")
    assert env.module.closing is not None
    env.module.closing.lock_until(date(2026, 6, 30))
    with pytest.raises(PeriodClosedError):
        env.post("LOCKED", date(2026, 6, 30), SALE, journal="VTE")
    env.post("OK", date(2026, 7, 1), SALE, journal="VTE")
    with pytest.raises(AccountingConflictError):
        env.module.closing.lock_until(date(2026, 5, 1))


def test_journal_currency_is_enforced(env: Env):
    with pytest.raises(AccountingValidationError):
        env.post("C", date(2026, 11, 2), [("5711", "10", "0"), ("7011", "0", "10")], journal="CAF", currency="USD")


def test_foreign_currency_needs_a_rate_and_is_converted(env: Env):
    lines = [("5211", "2800000", "0"), ("7011", "0", "2800000")]
    with pytest.raises(AccountingValidationError):
        env.post("F1", date(2026, 11, 2), lines, journal="BQF", currency="CDF")
    draft = DraftEntry(
        "F2", "BQF", date(2026, 11, 2), "CDF",
        (DraftLine("5211", debit=D("2800000")), DraftLine("7011", credit=D("2800000"))),
        exchange_rate=D("0.00035"),
    )
    assert env.module.posting is not None
    posted = env.module.posting.post(draft)
    assert posted.total_debit == posted.total_credit == D("980.00")
    assert posted.lines[0].tx_debit == D("2800000")


def test_reversal_mirrors_links_and_is_idempotent(env: Env):
    assert env.module.posting is not None
    first = env.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    rev = env.module.posting.reverse(first.id, on_date=date(2026, 11, 5), reason="erreur de prix")
    assert rev.reversal_of == first.id and "erreur de prix" in rev.label
    assert [(ln.debit, ln.credit) for ln in rev.lines] == [(ln.credit, ln.debit) for ln in first.lines]
    again = env.module.posting.reverse(first.id, on_date=date(2026, 11, 6))
    assert again.id == rev.id
    with pytest.raises(AccountingConflictError):
        env.module.posting.reverse(rev.id)
    assert env.module.uow_factory is not None
    with env.module.uow_factory() as uow:
        assert uow.ledger.get_entry(first.id).reversed_by == rev.id  # type: ignore[union-attr]


def test_closing_entries_cannot_be_reversed(env: Env):
    assert env.module.posting is not None and env.module.closing is not None
    env.post("R", date(2026, 3, 1), [("5212", "10", "0"), ("7011", "0", "10")], journal="BQU")
    closing = env.module.closing.close_fiscal_year("2026")
    assert closing is not None and closing.kind == EntryKind.CLOSING
    with pytest.raises(AccountingConflictError):
        env.module.posting.reverse(closing.id)


def test_core_port_posts_entries_and_resolves_short_codes(env: Env):
    poster = env.ctx.services.get_optional(cast(type[object], AccountingPoster))
    assert isinstance(poster, AccountingPoster)
    dto = JournalEntryDTO(
        entry_date=date(2026, 11, 2), journal="VTE", label="Facture F1", source_ref="invoice.certified:F1",
        lines=[
            JournalLineDTO(account="4111", debit=D("116"), currency=Currency.USD),
            JournalLineDTO(account="7011", credit=D("100"), currency=Currency.USD),
            JournalLineDTO(account="4431", credit=D("16"), currency=Currency.USD),
        ],
    )
    entry_id = poster.post(dto)
    assert poster.post(dto) == entry_id  # idempotent
    mixed = dto.model_copy(update={
        "source_ref": "M1",
        "lines": [
            JournalLineDTO(account="4111", debit=D("1"), currency=Currency.USD),
            JournalLineDTO(account="7011", credit=D("1"), currency=Currency.CDF),
        ],
    })
    with pytest.raises(AccountingValidationError):
        poster.post(mixed)


def test_service_enforces_permissions(env: Env):
    assert env.module.service is not None
    svc = env.module.service
    draft = DraftEntry("P1", "OD", date(2026, 11, 2), "USD",
                       (DraftLine("5212", debit=D("5")), DraftLine("7011", credit=D("5"))))
    with pytest.raises(AccountingPermissionError):
        svc.post_entry(env.reader, draft)
    posted = svc.post_entry(env.admin, draft)
    assert svc.trial_balance(env.reader, date(2026, 11, 1), date(2026, 11, 30)).is_balanced
    with pytest.raises(AccountingPermissionError):
        svc.close_fiscal_year(env.reader, "2026")
    with pytest.raises(AccountingPermissionError):
        svc.reverse_entry(env.reader, posted.id)
    svc.add_journal(env.admin, Journal("DIV", "Divers", JournalKind.MISC))
    with pytest.raises(AccountingConflictError):
        svc.add_journal(env.admin, Journal("DIV", "Divers", JournalKind.MISC))


def test_module_registers_core_port_once(env: Env):
    registry: ServiceRegistry = env.ctx.services
    assert registry.get_optional(cast(type[object], AccountingPoster)) is not None
