from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from vorpal.modules.adapters.odoo.dto import JournalEntryDTO, JournalLineDTO
from vorpal.modules.adapters.odoo.errors import (
    DuplicateSourceRefError,
    IncompleteFiscalStampError,
    InvalidPayloadError,
    UnbalancedEntryError,
)
from vorpal.modules.adapters.odoo.poster import OdooAccountingPoster

from .helpers import Env, Ev, certified, doc, make_client, ready


def totals(client, move_id):  # type: ignore[no-untyped-def]
    m = client.moves[move_id].prepared
    return (sum((pl.debit_company for pl in m.lines), Decimal(0)),
            sum((pl.credit_company for pl in m.lines), Decimal(0)))


class InvoiceFlow(unittest.TestCase):
    def test_invoice_posts_only_after_certification(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc()))
        self.assertEqual(len(e.client.moves), 0)  # prêt à certifier != certifié
        e.handlers.handle(certified())
        (mv,) = e.client.moves.values()
        self.assertEqual(mv.state, "posted")
        self.assertEqual(totals(e.client, mv.move_id), (Decimal("174.00"), Decimal("174.00")))
        self.assertEqual({pl.line.account_code for pl in mv.prepared.lines},
                         {"4111", "7011", "4431"})
        self.assertEqual((mv.prepared.entry.dgi_code, mv.prepared.entry.dgi_qr),
                         ("DGI-ABC", "QR-DATA"))
        self.assertEqual(mv.prepared.lines[0].line.partner_ref, "A1234567")
        self.assertEqual(e.published[0][0], "journal.entry.posted")

    def test_certified_before_ready_is_correlated(self) -> None:
        """Bus synchrone : DGI publie `invoice.certified` pendant le traitement de `ready`."""
        e = Env()
        e.handlers.handle(certified())
        self.assertEqual(len(e.client.moves), 0)
        e.handlers.handle(ready(doc()))
        self.assertEqual(len(e.client.moves), 1)

    def test_replay_creates_no_duplicate(self) -> None:
        e = Env()
        for _ in range(3):
            e.handlers.handle(ready(doc()))
            e.handlers.handle(certified())
        self.assertEqual(len(e.client.moves), 1)
        self.assertEqual(len(e.published), 1)

    def test_replay_with_different_content_is_refused(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc()))
        e.handlers.handle(certified())
        other = doc(total_ht="1.00", total_vat="0", total_ttc="1.00")
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(ready(other))
        self.assertEqual(len(e.client.moves), 1)

    def test_incomplete_stamp_is_never_posted(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc()))
        for bad in ({"qr_code": ""}, {"authentication_code": " "}, {"certified_at": "hier"},
                    {"fiscal_device_id": None}):
            with self.assertRaises(IncompleteFiscalStampError):
                e.handlers.handle(certified(**bad))
        self.assertEqual(len(e.client.moves), 0)
        self.assertIsNone(e.queue.get("invoice.certified:F-2026-0001"))

    def test_rejected_invoice_is_forgotten_and_never_posted(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc()))
        e.handlers.handle(Ev("invoice.rejected", {"invoice_id": "F-2026-0001",
                                                  "serial_number": "F-2026-0001", "reason": "x"}))
        e.handlers.handle(certified())  # cachet seul, sans document : rien à poster
        self.assertEqual(len(e.client.moves), 0)

    def test_float_amounts_refused(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc(total_ht=150.0)))
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(certified())

    def test_total_mismatch_refused(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc(total_ttc="999.00")))
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(certified())
        self.assertEqual(len(e.client.moves), 0)

    def test_event_type_must_match_document_type(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc()))
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(certified(etype="credit_note.certified"))

    def test_credit_note_reverses_invoice(self) -> None:
        e = Env()
        cn = doc("A-1", invoice_type="avoir", original_invoice_ref="F-2026-0001")
        e.handlers.handle(ready(cn))
        e.handlers.handle(certified("A-1", "credit_note.certified"))
        (mv,) = e.client.moves.values()
        rec = next(pl for pl in mv.prepared.lines if pl.line.account_code == "4111")
        self.assertEqual(rec.credit_company, Decimal("174.00"))
        self.assertEqual(mv.prepared.entry.narration, "Avoir sur facture F-2026-0001")
        d, c = totals(e.client, mv.move_id)
        self.assertEqual(d, c)

    def test_credit_note_requires_original_ref(self) -> None:
        e = Env()
        e.handlers.register_document(doc("A-2", invoice_type="avoir"))
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(certified("A-2", "credit_note.certified"))

    def test_customer_without_nif_uses_name_key(self) -> None:
        e = Env()
        e.handlers.handle(ready(doc(customer={"name": "Particulier"})))
        e.handlers.handle(certified())
        (mv,) = e.client.moves.values()
        self.assertEqual(mv.prepared.lines[0].line.partner_ref, "name:Particulier")


