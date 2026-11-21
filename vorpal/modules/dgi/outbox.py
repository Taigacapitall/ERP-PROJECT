"""File d'attente persistante (Outbox) pour la certification et la reprise automatique."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vorpal.modules.dgi.models import CertificationStatus, InvoiceDTO


@dataclass
class OutboxEntry:
    """Entree persistee de la file d'attente de certification fiscale."""

    serial_number: str
    invoice: InvoiceDTO
    status: CertificationStatus
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    updated_at: datetime | None = None


class OutboxRepository:
    """Depot en memoire de la file d'attente (idempotent par numero de facture / serial_number)."""

    def __init__(self) -> None:
        self._entries: dict[str, OutboxEntry] = {}

    def enqueue(self, invoice: InvoiceDTO, now: datetime) -> OutboxEntry:
        existing = self._entries.get(invoice.serial_number)
        if existing is not None:
            return existing
        entry = OutboxEntry(
            serial_number=invoice.serial_number,
            invoice=invoice,
            status=CertificationStatus.PENDING,
            attempts=0,
            next_attempt_at=now,
            updated_at=now,
        )
        self._entries[invoice.serial_number] = entry
        return entry

    def get(self, serial_number: str) -> OutboxEntry | None:
        return self._entries.get(serial_number)

    def save(self, entry: OutboxEntry) -> None:
        self._entries[entry.serial_number] = entry

    def list_due(self, now: datetime) -> list[OutboxEntry]:
        """Retourne les factures eligibles a la certification ou a la reprise a l'instant `now`."""
        due: list[OutboxEntry] = []
        for entry in self._entries.values():
            if entry.status == CertificationStatus.CERTIFIED:
                continue
            if entry.status in (CertificationStatus.PENDING, CertificationStatus.RETRY):
                if entry.next_attempt_at is None or entry.next_attempt_at <= now:
                    due.append(entry)
        return due

    def list_all(self) -> list[OutboxEntry]:
        return list(self._entries.values())
