"""Scenario de reference sur persistance memoire (le meme est rejoue sur SQL dans test_stock_sql)."""

from __future__ import annotations

from tests.stock.helpers import SCENARIO_EXPECTED, Env, run_scenario


def test_reference_scenario_matches_hand_computed_figures(env: Env) -> None:
    assert run_scenario(env) == SCENARIO_EXPECTED
    assert env.audit.verify_chain()
