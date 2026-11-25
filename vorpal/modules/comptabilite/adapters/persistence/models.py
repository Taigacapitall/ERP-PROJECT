"""Modèles ORM du grand livre (préfixe compta_).

Les montants de la devise société sont stockés en unités mineures (entiers) : les sommes SQL sont
exactes sur SQLite comme sur PostgreSQL. Les montants d'origine (``tx_*``) et les taux sont du texte
Decimal exact (jamais agrégés en SQL).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vorpal.core.db import Base


class AccountModel(Base):
    __tablename__ = "compta_accounts"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    reconcilable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class JournalModel(Base):
    __tablename__ = "compta_journals"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_account: Mapped[str | None] = mapped_column(String(10), nullable=True)


class FiscalYearModel(Base):
    __tablename__ = "compta_fiscal_years"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class SettingModel(Base):
    __tablename__ = "compta_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SequenceModel(Base):
    __tablename__ = "compta_sequences"

    journal_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    fiscal_year: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EntryModel(Base):
    __tablename__ = "compta_entries"
    __table_args__ = (
        UniqueConstraint("number", name="uq_compta_entries_number"),
        UniqueConstraint("source_ref", name="uq_compta_entries_source_ref"),
        Index("ix_compta_entries_date", "entry_date"),
        Index("ix_compta_entries_year", "fiscal_year"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    journal_code: Mapped[str] = mapped_column(
        String(16), ForeignKey("compta_journals.code"), nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reference: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    company_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange_rate: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    posted_at: Mapped[str] = mapped_column(String(40), nullable=False)
    reversal_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reversed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dgi_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dgi_qr: Mapped[str | None] = mapped_column(Text, nullable=True)


class EntryLineModel(Base):
    __tablename__ = "compta_entry_lines"
    __table_args__ = (
        Index("ix_compta_lines_entry", "entry_id"),
        Index("ix_compta_lines_account", "account_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(64), ForeignKey("compta_entries.id"), nullable=False)
    line_order: Mapped[int] = mapped_column(Integer, nullable=False)
    account_code: Mapped[str] = mapped_column(String(10), ForeignKey("compta_accounts.code"), nullable=False)
    debit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    credit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tx_debit: Mapped[str] = mapped_column(String(40), nullable=False)
    tx_credit: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    partner_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