class Currencies(unittest.TestCase):
    def test_cdf_invoice_in_usd_company(self) -> None:
        e = Env()  # taux fake : 1 CDF = 0.00035 USD (~2857 CDF/USD)
        e.handlers.handle(ready(doc(currency="CDF", total_ht="1000000", total_vat="160000",
                                    total_ttc="1160000")))
        e.handlers.handle(certified())
        (mv,) = e.client.moves.values()
        self.assertEqual(mv.prepared.rate, Decimal("0.00035"))
        rec = next(pl for pl in mv.prepared.lines if pl.line.account_code == "4111")
        self.assertEqual(rec.debit_company, Decimal("406.00"))  # 1 160 000 CDF * 0.00035
        d, c = totals(e.client, mv.move_id)
        self.assertEqual(d, c)
        self.assertEqual(rec.line.debit, Decimal("1160000"))  # devise de transaction conservée

    def test_rounding_residual_is_absorbed(self) -> None:
        client = make_client(rates={"CDF": Decimal("0.000357")})
        entry = JournalEntryDTO(
            "t:1", "VTE", __import__("datetime").date(2026, 11, 20), "CDF",
            (JournalLineDTO("4111", debit=Decimal("100.5")),
             JournalLineDTO("7011", credit=Decimal("33.5")),
             JournalLineDTO("7061", credit=Decimal("33.5")),
             JournalLineDTO("4431", credit=Decimal("33.5"))))
        r = OdooAccountingPoster(client).post(entry)
        d, c = totals(client, r.move_id)
        self.assertEqual(d, c)

    def test_missing_rate_is_rejected_not_guessed(self) -> None:
        e = Env(make_client(rates={}))
        e.handlers.handle(ready(doc(currency="CDF")))
        e.handlers.handle(certified())
        self.assertEqual(e.queue.get("invoice.certified:F-2026-0001").status, "rejected")  # type: ignore[union-attr]
        self.assertEqual(len(e.client.moves), 0)

    def test_unbalanced_dto_refused(self) -> None:
        entry = JournalEntryDTO("t:2", "VTE", __import__("datetime").date(2026, 1, 1), "USD",
                                (JournalLineDTO("4111", debit=Decimal("10")),
                                 JournalLineDTO("7011", credit=Decimal("9.99"))))
        with self.assertRaises(UnbalancedEntryError):
            OdooAccountingPoster(make_client()).post(entry)


