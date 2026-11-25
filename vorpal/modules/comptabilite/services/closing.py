"""Verrouillage de période et clôture d'exercice (transfert du résultat en classe 13)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from vorpal.modules.comptabilite.domain.entries import DraftEntry, DraftLine, EntryKind, PostedEntry
from vorpal.modules.comptabilite.domain.periods import FiscalYearStatus
from vorpal.modules.comptabilite.errors import AccountingConflictError, AccountingNotFoundError
from vorpal.modules.comptabilite.services.common import AccountingDependencies
from vorpal.modules.comptabilite.services.posting import PostingService, locked_until
from vorpal.modules.comptabilite.settings import SETTING_LOCKED_UNTIL

ZERO = Decimal("0")


class ClosingService:
    def __init__(self, deps: AccountingDependencies, posting: PostingService) -> None:
        self._deps = deps
        self._posting = posting

    def lock_until(self, day: date) -> None:
        """Interdit toute écriture datée jusqu'à ``day`` inclus. Le verrou ne recule jamais."""
        with self._deps.uow_factory() as uow:
            current = locked_until(uow.ledger)
            if current is not None and day < current:
                raise AccountingConflictError(
                    f"Le verrou ne peut pas reculer (actuel : {current.isoformat()}, demandé : {day.isoformat()})."
                )
            uow.ledger.set_setting(SETTING_LOCKED_UNTIL, day.isoformat())
            uow.commit()

    def close_fiscal_year(self, code: str) -> PostedEntry | None:
        """Solde les comptes 6 à 8 vers 131 (bénéfice) ou 139 (perte), puis clôt et verrouille l'exercice.

        Idempotent : rejouable après une interruption. Retourne l'écriture de clôture (None si aucun mouvement).
        """
        with self._deps.uow_factory() as uow:
            repo = uow.ledger
            year = repo.get_fiscal_year(code)
            if year is None:
                raise AccountingNotFoundError(f"Exercice '{code}' introuvable.")
            earlier_open = [
                y.code
                for y in repo.list_fiscal_years()
                if y.end < year.start and y.status != FiscalYearStatus.CLOSED
            ]
            if earlier_open:
                raise AccountingConflictError(
                    f"Clôturez d'abord les exercices antérieurs : {', '.join(earlier_open)}."
                )
            nets = {
                t.account_code: t.debit - t.credit
                for t in repo.account_totals(year.start, year.end, include_closing=False)
                if t.account_code[0] in "678"
            }
        settings = self._deps.settings
        lines = [
            DraftLine(c, debit=-n, label=f"Clôture {code}") if n < 0 else DraftLine(c, credit=n, label=f"Clôture {code}")
            for c, n in sorted(nets.items())
            if n != 0
        ]
        total = sum(nets.values(), ZERO)
        if total < 0:
            lines.append(DraftLine(settings.profit_account, credit=-total, label=f"Résultat {code} : bénéfice"))
        elif total > 0:
            lines.append(DraftLine(settings.loss_account, debit=total, label=f"Résultat {code} : perte"))
        closing: PostedEntry | None = None
        if lines:
            closing = self._posting.post(
                DraftEntry(
                    source_ref=f"closing:{code}",
                    journal_code=settings.closing_journal,
                    entry_date=year.end,
                    currency=settings.company_currency,
                    lines=tuple(lines),
                    label=f"Clôture de l'exercice {code}",
                ),
                kind=EntryKind.CLOSING,
            )
        with self._deps.uow_factory() as uow:
            uow.ledger.save_fiscal_year(replace(year, status=FiscalYearStatus.CLOSED))
            current = locked_until(uow.ledger)
            if current is None or current < year.end:
                uow.ledger.set_setting(SETTING_LOCKED_UNTIL, year.end.isoformat())
            uow.commit()
        return closing
