"""Pont vers le port ``AccountingPoster`` du Core : ``post(JournalEntryDTO) -> str``.

Le DTO du Core est lu par duck typing (``journal``, ``source_ref``, ``entry_date``, ``label``,
``lines[].account/debit/credit/currency``). Une écriture du Core est mono-devise côté Odoo :
des lignes en devises différentes sont refusées (pas de taux porté par le contrat Core).
"""
from __future__ import annotations

from typing import Any

from .dto import JournalEntryDTO, JournalLineDTO
from .errors import MappingError, OdooUnavailableError
from .poster import OdooAccountingPoster


def to_adapter_entry(core: Any) -> JournalEntryDTO:
    currencies = {getattr(ln.currency, "value", ln.currency) for ln in core.lines}
    if len(currencies) != 1:
        raise MappingError("écriture multi-devises non prise en charge par l'adaptateur")
    return JournalEntryDTO(
        source_ref=core.source_ref, journal_code=core.journal, entry_date=core.entry_date,
        currency=str(currencies.pop()), narration=core.label, reference=core.source_ref,
        lines=tuple(JournalLineDTO(ln.account, ln.debit, ln.credit, core.label)
                    for ln in core.lines))


class CoreAccountingPoster:
    """Implémente ``vorpal.core.contracts.AccountingPoster`` (Protocol runtime_checkable)."""

    def __init__(self, poster: OdooAccountingPoster,
                 external_error: type[Exception] | None = None) -> None:
        self._poster = poster
        self._external_error = external_error

    def _wrap(self) -> type[Exception]:
        if self._external_error is not None:
            return self._external_error
        from vorpal.core.errors import ExternalServiceError  # import tardif : Core requis ici
        return ExternalServiceError

    def post(self, entry: Any) -> str:
        try:
            result = self._poster.post(to_adapter_entry(entry))
        except OdooUnavailableError as exc:
            raise self._wrap()(str(exc)) from exc
        return str(result.move_id)
