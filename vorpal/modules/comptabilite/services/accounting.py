"""Façade applicative de la comptabilité : vérifie les permissions de l'acteur puis délègue."""

from __future__ import annotations

from datetime import date

from vorpal.core.security import AuthorizationService, User
from vorpal.modules.comptabilite.domain.accounts import Account, Journal
from vorpal.modules.comptabilite.domain.entries import DraftEntry, PostedEntry
from vorpal.modules.comptabilite.errors import AccountingPermissionError
from vorpal.modules.comptabilite.reporting.models import GeneralLedger, Statement, TrialBalance
from vorpal.modules.comptabilite.services.chart import ChartService
from vorpal.modules.comptabilite.services.closing import ClosingService
from vorpal.modules.comptabilite.services.common import AccountingDependencies
from vorpal.modules.comptabilite.services.posting import PostingService
from vorpal.modules.comptabilite.services.reports import ReportService
from vorpal.modules.comptabilite.settings import PERM_ADMIN, PERM_POST, PERM_VIEW


class AccountingService:
    """Point d'entrée pour les utilisateurs : ``compta.view`` (états), ``compta.post`` (écritures),
    ``compta.admin`` (plan, journaux, exercices, verrouillage, clôture)."""

    def __init__(
        self,
        deps: AccountingDependencies,
        authz: AuthorizationService,
        posting: PostingService,
        reports: ReportService,
        chart: ChartService,
        closing: ClosingService,
    ) -> None:
        self._authz = authz
        self._posting = posting
        self._reports = reports
        self._chart = chart
        self._closing = closing
        self._deps = deps

    def _require(self, actor: User, permission: str) -> None:
        if not self._authz.check(actor, permission):
            raise AccountingPermissionError(
                f"Accès refusé pour '{actor.username}' : permission '{permission}' requise."
            )

    # écritures
    def post_entry(self, actor: User, draft: DraftEntry) -> PostedEntry:
        self._require(actor, PERM_POST)
        return self._posting.post(draft)

    def reverse_entry(
        self, actor: User, entry_id: str, *, on_date: date | None = None, reason: str = ""
    ) -> PostedEntry:
        self._require(actor, PERM_POST)
        return self._posting.reverse(entry_id, on_date=on_date, reason=reason)

    # états
    def trial_balance(self, actor: User, date_from: date, date_to: date) -> TrialBalance:
        self._require(actor, PERM_VIEW)
        return self._reports.trial_balance(date_from, date_to)

    def general_ledger(
        self,
        actor: User,
        date_from: date,
        date_to: date,
        *,
        account_codes: list[str] | None = None,
        prefix: str | None = None,
    ) -> GeneralLedger:
        self._require(actor, PERM_VIEW)
        return self._reports.general_ledger(date_from, date_to, account_codes=account_codes, prefix=prefix)

    def balance_sheet(self, actor: User, as_of: date) -> Statement:
        self._require(actor, PERM_VIEW)
        return self._reports.balance_sheet(as_of)

    def income_statement(self, actor: User, date_from: date, date_to: date) -> Statement:
        self._require(actor, PERM_VIEW)
        return self._reports.income_statement(date_from, date_to)

    # administration
    def add_account(self, actor: User, account: Account) -> None:
        self._require(actor, PERM_ADMIN)
        self._chart.add_account(account)

    def add_journal(self, actor: User, journal: Journal) -> None:
        self._require(actor, PERM_ADMIN)
        self._chart.add_journal(journal)

    def create_fiscal_year(self, actor: User, year: int) -> None:
        self._require(actor, PERM_ADMIN)
        self._chart.ensure_fiscal_year(year)

    def lock_until(self, actor: User, day: date) -> None:
        self._require(actor, PERM_ADMIN)
        self._closing.lock_until(day)

    def close_fiscal_year(self, actor: User, code: str) -> PostedEntry | None:
        self._require(actor, PERM_ADMIN)
        return self._closing.close_fiscal_year(code)
