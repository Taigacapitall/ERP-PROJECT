"""Journal fiscal append-only avec hash chaine, recherche et export CSV."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vorpal.modules.dgi.models import CertificationStatus


@dataclass(frozen=True)
class FiscalJournalEntry:
    """Entree immuable du journal fiscal."""

    sequence: int
    timestamp: datetime
    invoice_id: str
    serial_number: str
    status: CertificationStatus
    request_json: str
    response_json: str
    previous_hash: str
    entry_hash: str


def _compute_entry_hash(
    sequence: int,
    timestamp: datetime,
    invoice_id: str,
    serial_number: str,
    status_value: str,
    request_json: str,
    response_json: str,
    previous_hash: str,
) -> str:
    payload = (
        f"{sequence}|{timestamp.isoformat()}|{invoice_id}|{serial_number}|"
        f"{status_value}|{request_json}|{response_json}|{previous_hash}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FiscalJournal:
    """Journal fiscal append-only avec verification d'integrite par hash chaine."""

    def __init__(self) -> None:
        self._entries: list[FiscalJournalEntry] = []

    def append(
        self,
        invoice_id: str,
        serial_number: str,
        status: CertificationStatus,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        timestamp: datetime,
    ) -> FiscalJournalEntry:
        seq = len(self._entries) + 1
        prev_hash = self._entries[-1].entry_hash if self._entries else "GENESIS"
        req_json = json.dumps(request_payload, sort_keys=True, ensure_ascii=True, default=str)
        resp_json = json.dumps(response_payload, sort_keys=True, ensure_ascii=True, default=str)
        entry_hash = _compute_entry_hash(
            sequence=seq,
            timestamp=timestamp,
            invoice_id=invoice_id,
            serial_number=serial_number,
            status_value=status.value,
            request_json=req_json,
            response_json=resp_json,
            previous_hash=prev_hash,
        )
        entry = FiscalJournalEntry(
            sequence=seq,
            timestamp=timestamp,
            invoice_id=invoice_id,
            serial_number=serial_number,
            status=status,
            request_json=req_json,
            response_json=resp_json,
            previous_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[FiscalJournalEntry]:
        return list(self._entries)

    def search(
        self,
        invoice_id: str | None = None,
        serial_number: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[FiscalJournalEntry]:
        results: list[FiscalJournalEntry] = []
        for entry in self._entries:
            if invoice_id is not None and entry.invoice_id != invoice_id:
                continue
            if serial_number is not None and entry.serial_number != serial_number:
                continue
            if start_at is not None and entry.timestamp < start_at:
                continue
            if end_at is not None and entry.timestamp > end_at:
                continue
            results.append(entry)
        return results

    def verify_integrity(self) -> bool:
        expected_prev = "GENESIS"
        for idx, entry in enumerate(self._entries, start=1):
            if entry.sequence != idx or entry.previous_hash != expected_prev:
                return False
            recomputed = _compute_entry_hash(
                sequence=entry.sequence,
                timestamp=entry.timestamp,
                invoice_id=entry.invoice_id,
                serial_number=entry.serial_number,
                status_value=entry.status.value,
                request_json=entry.request_json,
                response_json=entry.response_json,
                previous_hash=entry.previous_hash,
            )
            if recomputed != entry.entry_hash:
                return False
            expected_prev = entry.entry_hash
        return True

    def export_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "sequence",
                "timestamp",
                "invoice_id",
                "serial_number",
                "status",
                "request_json",
                "response_json",
                "previous_hash",
                "entry_hash",
            ]
        )
        for entry in self._entries:
            writer.writerow(
                [
                    entry.sequence,
                    entry.timestamp.isoformat(),
                    entry.invoice_id,
                    entry.serial_number,
                    entry.status.value,
                    entry.request_json,
                    entry.response_json,
                    entry.previous_hash,
                    entry.entry_hash,
                ]
            )
        return buffer.getvalue()
