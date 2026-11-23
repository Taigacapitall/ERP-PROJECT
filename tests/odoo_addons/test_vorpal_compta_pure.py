"""Tests de la logique pure de l'addon (sans Odoo). Les tests Odoo sont dans l'addon."""
from __future__ import annotations

import csv
import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ADDON = Path(__file__).resolve().parents[2] / "odoo_addons" / "vorpal_compta_ohada"
sys.path.insert(0, str(ADDON))

from ohada import depreciation, engine, protection  # noqa: E402

D = Decimal


class Protection(unittest.TestCase):
    def test_certified_move_blocks_fiscal_fields(self) -> None:
        self.assertEqual(protection.move_write_violations(["line_ids", "state"], True), ["line_ids"])
        self.assertEqual(protection.move_write_violations(["vorpal_dgi_code"], True), ["vorpal_dgi_code"])

    def test_technical_fields_stay_writable(self) -> None:
        self.assertEqual(protection.move_write_violations(["state", "name", "payment_state"], True), [])
        self.assertEqual(protection.line_write_violations(["matching_number", "reconciled"], True), [])

    def test_uncertified_move_is_free(self) -> None:
        self.assertEqual(protection.move_write_violations(["line_ids"], False), [])
        self.assertTrue(protection.may_reset_or_delete(False))
        self.assertFalse(protection.may_reset_or_delete(True))

    def test_lines_of_certified_move(self) -> None:
        self.assertEqual(protection.line_write_violations(["debit", "name"], True), ["debit"])


def _plan_codes() -> list[str]:
    path = os.environ.get("VORPAL_SYSCOHADA_CSV")
    if not path:
        return []
    with Path(path).open(encoding="utf-8") as fh:
        return [r["code"] for r in csv.DictReader(fh)]


class Statements(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("VORPAL_SYSCOHADA_CSV"), "plan SYSCOHADA non fourni")
    def test_every_account_of_the_real_plan_is_mapped_exactly_once(self) -> None:
        codes = _plan_codes()
        un, multi = engine.coverage(engine.load_definition("bilan"), codes, list("12345678"))
        self.assertEqual((un, multi), ([], {}))
        un, multi = engine.coverage(engine.load_definition("compte_resultat"), codes, list("678"))
        self.assertEqual((un, multi), ([], {}))

    def _balances(self) -> dict[str, Decimal]:
        # écriture 6 chiffres (plan généré) ; balance équilibrée
        return {
            "101100": D("-5000"),   # capital
            "241000": D("3000"),    # matériel
            "284100": D("-300"),    # amortissement
            "311100": D("800"),     # stock
            "411100": D("1200"),    # clients
            "401100": D("-900"),    # fournisseurs
            "443100": D("-160"),    # TVA facturée
            "445200": D("40"),      # TVA récupérable
            "521000": D("1500"),    # banque
            "571100": D("100"),     # caisse
            "601100": D("600"),     # achats
            "661100": D("200"),     # salaires
            "701100": D("-1080"),   # ventes marchandises
}
    def test_balance_is_balanced_fixture(self) -> None:
        # fixture volontairement équilibrée sauf l'écart ci-dessous (résultat de période)
        self.assertEqual(sum(self._balances().values(), D(0)), D("0"))

    def test_balance_sheet_balances(self) -> None:
        lines = {ln.code: ln.value for ln in engine.evaluate(engine.load_definition("bilan"), self._balances())}
        self.assertEqual(lines["BZ"], lines["DZ"])
        self.assertEqual(lines["AI"], D("2700"))          # 3000 - 300
        self.assertEqual(lines["CJ"], D("280"))           # 1080 - 600 - 200
        self.assertEqual(lines["CP"], D("5280"))

    def test_income_statement(self) -> None:
        lines = {ln.code: ln.value for ln in engine.evaluate(engine.load_definition("compte_resultat"), self._balances())}
        self.assertEqual(lines["XB"], D("1080"))
        self.assertEqual(lines["XA"], D("480"))
        self.assertEqual(lines["XI"], D("280"))

    def test_net_result_equals_bilan_result(self) -> None:
        b = {ln.code: ln.value for ln in engine.evaluate(engine.load_definition("bilan"), self._balances())}
        c = {ln.code: ln.value for ln in engine.evaluate(engine.load_definition("compte_resultat"), self._balances())}
        self.assertEqual(b["CJ"], c["XI"])

    def test_forward_reference_rejected(self) -> None:
        with self.assertRaises(engine.DefinitionError):
            engine.evaluate({"lines": [{"code": "A", "label": "a", "sum": ["+B"]}]}, {})


class Depreciation(unittest.TestCase):
    def test_linear_sums_to_base_and_prorata(self) -> None:
        plan = depreciation.schedule(D("12000"), D("0"), 5, date(2026, 4, 10))
        self.assertEqual(plan[0].months, 9)
        self.assertEqual(plan[0].amount, D("1800.00"))
        self.assertEqual(plan[-1].net_value, D("0"))
        self.assertEqual(sum((x.amount for x in plan), D(0)), D("12000"))

    def test_residual_value_is_kept(self) -> None:
        plan = depreciation.schedule(D("10000"), D("1000"), 3, date(2026, 1, 1))
        self.assertEqual(plan[-1].net_value, D("1000"))

    def test_declining_front_loaded_and_complete(self) -> None:
        plan = depreciation.schedule(D("10000"), D("0"), 5, date(2026, 1, 1), "declining", D("2"))
        self.assertGreater(plan[0].amount, plan[-1].amount)
        self.assertEqual(sum((x.amount for x in plan), D(0)), D("10000"))
        self.assertTrue(all(x.amount >= 0 for x in plan))

    def test_invalid_parameters(self) -> None:
        with self.assertRaises(ValueError):
            depreciation.schedule(D("100"), D("100"), 5, date(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
