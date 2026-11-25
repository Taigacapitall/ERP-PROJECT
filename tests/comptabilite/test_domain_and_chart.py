"""Domaine (écritures, conversion) et plan comptable."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from vorpal.modules.comptabilite.domain.accounts import Account
from vorpal.modules.comptabilite.domain.entries import DraftEntry, DraftLine, convert_lines
from vorpal.modules.comptabilite.errors import (
    AccountingNotFoundError,
    AccountingValidationError,
    UnbalancedEntryError,
)
from vorpal.modules.comptabilite.reporting import engine

from .conftest import Env

D = Decimal


def draft(*lines: DraftLine, currency: str = "USD") -> DraftEntry:
    return DraftEntry("R1", "OD", date(2026, 11, 1), currency, tuple(lines))


def test_balanced_entry_is_valid():
    draft(DraftLine("411", debit=D("10")), DraftLine("701", credit=D("10"))).validate()


@pytest.mark.parametrize(
    "lines",
    [
        (DraftLine("411", debit=D("10")), DraftLine("701", credit=D("9"))),
        (DraftLine("411", debit=D("10")),),
        (DraftLine("411", debit=D("10"), credit=D("10")), DraftLine("701", credit=D("0"))),
        (DraftLine("411", debit=D("-1")), DraftLine("701", credit=D("-1"))),
        (DraftLine("411"), DraftLine("701", credit=D("0"))),
    ],
)
def test_invalid_entries_are_refused(lines):
    with pytest.raises(UnbalancedEntryError):
        draft(*lines).validate()


def test_float_amounts_are_refused():
    with pytest.raises(AccountingValidationError):
        draft(DraftLine("411", debit=10.0), DraftLine("701", credit=D("10"))).validate()  # type: ignore[arg-type]


def test_conversion_absorbs_rounding_residual_within_tolerance():
    d = draft(
        DraftLine("411", debit=D("100.00")),
        DraftLine("701", credit=D("33.33")),
        DraftLine("4431", credit=D("66.67")),
        currency="CDF",
    )
    lines = convert_lines(d, D("0.00035"))
    assert sum(ln.debit for ln in lines) == sum(ln.credit for ln in lines)


def test_conversion_refuses_invalid_rate():
    d = draft(DraftLine("411", debit=D("1")), DraftLine("701", credit=D("1")))
    with pytest.raises(UnbalancedEntryError):
        convert_lines(d, D("0"))


def test_account_validation():
    with pytest.raises(AccountingValidationError):
        Account("ABC", "x")
    with pytest.raises(AccountingValidationError):
        Account("0123", "x")
    assert Account("4111", "Clients").account_class == 4


def test_defaults_are_installed_and_idempotent(env: Env):
    chart = env.module.chart
    assert chart is not None
    assert chart.install_default_plan() == 0
    assert chart.install_default_journals() == 0
    assert chart.ensure_fiscal_year(2026).code == "2026"


def test_import_csv_creates_missing_accounts_only(env: Env):
    chart = env.module.chart
    assert chart is not None
    csv_text = "code,name,reconcile\n411100,Clients locaux,True\n999999,Test,False\n701100,Ventes,False\n"
    assert chart.import_csv(csv_text) == 3
    assert chart.import_csv(csv_text) == 0
    assert chart.resolve_account("411100").reconcilable is True


def test_import_csv_refuses_bad_rows_and_missing_columns(env: Env):
    chart = env.module.chart
    assert chart is not None
    with pytest.raises(AccountingValidationError):
        chart.import_csv("code,name\nABC,Mauvais\n")
    with pytest.raises(AccountingValidationError):
        chart.import_csv("numero,libelle\n1,2\n")


def test_resolve_account_by_prefix(env: Env):
    chart = env.module.chart
    assert chart is not None
    assert chart.resolve_account("4111").code == "4111"
    chart.import_csv("code,name\n411100,Clients\n")
    assert chart.resolve_account("41110").code == "411100"
    with pytest.raises(AccountingNotFoundError):
        chart.resolve_account("98765")


def test_default_plan_is_fully_covered_by_statement_definitions(env: Env):
    chart = env.module.chart
    assert chart is not None
    assert env.module.uow_factory is not None
    with env.module.uow_factory() as uow:
        codes = [a.code for a in uow.ledger.list_accounts()]
    bilan = engine.load_definition("bilan")
    resultat = engine.load_definition("compte_resultat")
    assert engine.coverage(bilan, codes, list("12345")) == ([], {})
    assert engine.coverage(resultat, codes, list("678")) == ([], {})
