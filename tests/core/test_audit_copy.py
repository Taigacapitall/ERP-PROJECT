from __future__ import annotations

from datetime import UTC, datetime

from vorpal.core.audit import AuditLog

TS = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)


def test_record_copies_before_and_after() -> None:
    audit = AuditLog()
    lines: list[dict[str, str]] = [{"qty": "1"}]
    before: dict[str, object] = {"status": "DRAFT"}
    after: dict[str, object] = {"status": "CERTIFIED", "lines": lines}
    entry = audit.record(
        "admin", "CERTIFY", "invoice", "INV-1", before=before, after=after, timestamp=TS
    )

    before["status"] = "HACKED"  # mutation de l'appelant, apres l'enregistrement
    lines[0]["qty"] = "999"  # mutation d'une structure imbriquee

    assert audit.verify_chain() is True
    assert entry.before == {"status": "DRAFT"}
    assert entry.after == {"status": "CERTIFIED", "lines": [{"qty": "1"}]}


def test_same_dict_reused_between_calls_does_not_alias_entries() -> None:
    audit = AuditLog()
    payload: dict[str, object] = {"n": "1"}
    audit.record("admin", "UPDATE", "invoice", "INV-1", after=payload, timestamp=TS)
    payload["n"] = "2"
    audit.record("admin", "UPDATE", "invoice", "INV-1", after=payload, timestamp=TS)

    assert [e.after for e in audit.entries] == [{"n": "1"}, {"n": "2"}]
    assert audit.verify_chain() is True
