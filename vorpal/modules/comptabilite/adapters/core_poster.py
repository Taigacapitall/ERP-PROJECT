"""Implémentation du port ``AccountingPoster`` du Core (``post(JournalEntryDTO) -> str``)."""

from __future__ import annotations

from vorpal.core.contracts import AccountingPoster, JournalEntryDTO
from vorpal.modules.comptabilite.domain.entries import DraftEntry, DraftLine
from vorpal.modules.comptabilite.errors import AccountingValidationError
from vorpal.modules.comptabilite.services.chart import ChartService
from vorpal.modules.comptabilite.services.posting import PostingService


class CoreLedgerPoster(AccountingPoster):
    """Écriture du Core -> écriture du grand livre. Mono-devise (le contrat Core ne porte pas de taux) ;
    un code de compte court est résolu par préfixe (plans à 6 chiffres)."""

    def __init__(self, posting: PostingService, chart: ChartService) -> None:
        self._posting = posting
        self._chart = chart

    def post(self, entry: JournalEntryDTO) -> str:
        currencies = {line.currency.value for line in entry.lines}
        if len(currencies) != 1:
            raise AccountingValidationError("Écriture multi-devises non prise en charge par le port Core.")
        lines = tuple(
            DraftLine(
                account_code=self._chart.resolve_account(line.account).code,
                debit=line.debit,
                credit=line.credit,
                label=entry.label,
            )
            for line in entry.lines
        )
        posted = self._posting.post(
            DraftEntry(
                source_ref=entry.source_ref,
                journal_code=entry.journal,
                entry_date=entry.entry_date,
                currency=currencies.pop(),
                lines=lines,
                reference=entry.source_ref,
                label=entry.label,
            )
        )
        return posted.id
