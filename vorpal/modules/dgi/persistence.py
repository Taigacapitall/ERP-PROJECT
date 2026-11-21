"""Persistance SQL de l'outbox et du journal fiscal DGI (tables dgi_*, migration additive)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from vorpal.core.db import Base, MigrationStep
from vorpal.core.money import Currency, Money
from vorpal.modules.dgi.journal import FiscalJournal, FiscalJournalEntry, _compute_entry_hash
from vorpal.modules.dgi.models import (
    CertificationStatus,
    FiscalCertificationData,
    InvoiceDTO,
    InvoiceLineDTO,
    PartyTaxInfo,
)
from vorpal.modules.dgi.outbox import OutboxEntry, OutboxRepository


class DgiOutboxModel(Base):
    """File d'attente de certification (une ligne par numero de serie)."""

    __tablename__ = "dgi_outbox"

    serial_number: Mapped[str] = mapped_column(String(128), primary_key=True)
    invoice_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Dates en texte ISO 8601 : SQLite ne conserve pas le fuseau des DateTime.
    next_attempt_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class DgiJournalModel(Base):
    """Journal fiscal append-only a hash chaine (la cle primaire `sequence` interdit les doublons)."""

    __tablename__ = "dgi_journal"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    timestamp: Mapped[str] = mapped_column(String(40), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)


def create_dgi_schema_v1(engine: Engine) -> None:
    """Cree uniquement les tables dgi_* (additif, idempotent)."""
    tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("dgi_")]
    Base.metadata.create_all(bind=engine, tables=tables, checkfirst=True)


DGI_MIGRATIONS: dict[int, MigrationStep] = {1: create_dgi_schema_v1}


# ---------- serialisation JSON des factures DGI ----------


def _money_to(value: Money) -> dict[str, str]:
    return {"amount": str(value.amount), "currency": value.currency.value}


def _money_from(data: dict[str, str]) -> Money:
    return Money(Decimal(data["amount"]), Currency(data["currency"]))


def _party_to(party: PartyTaxInfo) -> dict[str, str | None]:
    return {"name": party.name, "tax_id": party.tax_id, "rccm": party.rccm, "address": party.address}


def _party_from(data: dict[str, Any]) -> PartyTaxInfo:
    return PartyTaxInfo(
        name=data["name"], tax_id=data["tax_id"], rccm=data["rccm"], address=data["address"]
    )


def _line_to(line: InvoiceLineDTO) -> dict[str, Any]:
    return {
        "line_id": line.line_id,
        "designation": line.designation,
        "quantity": str(line.quantity),
        "unit_price_ht": _money_to(line.unit_price_ht),
        "line_total_ht": _money_to(line.line_total_ht),
        "vat_rate": str(line.vat_rate),
        "vat_amount": _money_to(line.vat_amount),
        "line_total_ttc": _money_to(line.line_total_ttc),
        "tax_group_code": line.tax_group_code,
        "item_code": line.item_code,
        "out_of_scope": line.out_of_scope,
        "other_taxes_amount": (
            _money_to(line.other_taxes_amount) if line.other_taxes_amount is not None else None
        ),
    }


def _line_from(data: dict[str, Any]) -> InvoiceLineDTO:
    other = data["other_taxes_amount"]
    return InvoiceLineDTO(
        line_id=data["line_id"],
        designation=data["designation"],
        quantity=Decimal(data["quantity"]),
        unit_price_ht=_money_from(data["unit_price_ht"]),
        line_total_ht=_money_from(data["line_total_ht"]),
        vat_rate=Decimal(data["vat_rate"]),
        vat_amount=_money_from(data["vat_amount"]),
        line_total_ttc=_money_from(data["line_total_ttc"]),
        tax_group_code=data["tax_group_code"],
        item_code=data["item_code"],
        out_of_scope=bool(data["out_of_scope"]),
        other_taxes_amount=_money_from(other) if other is not None else None,
    )


def invoice_to_json(invoice: InvoiceDTO) -> str:
    cert = invoice.certification
    payload: dict[str, Any] = {
        "invoice_id": invoice.invoice_id,
        "serial_number": invoice.serial_number,
        "issue_date": invoice.issue_date.isoformat(),
        "currency": invoice.currency.value,
        "seller": _party_to(invoice.seller),
        "customer": _party_to(invoice.customer),
        "lines": [_line_to(line) for line in invoice.lines],
        "total_ht": _money_to(invoice.total_ht),
        "total_vat": _money_to(invoice.total_vat),
        "non_taxable_amount": _money_to(invoice.non_taxable_amount),
        "total_ttc": _money_to(invoice.total_ttc),
        "other_taxes_amount": _money_to(invoice.other_taxes_amount),
        "status": invoice.status.value,
        "certification": (
            {
                "fiscal_device_id": cert.fiscal_device_id,
                "authentication_code": cert.authentication_code,
                "qr_code": cert.qr_code,
                "certified_at": cert.certified_at,
            }
            if cert is not None
            else None
        ),
        "original_invoice_id": invoice.original_invoice_id,
        "is_credit_note": invoice.is_credit_note,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=True)


