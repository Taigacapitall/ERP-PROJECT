"""Implémentation SQLAlchemy du grand livre : dépôt, unité de travail, migration additive."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from types import TracebackType

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from vorpal.core.db import Base, MigrationStep, UnitOfWork
from vorpal.modules.comptabilite.adapters.persistence import models as m
from vorpal.modules.comptabilite.domain.accounts import Account, Journal, JournalKind
from vorpal.modules.comptabilite.domain.entries import EntryKind, PostedEntry, PostedLine
from vorpal.modules.comptabilite.domain.periods import FiscalYear, FiscalYearStatus
from vorpal.modules.comptabilite.errors import AccountingValidationError
from vorpal.modules.comptabilite.ports.repositories import (
    AccountTotals,
    LedgerRepository,
    LedgerRow,
    LedgerUnitOfWork,
)

_HUNDRED = Decimal(100)


def to_minor(amount: Decimal) -> int:
    """Montant -> unités mineures (centimes). Les montants société sont déjà à 2 décimales."""
    return int((amount * _HUNDRED).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_minor(value: int) -> Decimal:
    return Decimal(int(value)).scaleb(-2)


def create_schema_v1(engine: Engine) -> None:
    """Crée uniquement les tables compta_* (additif, idempotent)."""
    tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("compta_")]
    Base.metadata.create_all(bind=engine, tables=tables, checkfirst=True)


MIGRATIONS: dict[int, MigrationStep] = {1: create_schema_v1}


class SqlLedgerRepository(LedgerRepository):
    def __init__(self, session: Session) -> None:
        self._s = session

    # --- plan comptable
    @staticmethod
    def _account(row: m.AccountModel) -> Account:
        return Account(code=row.code, name=row.name, reconcilable=row.reconcilable, is_active=row.is_active)

    def get_account(self, code: str) -> Account | None:
        row = self._s.get(m.AccountModel, code)
        return self._account(row) if row is not None else None

    def save_account(self, account: Account) -> None:
        self._s.merge(
            m.AccountModel(
                code=account.code,
                name=account.name,
                reconcilable=account.reconcilable,
                is_active=account.is_active,
            )
        )
        self._s.flush()

    def list_accounts(self, active_only: bool = False) -> list[Account]:
        stmt = select(m.AccountModel).order_by(m.AccountModel.code)
        if active_only:
            stmt = stmt.where(m.AccountModel.is_active.is_(True))
        return [self._account(r) for r in self._s.scalars(stmt).all()]

    def find_first_by_prefix(self, prefix: str) -> Account | None:
        if not prefix.isdigit():
            raise AccountingValidationError(f"Préfixe de compte invalide '{prefix}'.")
        stmt = (
            select(m.AccountModel)
            .where(m.AccountModel.code.like(f"{prefix}%"), m.AccountModel.is_active.is_(True))
            .order_by(m.AccountModel.code)
            .limit(1)
        )
        row = self._s.scalars(stmt).first()
        return self._account(row) if row is not None else None

    # --- journaux
    @staticmethod
    def _journal(row: m.JournalModel) -> Journal:
        return Journal(
            code=row.code,
            name=row.name,
            kind=JournalKind(row.kind),
            currency=row.currency,
            is_active=row.is_active,
            default_account=row.default_account,
        )

    def get_journal(self, code: str) -> Journal | None:
        row = self._s.get(m.JournalModel, code)
        return self._journal(row) if row is not None else None

    def save_journal(self, journal: Journal) -> None:
        self._s.merge(
            m.JournalModel(
                code=journal.code,
                name=journal.name,
                kind=journal.kind.value,
                currency=journal.currency,
                is_active=journal.is_active,
                default_account=journal.default_account,
            )
        )
        self._s.flush()

    def list_journals(self) -> list[Journal]:
        rows = self._s.scalars(select(m.JournalModel).order_by(m.JournalModel.code)).all()
        return [self._journal(r) for r in rows]

    # --- exercices
    @staticmethod
    def _year(row: m.FiscalYearModel) -> FiscalYear:
        return FiscalYear(
            code=row.code,
            start=row.start_date,
            end=row.end_date,
            status=FiscalYearStatus(row.status),
        )

    def get_fiscal_year(self, code: str) -> FiscalYear | None:
        row = self._s.get(m.FiscalYearModel, code)
        return self._year(row) if row is not None else None

    def save_fiscal_year(self, year: FiscalYear) -> None:
        self._s.merge(
            m.FiscalYearModel(
                code=year.code, start_date=year.start, end_date=year.end, status=year.status.value
            )
        )
        self._s.flush()

    def list_fiscal_years(self) -> list[FiscalYear]:
        rows = self._s.scalars(select(m.FiscalYearModel).order_by(m.FiscalYearModel.start_date)).all()
        return [self._year(r) for r in rows]

    # --- réglages
    def get_setting(self, key: str) -> str | None:
        row = self._s.get(m.SettingModel, key)
        return row.value if row is not None else None

    def set_setting(self, key: str, value: str) -> None:
        self._s.merge(m.SettingModel(key=key, value=value))
        self._s.flush()

    # --- écritures
    def _hydrate(self, row: m.EntryModel) -> PostedEntry:
        line_rows = self._s.scalars(
            select(m.EntryLineModel)
            .where(m.EntryLineModel.entry_id == row.id)
            .order_by(m.EntryLineModel.line_order)
        ).all()
        lines = tuple(
            PostedLine(
                account_code=ln.account_code,
                debit=from_minor(ln.debit_minor),
                credit=from_minor(ln.credit_minor),
                tx_debit=Decimal(ln.tx_debit),
                tx_credit=Decimal(ln.tx_credit),
                label=ln.label,
                partner_ref=ln.partner_ref,
            )
            for ln in line_rows
        )
        return PostedEntry(
            id=row.id,
            number=row.number,
            journal_code=row.journal_code,
            entry_date=row.entry_date,
            fiscal_year=row.fiscal_year,
            source_ref=row.source_ref,
            currency=row.currency,
            company_currency=row.company_currency,
            exchange_rate=Decimal(row.exchange_rate),
            lines=lines,
            posted_at=datetime.fromisoformat(row.posted_at),
            reference=row.reference,
            label=row.label,
            kind=EntryKind(row.kind),
            reversal_of=row.reversal_of,
            reversed_by=row.reversed_by,
            dgi_code=row.dgi_code,
            dgi_qr=row.dgi_qr,
        )

    def get_entry(self, entry_id: str) -> PostedEntry | None:
        row = self._s.get(m.EntryModel, entry_id)
        return self._hydrate(row) if row is not None else None

    def get_entry_by_source_ref(self, source_ref: str) -> PostedEntry | None:
        row = self._s.scalars(select(m.EntryModel).where(m.EntryModel.source_ref == source_ref)).first()
        return self._hydrate(row) if row is not None else None

    def add_entry(self, entry: PostedEntry) -> None:
        self._s.add(
            m.EntryModel(
                id=entry.id,
                number=entry.number,
                journal_code=entry.journal_code,
                entry_date=entry.entry_date,
                fiscal_year=entry.fiscal_year,
                source_ref=entry.source_ref,
                reference=entry.reference,
                label=entry.label,
                currency=entry.currency,
                company_currency=entry.company_currency,
                exchange_rate=str(entry.exchange_rate),
                kind=entry.kind.value,
                posted_at=entry.posted_at.isoformat(),
                reversal_of=entry.reversal_of,
                reversed_by=entry.reversed_by,
                dgi_code=entry.dgi_code,
                dgi_qr=entry.dgi_qr,
            )
        )
        self._s.flush()
        for idx, ln in enumerate(entry.lines, start=1):
            self._s.add(
                m.EntryLineModel(
                    entry_id=entry.id,
                    line_order=idx,
                    account_code=ln.account_code,
                    debit_minor=to_minor(ln.debit),
                    credit_minor=to_minor(ln.credit),
                    tx_debit=str(ln.tx_debit),
                    tx_credit=str(ln.tx_credit),
                    label=ln.label,
                    partner_ref=ln.partner_ref,
                )
            )
        self._s.flush()

    def set_reversed_by(self, entry_id: str, reversal_id: str) -> None:
        self._s.execute(
            update(m.EntryModel).where(m.EntryModel.id == entry_id).values(reversed_by=reversal_id)
        )
        self._s.flush()

    def next_number(self, journal_code: str, fiscal_year: str) -> str:
        same = (
            m.SequenceModel.journal_code == journal_code,
            m.SequenceModel.fiscal_year == fiscal_year,
        )
        for _ in range(5):
            value = self._s.execute(
                update(m.SequenceModel)
                .where(*same)
                .values(last_value=m.SequenceModel.last_value + 1)
                .returning(m.SequenceModel.last_value)
            ).scalar_one_or_none()
            if value is not None:
                return f"{journal_code}-{fiscal_year}-{int(value):06d}"
            try:
                with self._s.begin_nested():
                    self._s.add(
                        m.SequenceModel(journal_code=journal_code, fiscal_year=fiscal_year, last_value=1)
                    )
                    self._s.flush()
            except IntegrityError:
                continue
            return f"{journal_code}-{fiscal_year}-{1:06d}"
        raise AccountingValidationError("Impossible d'allouer un numéro d'écriture, réessayez.")

    def list_entries(self, date_from: date | None = None, date_to: date | None = None) -> list[PostedEntry]:
        stmt = select(m.EntryModel).order_by(m.EntryModel.entry_date, m.EntryModel.number)
        if date_from is not None:
            stmt = stmt.where(m.EntryModel.entry_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(m.EntryModel.entry_date <= date_to)
        return [self._hydrate(r) for r in self._s.scalars(stmt).all()]

    # --- agrégats
    def account_totals(
        self,
        date_from: date | None,
        date_to: date | None,
        *,
        include_closing: bool,
        account_prefix: str | None = None,
    ) -> list[AccountTotals]:
        stmt = (
            select(
                m.EntryLineModel.account_code,
                func.sum(m.EntryLineModel.debit_minor),
                func.sum(m.EntryLineModel.credit_minor),
            )
            .join(m.EntryModel, m.EntryModel.id == m.EntryLineModel.entry_id)
            .group_by(m.EntryLineModel.account_code)
            .order_by(m.EntryLineModel.account_code)
        )
        if date_from is not None:
            stmt = stmt.where(m.EntryModel.entry_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(m.EntryModel.entry_date <= date_to)
        if not include_closing:
            stmt = stmt.where(m.EntryModel.kind != EntryKind.CLOSING.value)
        if account_prefix is not None:
            if not account_prefix.isdigit():
                raise AccountingValidationError(f"Préfixe de compte invalide '{account_prefix}'.")
            stmt = stmt.where(m.EntryLineModel.account_code.like(f"{account_prefix}%"))
        return [
            AccountTotals(code, from_minor(int(d or 0)), from_minor(int(c or 0)))
            for code, d, c in self._s.execute(stmt).all()
        ]

    def ledger_rows(
        self,
        account_code: str,
        date_from: date | None,
        date_to: date | None,
        *,
        include_closing: bool,
    ) -> list[LedgerRow]:
        stmt = (
            select(m.EntryModel, m.EntryLineModel)
            .join(m.EntryLineModel, m.EntryModel.id == m.EntryLineModel.entry_id)
            .where(m.EntryLineModel.account_code == account_code)
            .order_by(m.EntryModel.entry_date, m.EntryModel.number, m.EntryLineModel.line_order)
        )
        if date_from is not None:
            stmt = stmt.where(m.EntryModel.entry_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(m.EntryModel.entry_date <= date_to)
        if not include_closing:
            stmt = stmt.where(m.EntryModel.kind != EntryKind.CLOSING.value)
        return [
            LedgerRow(
                entry_id=e.id,
                number=e.number,
                entry_date=e.entry_date,
                journal_code=e.journal_code,
                entry_label=e.label,
                reference=e.reference,
                account_code=ln.account_code,
                debit=from_minor(ln.debit_minor),
                credit=from_minor(ln.credit_minor),
                label=ln.label,
                partner_ref=ln.partner_ref,
            )
            for e, ln in self._s.execute(stmt).all()
        ]


class SqlLedgerUnitOfWork(LedgerUnitOfWork):
    """Unité de travail SQLAlchemy composant celle du Core."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._core_uow = UnitOfWork(session_factory)

    def __enter__(self) -> LedgerUnitOfWork:
        self._core_uow.__enter__()
        session = self._core_uow.session
        assert session is not None
        self.ledger = SqlLedgerRepository(session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._core_uow.__exit__(exc_type, exc_val, exc_tb)

    def commit(self) -> None:
        session = self._core_uow.session
        if session is None:
            raise RuntimeError("La transaction n'est pas ouverte.")
        session.commit()

    def rollback(self) -> None:
        session = self._core_uow.session
        if session is None:
            raise RuntimeError("La transaction n'est pas ouverte.")
        session.rollback()
