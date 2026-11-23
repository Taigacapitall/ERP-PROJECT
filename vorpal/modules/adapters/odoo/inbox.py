"""Boîte de corrélation SQLite : documents commerciaux (``invoice.ready_for_certification``)
et cachets fiscaux (``invoice.certified`` / ``credit_note.certified``).

Les événements réels du Core ne portent jamais ensemble montants et cachet : la facture
arrive avant (ou, avec un bus synchrone, parfois après) le résultat DGI. On stocke les deux
et on n'émet l'écriture que lorsque le couple est complet.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FiscalRecord:
    number: str
    document: dict[str, Any] | None
    stamp: dict[str, Any] | None
    stamp_event: str | None
    completed: bool


class SqliteFiscalInbox:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS odoo_fiscal_inbox (
                    number TEXT PRIMARY KEY, document TEXT, stamp TEXT,
                    stamp_event TEXT, completed INTEGER NOT NULL DEFAULT 0)""")

    def put_document(self, number: str, document: dict[str, Any]) -> bool:
        """False si un document différent est déjà complété (rejeu modifié refusé)."""
        raw = json.dumps(document, sort_keys=True)
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT document, completed FROM odoo_fiscal_inbox WHERE number=?",
                (number,)).fetchone()
            if row is None:
                self._db.execute("INSERT INTO odoo_fiscal_inbox(number,document) VALUES (?,?)",
                                 (number, raw))
                return True
            if row[1]:
                return row[0] == raw
            self._db.execute("UPDATE odoo_fiscal_inbox SET document=? WHERE number=?",
                             (raw, number))
            return True

    def put_stamp(self, number: str, event: str, stamp: dict[str, Any]) -> None:
        raw = json.dumps(stamp, sort_keys=True)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO odoo_fiscal_inbox(number,stamp,stamp_event) VALUES (?,?,?) "
                "ON CONFLICT(number) DO UPDATE SET stamp=excluded.stamp, "
                "stamp_event=excluded.stamp_event WHERE completed=0", (number, raw, event))

    def get(self, number: str) -> FiscalRecord | None:
        with self._lock:
            r = self._db.execute(
                "SELECT number,document,stamp,stamp_event,completed "
                "FROM odoo_fiscal_inbox WHERE number=?", (number,)).fetchone()
        if r is None:
            return None
        return FiscalRecord(r[0], json.loads(r[1]) if r[1] else None,
                            json.loads(r[2]) if r[2] else None, r[3], bool(r[4]))

    def mark_completed(self, number: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE odoo_fiscal_inbox SET completed=1 WHERE number=?", (number,))

    def discard_incomplete(self, number: str) -> None:
        """Rejet DGI : on oublie un document jamais comptabilisé (une resoumission le recréera)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM odoo_fiscal_inbox WHERE number=? AND completed=0",
                             (number,))
