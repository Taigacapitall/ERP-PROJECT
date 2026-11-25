"""Redémarrage sur base fichier : les écritures, les exercices et la numérotation survivent."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from vorpal.modules.comptabilite.errors import AccountingConflictError

from .conftest import _build

D = Decimal
SALE = [("4111", "116", "0"), ("7011", "0", "100"), ("4431", "0", "16")]


def test_restart_keeps_entries_numbers_and_idempotence(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'compta.db'}"
    first = _build(db_url=url)
    a = first.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    first.ctx.db.dispose()

    second = _build(db_url=url)  # nouveau processus simulé : migrations rejouées sans effet
    assert second.post("S1", date(2026, 11, 2), SALE, journal="VTE").id == a.id
    assert second.post("S2", date(2026, 11, 3), SALE, journal="VTE").number == "VTE-2026-000002"
    tb = second.module.reports.trial_balance(date(2026, 1, 1), date(2026, 12, 31))  # type: ignore[union-attr]
    assert tb.is_balanced and tb.total_period_debit == D("232.00")
    second.ctx.db.dispose()


def test_database_refuses_duplicate_source_ref_and_number(tmp_path: Path):
    env = _build(db_url=f"sqlite:///{tmp_path / 'c.db'}")
    env.post("S1", date(2026, 11, 2), SALE, journal="VTE")
    with env.ctx.db.begin() as conn:
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(text("UPDATE compta_entries SET source_ref = source_ref"))  # sans effet
            conn.execute(
                text(
                    "INSERT INTO compta_entries (id, number, journal_code, entry_date, fiscal_year, source_ref, reference,"
                    " label, currency, company_currency, exchange_rate, kind, posted_at)"
                    " SELECT 'X', 'Y', journal_code, entry_date, fiscal_year, source_ref, '', '', currency,"
                    " company_currency, exchange_rate, kind, posted_at FROM compta_entries"
                )
            )
    env.ctx.db.dispose()


def test_sequential_numbers_without_gap_over_many_postings(tmp_path: Path):
    env = _build(db_url=f"sqlite:///{tmp_path / 'n.db'}")
    numbers = [env.post(f"S{i}", date(2026, 11, 2), SALE, journal="VTE").number for i in range(25)]
    assert numbers == [f"VTE-2026-{i:06d}" for i in range(1, 26)]
    env.ctx.db.dispose()


def test_conflicting_replay_after_restart_is_refused(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'r.db'}"
    _build(db_url=url).post("S1", date(2026, 11, 2), SALE, journal="VTE")
    again = _build(db_url=url)
    with pytest.raises(AccountingConflictError):
        again.post("S1", date(2026, 11, 2), [("4111", "5", "0"), ("7011", "0", "5")], journal="VTE")