class Resilience(unittest.TestCase):
    def test_odoo_down_then_recovery_no_loss_no_duplicate(self) -> None:
        e = Env(make_client(available=False))
        e.handlers.handle(ready(doc()))
        e.handlers.handle(certified())  # ne lève pas
        item = e.queue.get("invoice.certified:F-2026-0001")
        self.assertEqual((item.status, item.attempts), ("pending", 1))  # type: ignore[union-attr]
        self.assertEqual(len(e.client.moves), 0)
        e.client.available = True
        self.assertEqual(e.handlers.process_pending(), 0)  # backoff pas écoulé
        e.now += timedelta(minutes=5)
        self.assertEqual(e.handlers.process_pending(), 1)
        self.assertEqual(len(e.client.moves), 1)
        e.now += timedelta(hours=2)
        self.assertEqual(e.handlers.process_pending(), 0)
        self.assertEqual(len(e.client.moves), 1)

    def test_failed_validation_resumes_existing_draft(self) -> None:
        e = Env()
        e.client.fail_post_once = True
        e.handlers.handle(ready(doc()))
        e.handlers.handle(certified())
        self.assertEqual(next(iter(e.client.moves.values())).state, "draft")
        e.now += timedelta(minutes=5)
        e.handlers.process_pending()
        self.assertEqual(len(e.client.moves), 1)
        self.assertEqual(next(iter(e.client.moves.values())).state, "posted")

    def test_queue_survives_restart(self) -> None:
        import os
        import tempfile

        from vorpal.modules.adapters.odoo import SqlitePendingQueue
        path = os.path.join(tempfile.mkdtemp(), "q.db")
        e = Env(make_client(available=False))
        e.queue = SqlitePendingQueue(path)
        e.handlers._queue = e.queue  # noqa: SLF001
        e.handlers.handle(ready(doc()))
        e.handlers.handle(certified())
        reopened = SqlitePendingQueue(path)
        self.assertEqual(len(reopened.due(e.now + timedelta(hours=1))), 1)

    def test_concurrent_duplicate_is_resumed(self) -> None:
        client = make_client()
        real_find = client.find_move_by_source_ref
        state = {"first": True}

        def racy_find(ref: str):  # type: ignore[no-untyped-def]
            if state["first"]:
                state["first"] = False
                return None  # l'autre envoi n'est pas encore visible
            return real_find(ref)

        entry = JournalEntryDTO("t:3", "VTE", __import__("datetime").date(2026, 1, 1), "USD",
                                (JournalLineDTO("4111", debit=Decimal("10")),
                                 JournalLineDTO("7011", credit=Decimal("10"))))
        # un autre processus a déjà créé l'écriture
        OdooAccountingPoster(client).post(entry)
        client.find_move_by_source_ref = racy_find  # type: ignore[method-assign]
        r = OdooAccountingPoster(client).post(entry)
        self.assertFalse(r.created)
        self.assertEqual(len(client.moves), 1)
        with self.assertRaises(DuplicateSourceRefError):
            client.create_move(client.moves[1].prepared)

    def test_unknown_account_is_permanent_rejection(self) -> None:
        e = Env(make_client())
        del e.client.accounts["701100"]
        e.handlers.handle(ready(doc()))
        e.handlers.handle(certified())
        self.assertEqual(e.queue.get("invoice.certified:F-2026-0001").status, "rejected")  # type: ignore[union-attr]
        self.assertEqual(len(e.client.moves), 0)


