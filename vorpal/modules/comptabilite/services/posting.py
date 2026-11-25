"""Comptabilisation des écritures : validation, conversion, numérotation sans trou, extourne."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from vorpal.core.money import Currency
from vorpal.modules.comptabilite.domain.accounts import Journal
from vorpal.modules.comptabilite.domain.entries import (
    DraftEntry,
    EntryKind,
    PostedEntry,
    PostedLine,
    convert_lines,
)
from vorpal.modules.comptabilite.domain.periods import FiscalYear, FiscalYearStatus
from vorpal.modules.comptabilite.errors import (
    AccountingConflictError,
    AccountingNotFoundError,
    AccountingValidationError,
    PeriodClosedError,
)
from vorpal.modules.comptabilite.ports.repositories import LedgerRepository
from vorpal.modules.comptabilite.services.common import AccountingDependencies
from vorpal.modules.comptabilite.settings import SETTING_LOCKED_UNTIL

SYSTEM_USER = "system.comptabilite"


def locked_until(repo: LedgerRepository) -> date | None:
    raw = repo.get_setting(SETTING_LOCKED_UNTIL)
    return date.fromisoformat(raw) if raw else None


def fiscal_year_for(repo: LedgerRepository, day: date) -> FiscalYear | None:
    for year in repo.list_fiscal_years():
        if year.contains(day):
            return year
    return None


class PostingService:
    """Service système (sans acteur) : sert le port ``AccountingPoster`` et les écritures automatiques."""

    def __init__(self, deps: AccountingDependencies) -> None:
        self._deps = deps

    # ---------------------------------------------------------------- contrôles
    def _check_context(
        self, repo: LedgerRepository, journal_code: str, currency: str, day: date
    ) -> tuple[Journal, FiscalYear]:
        journal = repo.get_journal(journal_code)
        if journal is None or not journal.is_active:
            raise AccountingNotFoundError(f"Journal '{journal_code}' introuvable ou inactif.")
        if journal.currency is not None and journal.currency != currency:
            raise AccountingValidationError(
                f"Le journal {journal.code} est tenu en {journal.currency} : écriture en {currency} refusée."
            )
        year = fiscal_year_for(repo, day)
        if year is None:
            raise PeriodClosedError(f"Aucun exercice comptable ne couvre le {day.isoformat()}.")
        if year.status != FiscalYearStatus.OPEN:
            raise PeriodClosedError(f"L'exercice {year.code} est clos : écriture au {day.isoformat()} refusée.")
        limit = locked_until(repo)
        if limit is not None and day <= limit:
            raise PeriodClosedError(f"Période verrouillée jusqu'au {limit.isoformat()} : écriture refusée.")
        return journal, year

    def _check_accounts(self, repo: LedgerRepository, codes: list[str]) -> None:
        unknown: list[str] = []
        for code in sorted(set(codes)):
            account = repo.get_account(code)
            if account is None or not account.is_active:
                unknown.append(code)
        if unknown:
            raise AccountingNotFoundError(f"Comptes introuvables ou inactifs : {', '.join(unknown)}.")

    def _rate(self, draft: DraftEntry) -> Decimal:
        company = self._deps.settings.company_currency
        if draft.currency == company:
            return Decimal(1)
        if draft.exchange_rate is not None:
            return draft.exchange_rate
        provider = self._deps.rate_provider
        if provider is None:
            raise AccountingValidationError(
                f"Aucun taux {draft.currency}->{company} fourni et aucun fournisseur de taux configuré."
            )
        try:
            return provider.get_rate(Currency(draft.currency), Currency(company), draft.entry_date)
        except ValueError as exc:
            raise AccountingValidationError(f"Devise inconnue : {draft.currency}.") from exc

    # ---------------------------------------------------------------- comptabilisation
    def post(self, draft: DraftEntry, *, kind: EntryKind = EntryKind.NORMAL) -> PostedEntry:
        """Comptabilise une écriture. Idempotent par ``source_ref`` : un rejeu renvoie l'écriture existante
        (et refuse un contenu différent sous la même référence)."""
        draft.validate()
        with self._deps.uow_factory() as uow:
            repo = uow.ledger
            existing = repo.get_entry_by_source_ref(draft.source_ref)
            if existing is not None:
                if existing.fingerprint() != draft.fingerprint():
                    raise AccountingConflictError(
                        f"La référence source '{draft.source_ref}' est déjà comptabilisée avec un contenu différent."
                    )
                return existing
            _, year = self._check_context(repo, draft.journal_code, draft.currency, draft.entry_date)
            self._check_accounts(repo, [ln.account_code for ln in draft.lines])
            rate = self._rate(draft)
            lines = convert_lines(draft, rate, self._deps.settings.conversion_tolerance)
            posted = PostedEntry(
                id=self._deps.id_factory(),
                number=repo.next_number(draft.journal_code, year.code),
                journal_code=draft.journal_code,
                entry_date=draft.entry_date,
                fiscal_year=year.code,
                source_ref=draft.source_ref,
                currency=draft.currency,
                company_currency=self._deps.settings.company_currency,
                exchange_rate=rate,
                lines=lines,
                posted_at=self._deps.clock(),
                reference=draft.reference,
                label=draft.label,
                kind=kind,
                dgi_code=draft.dgi_code,
                dgi_qr=draft.dgi_qr,
            )
            repo.add_entry(posted)
            uow.commit()
        self._after_commit(posted, "ENTRY_POSTED")
        return posted

    def reverse(self, entry_id: str, *, on_date: date | None = None, reason: str = "") -> PostedEntry:
        """Extourne : écriture miroir liée à l'originale. Une écriture ne s'extourne qu'une fois."""
        with self._deps.uow_factory() as uow:
            repo = uow.ledger
            original = repo.get_entry(entry_id)
            if original is None:
                raise AccountingNotFoundError(f"Écriture '{entry_id}' introuvable.")
            source_ref = f"reversal:{original.id}"
            already = repo.get_entry_by_source_ref(source_ref)
            if already is not None:
                return already
            if original.kind == EntryKind.CLOSING:
                raise AccountingConflictError("Une écriture de clôture ne s'extourne pas.")
            if original.reversal_of is not None:
                raise AccountingConflictError("Une extourne ne peut pas être extournée.")
            if original.reversed_by is not None:
                raise AccountingConflictError(f"L'écriture {original.number} est déjà extournée.")
            day = on_date or self._deps.clock().date()
            _, year = self._check_context(repo, original.journal_code, original.currency, day)
            mirror = tuple(
                PostedLine(
                    account_code=ln.account_code,
                    debit=ln.credit,
                    credit=ln.debit,
                    tx_debit=ln.tx_credit,
                    tx_credit=ln.tx_debit,
                    label=ln.label,
                    partner_ref=ln.partner_ref,
                )
                for ln in original.lines
            )
            label = f"Extourne de {original.number}" + (f" — {reason.strip()}" if reason.strip() else "")
            posted = PostedEntry(
                id=self._deps.id_factory(),
                number=repo.next_number(original.journal_code, year.code),
                journal_code=original.journal_code,
                entry_date=day,
                fiscal_year=year.code,
                source_ref=source_ref,
                currency=original.currency,
                company_currency=original.company_currency,
                exchange_rate=original.exchange_rate,
                lines=mirror,
                posted_at=self._deps.clock(),
                reference=original.number,
                label=label,
                reversal_of=original.id,
            )
            repo.add_entry(posted)
            repo.set_reversed_by(original.id, posted.id)
            uow.commit()
        self._after_commit(posted, "ENTRY_REVERSED")
        return posted

    def _after_commit(self, posted: PostedEntry, action: str) -> None:
        if self._deps.audit is not None:
            self._deps.audit.record(
                user=SYSTEM_USER,
                action=action,
                entity="compta_entry",
                entity_id=posted.id,
                after={"number": posted.number, "source_ref": posted.source_ref},
                timestamp=self._deps.clock(),
            )
        if self._deps.on_posted is not None:
            self._deps.on_posted(posted)
