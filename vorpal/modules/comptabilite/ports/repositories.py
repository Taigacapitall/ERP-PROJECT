"""Ports de persistance du grand livre."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import TracebackType

from vorpal.modules.comptabilite.domain.accounts import Account, Journal
from vorpal.modules.comptabilite.domain.entries import PostedEntry
from vorpal.modules.comptabilite.domain.periods import FiscalYear


@dataclass(frozen=True)
class AccountTotals:
    """Cumul débit/crédit (devise société) d'un compte sur un intervalle."""

    account_code: str
    debit: Decimal
    credit: Decimal


@dataclass(frozen=True)
class LedgerRow:
    """Ligne d'écriture vue du grand livre."""

    entry_id: str
    number: str
    entry_date: date
    journal_code: str
    entry_label: str
    reference: str
    account_code: str
    debit: Decimal
    credit: Decimal
    label: str
    partner_ref: str | None


class LedgerRepository(ABC):
    """Persistance des comptes, journaux, exercices, écritures et réglages."""

    # --- plan comptable
    @abstractmethod
    def get_account(self, code: str) -> Account | None: ...

    @abstractmethod
    def save_account(self, account: Account) -> None: ...

    @abstractmethod
    def list_accounts(self, active_only: bool = False) -> list[Account]: ...

    @abstractmethod
    def find_first_by_prefix(self, prefix: str) -> Account | None:
        """Premier compte actif (code croissant) dont le code commence par ``prefix``."""

    # --- journaux
    @abstractmethod
    def get_journal(self, code: str) -> Journal | None: ...

    @abstractmethod
    def save_journal(self, journal: Journal) -> None: ...

    @abstractmethod
    def list_journals(self) -> list[Journal]: ...

    # --- exercices
    @abstractmethod
    def get_fiscal_year(self, code: str) -> FiscalYear | None: ...

    @abstractmethod
    def save_fiscal_year(self, year: FiscalYear) -> None: ...

    @abstractmethod
    def list_fiscal_years(self) -> list[FiscalYear]: ...

    # --- réglages (clé/valeur texte)
    @abstractmethod
    def get_setting(self, key: str) -> str | None: ...

    @abstractmethod
    def set_setting(self, key: str, value: str) -> None: ...

    # --- écritures
    @abstractmethod
    def get_entry(self, entry_id: str) -> PostedEntry | None: ...

    @abstractmethod
    def get_entry_by_source_ref(self, source_ref: str) -> PostedEntry | None: ...

    @abstractmethod
    def add_entry(self, entry: PostedEntry) -> None: ...

    @abstractmethod
    def set_reversed_by(self, entry_id: str, reversal_id: str) -> None: ...

    @abstractmethod
    def next_number(self, journal_code: str, fiscal_year: str) -> str:
        """Numéro suivant sans trou par (journal, exercice), alloué dans la transaction courante."""

    @abstractmethod
    def list_entries(self, date_from: date | None = None, date_to: date | None = None) -> list[PostedEntry]: ...

    # --- agrégats (exacts : montants stockés en unités mineures)
    @abstractmethod
    def account_totals(
        self,
        date_from: date | None,
        date_to: date | None,
        *,
        include_closing: bool,
        account_prefix: str | None = None,
    ) -> list[AccountTotals]:
        """Cumuls par compte pour les écritures datées dans [date_from, date_to] (bornes incluses)."""

    @abstractmethod
    def ledger_rows(
        self,
        account_code: str,
        date_from: date | None,
        date_to: date | None,
        *,
        include_closing: bool,
    ) -> list[LedgerRow]:
        """Lignes d'un compte, triées par date puis numéro."""


class LedgerUnitOfWork(ABC):
    """Unité de travail transactionnelle du grand livre."""

    ledger: LedgerRepository

    @abstractmethod
    def __enter__(self) -> LedgerUnitOfWork: ...

    @abstractmethod
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...
