"""Balance, grand livre, bilan, compte de résultat, clôture d'exercice (scénario chiffré à la main)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from vorpal.modules.comptabilite.errors import AccountingConflictError, PeriodClosedError

from .conftest import Env

D = Decimal
JAN, FEB, MAR, APR = date(2026, 1, 1), date(2026, 2, 10), date(2026, 3, 20), date(2026, 4, 30)


def scenario(env: Env) -> None:
    """Capital 10 000 ; achat 3 000 + TVA 480 ; vente 5 000 + TVA 800 ; règlements ; loyer 500.
    Résultat attendu : 5 000 - 3 000 - 500 = 1 500."""
    env.post("CAP", JAN, [("5212", "10000", "0"), ("101", "0", "10000")], journal="BQU")
    env.post("ACH1", date(2026, 1, 2), [("601", "3000", "0"), ("4452", "480", "0"), ("401", "0", "3480")], journal="ACH")
    env.post("VTE1", FEB, [("4111", "5800", "0"), ("7011", "0", "5000"), ("4431", "0", "800")], journal="VTE")
    env.post("PAY1", date(2026, 2, 15), [("401", "3480", "0"), ("5212", "0", "3480")], journal="BQU")
    env.post("ENC1", MAR, [("5212", "5800", "0"), ("4111", "0", "5800")], journal="BQU")
    env.post("LOY", APR, [("622", "500", "0"), ("5711", "0", "500")])


def test_trial_balance_is_balanced_with_expected_rows(env: Env):
    scenario(env)
    assert env.module.reports is not None
    tb = env.module.reports.trial_balance(JAN, date(2026, 12, 31))
    assert tb.is_balanced and not tb.warnings
    assert tb.total_period_debit == tb.total_period_credit == D("29060.00")
    by = {r.code: r for r in tb.rows}
    assert by["5212"].closing_debit == D("12320.00") and by["5212"].closing_credit == D("0")
    assert by["101"].closing_credit == D("10000.00")
    assert by["4111"].closing_debit == by["4111"].closing_credit == D("0")  # soldé mais mouvementé : affiché
    assert env.module.reports.trial_balance(JAN, date(2026, 12, 31), include_zero=True).rows[0].code


def test_trial_balance_opening_and_period_split(env: Env):
    scenario(env)
    assert env.module.reports is not None
    tb = env.module.reports.trial_balance(date(2026, 3, 1), date(2026, 3, 31))
    by = {r.code: r for r in tb.rows}
    assert by["5212"].opening_debit == D("6520.00")  # 10000 - 3480
    assert by["5212"].period_debit == D("5800.00") and by["5212"].closing_debit == D("12320.00")
    assert tb.is_balanced


def test_general_ledger_running_balance(env: Env):
    scenario(env)
    assert env.module.reports is not None
    gl = env.module.reports.general_ledger(date(2026, 2, 1), date(2026, 3, 31), account_codes=["5212"])
    (acc,) = gl.accounts
    assert acc.opening_balance == D("10000.00")
    assert [ln.balance for ln in acc.lines] == [D("6520.00"), D("12320.00")]
    assert acc.closing_balance == D("12320.00") and acc.total_debit == D("5800.00")
    only_class_7 = env.module.reports.general_ledger(JAN, date(2026, 12, 31), prefix="7")
    assert [a.code for a in only_class_7.accounts] == ["7011"]


def test_income_statement_matches_hand_computation(env: Env):
    scenario(env)
    assert env.module.reports is not None
    cr = env.module.reports.income_statement(JAN, date(2026, 12, 31))
    assert cr.value("TA") == D("5000.00") and cr.value("RA") == D("3000.00")
    assert cr.value("XA") == D("2000.00")
    assert cr.value("RH") == D("500.00")
    assert cr.value("XE") == cr.value("XI") == D("1500.00")


def test_balance_sheet_balances_and_includes_pending_result(env: Env):
    scenario(env)
    assert env.module.reports is not None
    bs = env.module.reports.balance_sheet(date(2026, 12, 31))
    assert not bs.warnings
    assert bs.value("BZ") == bs.value("DZ") == D("12300.00")
    assert bs.value("BT") == D("11820.00") and bs.value("BG") == D("480.00")
    assert bs.value("CA") == D("10000.00") and bs.value("CJ") == D("1500.00")
    assert bs.value("DK") == D("800.00")
    early = env.module.reports.balance_sheet(date(2026, 1, 31))
    assert early.value("BZ") == early.value("DZ")


def test_closing_transfers_result_and_keeps_statements_consistent(env: Env):
    scenario(env)
    assert env.module.closing is not None and env.module.reports is not None
    closing = env.module.closing.close_fiscal_year("2026")
    assert closing is not None
    credit_131 = [ln for ln in closing.lines if ln.account_code == "131"]
    assert credit_131[0].credit == D("1500.00")
    bs = env.module.reports.balance_sheet(date(2026, 12, 31))
    assert bs.value("CD") == D("1500.00") and bs.value("CJ") == D("0")
    assert bs.value("BZ") == bs.value("DZ") == D("12300.00")
    cr = env.module.reports.income_statement(JAN, date(2026, 12, 31))
    assert cr.value("XI") == D("1500.00")  # la clôture n'annule pas le résultat de l'exercice
    tb = env.module.reports.trial_balance(JAN, date(2026, 12, 31))
    assert tb.is_balanced
    again = env.module.closing.close_fiscal_year("2026")
    assert again is not None and again.id == closing.id
    with pytest.raises(PeriodClosedError):
        env.post("LATE", date(2026, 12, 1), [("5212", "1", "0"), ("7011", "0", "1")], journal="BQU")


def test_next_year_opens_with_carried_result(env: Env):
    scenario(env)
    assert env.module.closing is not None and env.module.reports is not None and env.module.chart is not None
    env.module.closing.close_fiscal_year("2026")
    env.module.chart.ensure_fiscal_year(2027)
    env.post("N1", date(2027, 1, 10), [("5212", "100", "0"), ("7011", "0", "100")], journal="BQU")
    tb = env.module.reports.trial_balance(date(2027, 1, 1), date(2027, 12, 31))
    by = {r.code: r for r in tb.rows}
    assert tb.is_balanced and not tb.warnings
    assert by["131"].opening_credit == D("1500.00")
    assert "7011" in by and by["7011"].opening_credit == D("0")
    assert env.module.reports.income_statement(date(2027, 1, 1), date(2027, 12, 31)).value("XI") == D("100.00")
    bs = env.module.reports.balance_sheet(date(2027, 12, 31))
    assert bs.value("BZ") == bs.value("DZ") == D("12400.00")


def test_unclosed_previous_year_is_flagged(env: Env):
    scenario(env)
    assert env.module.chart is not None and env.module.reports is not None
    env.module.chart.ensure_fiscal_year(2027)
    env.post("N1", date(2027, 1, 10), [("5212", "100", "0"), ("7011", "0", "100")], journal="BQU")
    tb = env.module.reports.trial_balance(date(2027, 1, 1), date(2027, 12, 31))
    assert tb.warnings and not tb.is_balanced
    bs = env.module.reports.balance_sheet(date(2027, 12, 31))
    assert bs.value("BZ") == bs.value("DZ")  # le bilan reste équilibré (résultat cumulé non affecté)


def test_loss_goes_to_139_and_order_of_closing_is_enforced(env: Env):
    assert env.module.closing is not None and env.module.chart is not None
    env.post("L", date(2026, 5, 1), [("622", "400", "0"), ("5212", "0", "400")], journal="BQU")
    env.module.chart.ensure_fiscal_year(2027)
    with pytest.raises(AccountingConflictError):
        env.module.closing.close_fiscal_year("2027")
    closing = env.module.closing.close_fiscal_year("2026")
    assert closing is not None
    loss = [ln for ln in closing.lines if ln.account_code == "139"]
    assert loss and loss[0].debit == D("400.00")


def test_year_without_activity_closes_without_entry(env: Env):
    assert env.module.closing is not None
    assert env.module.closing.close_fiscal_year("2026") is None


def test_report_range_is_validated(env: Env):
    assert env.module.reports is not None
    with pytest.raises(Exception, match="fin précède"):
        env.module.reports.trial_balance(date(2026, 2, 1), date(2026, 1, 1))
