"""OdooAccountingPoster : poste une écriture de façon idempotente et reprenable."""
from __future__ import annotations

from decimal import Decimal

from .client import OdooClient
from .currency import DEFAULT_TOLERANCE, convert_entry
from .dto import JournalEntryDTO, PostResult, PreparedMove
from .errors import DuplicateSourceRefError, MappingError


class OdooAccountingPoster:
    """Poster interne (DTO adaptateur). Le port Core est `core_poster.CoreAccountingPoster`."""

    def __init__(self, client: OdooClient, tolerance: Decimal = DEFAULT_TOLERANCE) -> None:
        self._client = client
        self._tolerance = tolerance

    def post(self, entry: JournalEntryDTO) -> PostResult:
        entry.validate()
        existing = self._client.find_move_by_source_ref(entry.source_ref)
        if existing is not None:
            return self._finish_existing(existing.move_id, existing.state)

        prepared = self._prepare(entry)
        try:
            move_id = self._client.create_move(prepared)
            created = True
        except DuplicateSourceRefError:
            # course avec un autre envoi : on reprend l'écriture existante
            again = self._client.find_move_by_source_ref(entry.source_ref)
            if again is None:
                raise
            return self._finish_existing(again.move_id, again.state)
        self._client.post_move(move_id)  # si ça échoue, la reprise trouvera le brouillon
        return PostResult(move_id=move_id, created=created, posted=True)

    def _finish_existing(self, move_id: int, state: str) -> PostResult:
        if state == "posted":
            return PostResult(move_id=move_id, created=False, posted=True)
        if state == "draft":
            self._client.post_move(move_id)
            return PostResult(move_id=move_id, created=False, posted=True)
        raise MappingError(f"écriture {move_id} à l'état '{state}' : intervention manuelle requise")

    def _prepare(self, entry: JournalEntryDTO) -> PreparedMove:
        company_ccy = self._client.get_company_currency()
        if entry.currency == company_ccy:
            rate = Decimal(1)
        else:
            found = entry.exchange_rate or self._client.get_rate(entry.currency, entry.entry_date)
            if found is None:
                raise MappingError(
                    f"aucun taux {entry.currency}->{company_ccy} au {entry.entry_date}")
            rate = found
        return PreparedMove(entry, company_ccy, rate, convert_entry(entry, rate, self._tolerance))
