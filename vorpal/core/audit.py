from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json


GENESIS_HASH = "0" * 64


def _canonical_json(data: object) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: datetime
    user: str
    action: str
    entity: str
    entity_id: str
    before: dict[str, object] | None
    after: dict[str, object] | None
    prev_hash: str
    entry_hash: str


def compute_entry_hash(
    seq: int,
    timestamp: datetime,
    user: str,
    action: str,
    entity: str,
    entity_id: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    prev_hash: str,
) -> str:
    payload = {
        "seq": seq,
        "timestamp": timestamp.isoformat(),
        "user": user,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "before": before,
        "after": after,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class AuditLog:
    """Journal d'audit append-only chaîné par SHA-256."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def record(
        self,
        user: str,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEntry:
        seq = len(self._entries) + 1
        ts = timestamp or datetime.now(timezone.utc)
        prev_hash = self._entries[-1].entry_hash if self._entries else GENESIS_HASH
        entry_hash = compute_entry_hash(
            seq=seq,
            timestamp=ts,
            user=user,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
            prev_hash=prev_hash,
        )
        entry = AuditEntry(
            seq=seq,
            timestamp=ts,
            user=user,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        return entry

    def verify_chain(self) -> bool:
        expected_prev = GENESIS_HASH
        for idx, entry in enumerate(self._entries, start=1):
            if entry.seq != idx or entry.prev_hash != expected_prev:
                return False
            recomputed = compute_entry_hash(
                seq=entry.seq,
                timestamp=entry.timestamp,
                user=entry.user,
                action=entry.action,
                entity=entry.entity,
                entity_id=entry.entity_id,
                before=entry.before,
                after=entry.after,
                prev_hash=entry.prev_hash,
            )
            if recomputed != entry.entry_hash:
                return False
            expected_prev = entry.entry_hash
        return True
