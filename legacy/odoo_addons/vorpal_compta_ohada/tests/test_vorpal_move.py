"""Tests Odoo (TransactionCase). NON EXÉCUTÉS à la livraison : aucune instance Odoo disponible.

Lancer : odoo-bin -d <db_test> -i vorpal_compta_ohada --test-tags /vorpal_compta_ohada --stop-after-init
"""
from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestVorpalMove(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env["account.chart.template"].try_loading("cd", company=cls.company, install_demo=False)
        cls.journal = cls.env["account.journal"].search(
            [("code", "=", "OD"), ("company_id", "=", cls.company.id)], limit=1)
        cls.acc_rec = cls.env["account.account"].search([("code", "=like", "4111%")], limit=1)
        cls.acc_rev = cls.env["account.account"].search([("code", "=like", "7011%")], limit=1)

    def _move(self, ref, dgi=None, currency=None, post=True):
        vals = {
            "move_type": "entry", "journal_id": self.journal.id, "date": date(2026, 11, 20),
            "vorpal_source_ref": ref,
            "line_ids": [Command.create({"account_id": self.acc_rec.id, "debit": 100.0, "credit": 0.0}),
                         Command.create({"account_id": self.acc_rev.id, "debit": 0.0, "credit": 100.0})],
        }
        if dgi:
            vals.update(vorpal_dgi_code=dgi, vorpal_dgi_qr="QR")
        move = self.env["account.move"].create(vals)
        if post:
            move.action_post()
        return move

    def test_journals_and_currencies_installed(self):
        codes = set(self.env["account.journal"].search([("company_id", "=", self.company.id)]).mapped("code"))
        self.assertTrue({"VTE", "ACH", "OD", "PAIE", "IMMO", "CAU", "CAF", "BQU", "BQF"} <= codes)
        cur = self.env["res.currency"].with_context(active_test=False).search([("name", "in", ["USD", "CDF"])])
        self.assertTrue(all(cur.mapped("active")))

    def test_source_ref_unique_per_company(self):
        self._move("invoice.certified:T1")
        with self.assertRaises(Exception), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self._move("invoice.certified:T1")

    def test_certified_move_is_locked(self):
        move = self._move("invoice.certified:T2", dgi="DGI-1")
        with self.assertRaises(UserError):
            move.write({"partner_id": self.env.user.partner_id.id})
        with self.assertRaises(UserError):
            move.line_ids[0].write({"debit": 1.0})
        with self.assertRaises(UserError):
            move.button_draft()
        with self.assertRaises(UserError):
            move.button_cancel()
        with self.assertRaises(UserError):
            move.unlink()

    def test_uncertified_move_keeps_native_behaviour(self):
        move = self._move("invoice.certified:T3", post=False)
        move.unlink()

    def test_ohada_report_balances_requires_accounting_right(self):
        user = self.env["res.users"].create({"name": "Sans droit", "login": "norights_vorpal",
                                             "group_ids": [Command.set([self.env.ref("base.group_user").id])]})
        with self.assertRaises(Exception):
            self.env["vorpal.ohada.report"].with_user(user).balances("2026-01-01", "2026-12-31")
