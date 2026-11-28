"""File persistante des écritures à poster (reprise après indisponibilité d'Odoo)."""
from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .dto import JournalEntryDTO

PENDING, DONE, REJECTED = "pending", "done", "rejected"


@dataclass(frozen=True)
class QueueItem:
    source_ref: str
    entry: JournalEntryDTO
    attempts: int
    status: str
    last_error: str | None


class PendingQueue(ABC):
    @abstractmethod
    def enqueue(self, entry: JournalEntryDTO) -> bool:
        """True si nouvellement ajouté ; False si la source_ref est déjà connue (dédoublonnage)."""

    @abstractmethod
    def get(self, source_ref: str) -> QueueItem | None: ...

    @abstractmethod
    def due(self, now: datetime, limit: int = 100) -> list[QueueItem]: ...

    @abstractmethod
    def mark_done(self, source_ref: str) -> None: ...

    @abstractmethod
    def mark_retry(self, source_ref: str, error: str, now: datetime) -> None: ...

    @abstractmethod
    def mark_rejected(self, source_ref: str, error: str) -> None: ...


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


class SqlitePendingQueue(PendingQueue):
    """Implémentation SQLite (stdlib). Table préfixée ``odoo_`` ; migration additive."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS odoo_pending_entry (
                    source_ref TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL, last_error TEXT)""")

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.astimezone(UTC).isoformat()

    def enqueue(self, entry: JournalEntryDTO) -> bool:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO odoo_pending_entry"
                "(source_ref,payload,status,attempts,next_attempt_at) VALUES (?,?,?,0,?)",
                (entry.source_ref, json.dumps(entry.to_dict()), PENDING,
                 self._iso(datetime.now(UTC))))
            return cur.rowcount == 1

    def _row(self, r: tuple[str, str, str, int, str | None]) -> QueueItem:
        return QueueItem(r[0], JournalEntryDTO.from_dict(json.loads(r[1])), r[3], r[2], r[4])

    def get(self, source_ref: str) -> QueueItem | None:
        with self._lock:
            r = self._db.execute(
                "SELECT source_ref,payload,status,attempts,last_error "
                "FROM odoo_pending_entry WHERE source_ref=?", (source_ref,)).fetchone()
        return None if r is None else self._row(r)

    def due(self, now: datetime, limit: int = 100) -> list[QueueItem]:
        with self._lock:
            rows = self._db.execute(
                "SELECT source_ref,payload,status,attempts,last_error FROM odoo_pending_entry "
                "WHERE status=? AND next_attempt_at<=? ORDER BY next_attempt_at LIMIT ?",
                (PENDING, self._iso(now), limit)).fetchall()
        return [self._row(r) for r in rows]

    def mark_done(self, source_ref: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE odoo_pending_entry SET status=?, last_error=NULL "
                             "WHERE source_ref=?", (DONE, source_ref))

    def mark_retry(self, source_ref: str, error: str, now: datetime) -> None:
        with self._lock, self._db:
            row = self._db.execute("SELECT attempts FROM odoo_pending_entry WHERE source_ref=?",
                                   (source_ref,)).fetchone()
            attempts = (row[0] if row else 0) + 1
            self._db.execute(
                "UPDATE odoo_pending_entry SET attempts=?, last_error=?, next_attempt_at=? "
                "WHERE source_ref=?",
                (attempts, error[:500], self._iso(now + backoff(attempts)), source_ref))

    def mark_rejected(self, source_ref: str, error: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE odoo_pending_entry SET status=?, last_error=? "
                             "WHERE source_ref=?", (REJECTED, error[:500], source_ref))