def invoice_from_json(text: str) -> InvoiceDTO:
    data: dict[str, Any] = json.loads(text)
    cert = data["certification"]
    return InvoiceDTO(
        invoice_id=data["invoice_id"],
        serial_number=data["serial_number"],
        issue_date=date.fromisoformat(data["issue_date"]),
        currency=Currency(data["currency"]),
        seller=_party_from(data["seller"]),
        customer=_party_from(data["customer"]),
        lines=[_line_from(item) for item in data["lines"]],
        total_ht=_money_from(data["total_ht"]),
        total_vat=_money_from(data["total_vat"]),
        non_taxable_amount=_money_from(data["non_taxable_amount"]),
        total_ttc=_money_from(data["total_ttc"]),
        other_taxes_amount=_money_from(data["other_taxes_amount"]),
        status=CertificationStatus(data["status"]),
        certification=(
            FiscalCertificationData(
                fiscal_device_id=cert["fiscal_device_id"],
                authentication_code=cert["authentication_code"],
                qr_code=cert["qr_code"],
                certified_at=cert["certified_at"],
            )
            if cert is not None
            else None
        ),
        original_invoice_id=data["original_invoice_id"],
        is_credit_note=bool(data["is_credit_note"]),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


# ---------- outbox SQL ----------


class SqlOutboxRepository(OutboxRepository):
    """Outbox persistante : une session courte par operation, commit immediat."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    @staticmethod
    def _to_entry(row: DgiOutboxModel) -> OutboxEntry:
        return OutboxEntry(
            serial_number=row.serial_number,
            invoice=invoice_from_json(row.invoice_json),
            status=CertificationStatus(row.status),
            attempts=row.attempts,
            next_attempt_at=_dt(row.next_attempt_at),
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            updated_at=_dt(row.updated_at),
        )

    def enqueue(self, invoice: InvoiceDTO, now: datetime) -> OutboxEntry:
        existing = self.get(invoice.serial_number)
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
        self.save(entry)
        return entry

    def get(self, serial_number: str) -> OutboxEntry | None:
        with self._factory() as session:
            row = session.get(DgiOutboxModel, serial_number)
            return self._to_entry(row) if row is not None else None

    def save(self, entry: OutboxEntry) -> None:
        with self._factory() as session:
            row = session.get(DgiOutboxModel, entry.serial_number)
            if row is None:
                row = DgiOutboxModel(serial_number=entry.serial_number)
                session.add(row)
            row.invoice_json = invoice_to_json(entry.invoice)
            row.status = entry.status.value
            row.attempts = entry.attempts
            row.next_attempt_at = _iso(entry.next_attempt_at)
            row.last_error_code = entry.last_error_code
            row.last_error_message = entry.last_error_message
            row.updated_at = _iso(entry.updated_at)
            session.commit()

    def list_due(self, now: datetime) -> list[OutboxEntry]:
        wanted = (CertificationStatus.PENDING.value, CertificationStatus.RETRY.value)
        with self._factory() as session:
            rows = session.scalars(
                select(DgiOutboxModel).where(DgiOutboxModel.status.in_(wanted))
            ).all()
            entries = [self._to_entry(row) for row in rows]
        return [e for e in entries if e.next_attempt_at is None or e.next_attempt_at <= now]

    def list_all(self) -> list[OutboxEntry]:
        with self._factory() as session:
            rows = session.scalars(select(DgiOutboxModel).order_by(DgiOutboxModel.serial_number)).all()
            return [self._to_entry(row) for row in rows]


# ---------- journal SQL ----------


class SqlFiscalJournal(FiscalJournal):
    """Journal fiscal persistant. Un acces concurrent sur la meme sequence leve IntegrityError."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def append(
        self,
        invoice_id: str,
        serial_number: str,
        status: CertificationStatus,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        timestamp: datetime,
    ) -> FiscalJournalEntry:
        req_json = json.dumps(request_payload, sort_keys=True, ensure_ascii=True, default=str)
        resp_json = json.dumps(response_payload, sort_keys=True, ensure_ascii=True, default=str)
        with self._factory() as session:
            last = session.scalars(
                select(DgiJournalModel).order_by(DgiJournalModel.sequence.desc()).limit(1)
            ).first()
            seq = last.sequence + 1 if last is not None else 1
            prev_hash = last.entry_hash if last is not None else "GENESIS"
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
            session.add(
                DgiJournalModel(
                    sequence=seq,
                    timestamp=timestamp.isoformat(),
                    invoice_id=invoice_id,
                    serial_number=serial_number,
                    status=status.value,
                    request_json=req_json,
                    response_json=resp_json,
                    previous_hash=prev_hash,
                    entry_hash=entry_hash,
                )
            )
            session.commit()
        return FiscalJournalEntry(
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

    def entries(self) -> list[FiscalJournalEntry]:
        with self._factory() as session:
            rows = session.scalars(select(DgiJournalModel).order_by(DgiJournalModel.sequence)).all()
            return [
                FiscalJournalEntry(
                    sequence=row.sequence,
                    timestamp=datetime.fromisoformat(row.timestamp),
                    invoice_id=row.invoice_id,
                    serial_number=row.serial_number,
                    status=CertificationStatus(row.status),
                    request_json=row.request_json,
                    response_json=row.response_json,
                    previous_hash=row.previous_hash,
                    entry_hash=row.entry_hash,
                )
                for row in rows
            ]

    def _snapshot(self) -> FiscalJournal:
        memory = FiscalJournal()
        memory._entries = self.entries()
        return memory

    def search(
        self,
        invoice_id: str | None = None,
        serial_number: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[FiscalJournalEntry]:
        return self._snapshot().search(
            invoice_id=invoice_id, serial_number=serial_number, start_at=start_at, end_at=end_at
        )

    def verify_integrity(self) -> bool:
        return self._snapshot().verify_integrity()

    def export_csv(self) -> str:
        return self._snapshot().export_csv()
