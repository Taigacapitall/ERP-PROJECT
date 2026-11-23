from __future__ import annotations

import csv
import os
import unittest
import xmlrpc.client
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from vorpal.modules.adapters.odoo import AccountMapping, OdooConfig, XmlRpcOdooClient
from vorpal.modules.adapters.odoo.client import classify_fault
from vorpal.modules.adapters.odoo.dto import JournalEntryDTO, JournalLineDTO, PreparedMove
from vorpal.modules.adapters.odoo.currency import convert_entry
from vorpal.modules.adapters.odoo.errors import (
    DuplicateSourceRefError,
    MappingError,
    OdooRejectedError,
    OdooUnavailableError,
)

ENV = {"VORPAL_ODOO_URL": "http://x:8069/", "VORPAL_ODOO_DB": "db", "VORPAL_ODOO_USER": "u",
       "VORPAL_ODOO_API_KEY": "k"}


class ScriptedProxy:
    """Proxy XML-RPC simulé : répond selon (model, method)."""

    def __init__(self, handlers: dict[tuple[str, str], Any]) -> None:
        self.handlers, self.calls = handlers, []  # type: ignore[var-annotated]

    def authenticate(self, db: str, user: str, key: str, ctx: dict[str, Any]) -> int:
        return 7

    def execute_kw(self, db: str, uid: int, key: str, model: str, method: str,
                   args: list[Any], kw: dict[str, Any]) -> Any:
        self.calls.append((model, method, args, kw))
        h = self.handlers[(model, method)]
        if isinstance(h, Exception):
            raise h
        return h(args, kw) if callable(h) else h


class XmlRpcTests(unittest.TestCase):
    def _client(self, handlers: dict[tuple[str, str], Any]) -> tuple[XmlRpcOdooClient, ScriptedProxy]:
        proxy = ScriptedProxy(handlers)
        return XmlRpcOdooClient(OdooConfig.from_env(ENV), lambda path: proxy), proxy

    def test_config_requires_env_and_has_no_defaults_secret(self) -> None:
        with self.assertRaises(MappingError):
            OdooConfig.from_env({})
        self.assertEqual(OdooConfig.from_env(ENV).url, "http://x:8069")

    def test_account_resolution_falls_back_to_prefix(self) -> None:
        def search(args: list[Any], kw: dict[str, Any]) -> list[dict[str, Any]]:
            dom = args[0][0]
            return [{"id": 42}] if dom[1] == "=like" else []
        c, _ = self._client({("account.account", "search_read"): search})
        self.assertEqual(c.get_account_id("4111"), 42)
        self.assertEqual(c.get_account_id("4111"), 42)  # cache

    def test_unknown_account(self) -> None:
        c, _ = self._client({("account.account", "search_read"): []})
        with self.assertRaises(MappingError):
            c.get_account_id("9999")

    def test_rate_inversion(self) -> None:
        c, _ = self._client({
            ("res.currency", "search_read"): [{"id": 3}],
            ("res.currency.rate", "search_read"): [{"company_rate": 2857.142857, "rate": 0}]})
        rate = c.get_rate("CDF", date(2026, 11, 20))
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(float(rate), 0.00035, places=7)  # type: ignore[arg-type]

    def test_fault_classification(self) -> None:
        dup = xmlrpc.client.Fault(1, 'duplicate key value violates unique constraint "account_move_vorpal_source_ref_uniq" vorpal_source_ref')
        self.assertIsInstance(classify_fault(dup), DuplicateSourceRefError)
        self.assertIsInstance(classify_fault(xmlrpc.client.Fault(1, "could not serialize access")),
                              OdooUnavailableError)
        self.assertIsInstance(classify_fault(xmlrpc.client.Fault(1, "UserError: compte invalide")),
                              OdooRejectedError)

    def test_network_error_is_transient(self) -> None:
        c, _ = self._client({("res.company", "search_read"): ConnectionRefusedError("down")})
        with self.assertRaises(OdooUnavailableError):
            c.get_company_currency()

    def test_create_move_payload(self) -> None:
        captured: dict[str, Any] = {}

        def create(args: list[Any], kw: dict[str, Any]) -> int:
            captured.update(args[0])
            return 99

        def search(args: list[Any], kw: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"id": 5, "default_account_id": [8, "x"]}]

        c, _ = self._client({
            ("account.account", "search_read"): search, ("account.journal", "search_read"): search,
            ("account.journal", "read"): search, ("res.currency", "search_read"): search,
            ("res.partner", "search_read"): search, ("account.move", "create"): create})
        entry = JournalEntryDTO("invoice.certified:F1", "VTE", date(2026, 11, 20), "CDF", (
            JournalLineDTO("4111", debit=Decimal("1000"), partner_ref="C1"),
            JournalLineDTO("7011", credit=Decimal("1000"))), dgi_code="D", dgi_qr="Q")
        pm = PreparedMove(entry, "USD", Decimal("0.00035"), convert_entry(entry, Decimal("0.00035")))
        self.assertEqual(c.create_move(pm), 99)
        self.assertEqual(captured["vorpal_source_ref"], "invoice.certified:F1")
        self.assertEqual(captured["vorpal_dgi_code"], "D")
        first = captured["line_ids"][0][2]
        self.assertEqual((first["debit"], first["amount_currency"]), (0.35, 1000.0))


class MappingTests(unittest.TestCase):
    def test_default_mapping_loads(self) -> None:
        m = AccountMapping.load()
        self.assertEqual(m.status, "A_VALIDER")

    def test_incomplete_mapping_refused(self) -> None:
        with self.assertRaises(MappingError):
            AccountMapping({"events": {"invoice.certified": {}}})

    @unittest.skipUnless(os.environ.get("VORPAL_SYSCOHADA_CSV"), "CSV du plan l10n_syscohada non fourni")
    def test_mapping_codes_exist_in_syscohada_plan(self) -> None:
        rows = csv.DictReader(Path(os.environ["VORPAL_SYSCOHADA_CSV"]).open(encoding="utf-8"))
        codes = {r["code"] for r in rows}
        for code in AccountMapping.load().account_codes():
            self.assertTrue(any(c == code or c.startswith(code) for c in codes),
                            f"compte {code} absent du plan")

    def test_no_secret_in_sources(self) -> None:
        root = Path(__file__).resolve().parents[3] / "vorpal" / "modules" / "adapters" / "odoo"
        for p in root.rglob("*.py"):
            text = p.read_text(encoding="utf-8").lower()
            self.assertNotIn("api_key=\"", text.replace(" ", ""))