class PaymentAndPayroll(unittest.TestCase):
    PAY = {"payment_id": "P1", "customer_id": "C001", "amount": "571428.57", "currency": "CDF",
           "payment_date": "2026-11-20", "method": "CASH"}

    def test_payment_cash_cdf_real_ventes_payload(self) -> None:
        e = Env()
        e.handlers.handle(Ev("payment.received", dict(self.PAY)))
        (mv,) = e.client.moves.values()
        self.assertEqual(mv.prepared.entry.journal_code, "CAF")
        self.assertEqual(mv.prepared.lines[0].line.account_code, "journal:CAF")
        self.assertEqual(mv.prepared.lines[1].line.partner_ref, "C001")
        d, c = totals(e.client, mv.move_id)
        self.assertEqual(d, c)

    def test_payment_replay_is_idempotent(self) -> None:
        e = Env()
        for _ in range(2):
            e.handlers.handle(Ev("payment.received", dict(self.PAY)))
        self.assertEqual(len(e.client.moves), 1)

    def test_payment_partner_resolver_aligns_with_invoice_partner(self) -> None:
        e = Env()
        e.handlers._resolve_partner = lambda cid: ("A1234567", "Client SARL")  # noqa: SLF001
        e.handlers.handle(Ev("payment.received", dict(self.PAY)))
        (mv,) = e.client.moves.values()
        self.assertEqual(mv.prepared.lines[1].line.partner_ref, "A1234567")

    def test_payment_unknown_method_refused(self) -> None:
        e = Env()
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(Ev("payment.received", dict(self.PAY, method="BITCOIN")))

    def test_payment_float_refused(self) -> None:
        with self.assertRaises(InvalidPayloadError):
            Env().handlers.handle(Ev("payment.received", dict(self.PAY, amount=1.5)))

    PAYROLL = {"period": "2026-10", "employee_id": "E1", "gross_pay": "1000.00",
               "deductions": "200.00", "net_pay": "800.00", "employer_charges": "100.00",
               "breakdown": {"employee_social": "50.00", "employee_income_tax": "150.00"}}

    def test_payroll_balanced(self) -> None:
        e = Env()
        e.handlers.handle(Ev("payroll.validated", dict(self.PAYROLL)))
        (mv,) = e.client.moves.values()
        self.assertEqual(mv.prepared.entry.entry_date.isoformat(), "2026-10-31")
        self.assertEqual(totals(e.client, mv.move_id), (Decimal("1100.00"), Decimal("1100.00")))

    def test_payroll_inconsistent_refused(self) -> None:
        e = Env()
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(Ev("payroll.validated", dict(self.PAYROLL, net_pay="900.00")))
        with self.assertRaises(InvalidPayloadError):
            e.handlers.handle(Ev("payroll.validated", dict(self.PAYROLL, breakdown={})))


class CoreBridge(unittest.TestCase):
    def test_core_poster_returns_move_id_as_str(self) -> None:
        from types import SimpleNamespace as NS

        from vorpal.modules.adapters.odoo.core_poster import CoreAccountingPoster
        cur = NS(value="USD")
        core = NS(entry_date=__import__("datetime").date(2026, 11, 20), journal="VTE",
                  label="Test", source_ref="core:1",
                  lines=[NS(account="4111", debit=Decimal("10"), credit=Decimal(0), currency=cur),
                         NS(account="7011", debit=Decimal(0), credit=Decimal("10"), currency=cur)])
        client = make_client()
        poster = CoreAccountingPoster(OdooAccountingPoster(client))
        first = poster.post(core)
        self.assertEqual(first, poster.post(core))  # idempotent
        self.assertEqual(len(client.moves), 1)

    def test_core_poster_maps_unavailable_to_external_error(self) -> None:
        from types import SimpleNamespace as NS

        from vorpal.modules.adapters.odoo.core_poster import CoreAccountingPoster

        class Ext(Exception):
            pass
        cur = NS(value="USD")
        core = NS(entry_date=__import__("datetime").date(2026, 11, 20), journal="VTE",
                  label="T", source_ref="core:2",
                  lines=[NS(account="4111", debit=Decimal("1"), credit=Decimal(0), currency=cur),
                         NS(account="7011", debit=Decimal(0), credit=Decimal("1"), currency=cur)])
        poster = CoreAccountingPoster(OdooAccountingPoster(make_client(available=False)), Ext)
        with self.assertRaises(Ext):
            poster.post(core)

    def test_mixed_currency_refused(self) -> None:
        from types import SimpleNamespace as NS

        from vorpal.modules.adapters.odoo.core_poster import to_adapter_entry
        from vorpal.modules.adapters.odoo.errors import MappingError
        core = NS(entry_date=None, journal="VTE", label="T", source_ref="x", lines=[
            NS(account="1", debit=Decimal(1), credit=Decimal(0), currency="USD"),
            NS(account="2", debit=Decimal(0), credit=Decimal(1), currency="CDF")])
        with self.assertRaises(MappingError):
            to_adapter_entry(core)


if __name__ == "__main__":
    unittest.main()
