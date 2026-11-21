"""Numerotation sequentielle sans trou adossee a la base de donnees."""

from __future__ import annotations

from sqlalchemy.orm import Session

from vorpal.modules.achats.adapters.persistence.models import PurchaseSequenceModel
from vorpal.modules.achats.ports.repositories import PurchaseNumberSequence


class SqlPurchaseNumberSequence(PurchaseNumberSequence):
    """Incremente un compteur par cle et exercice au sein de la transaction courante."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        row = self._session.get(PurchaseSequenceModel, (sequence_key, fiscal_year))
        if row is None:
            row = PurchaseSequenceModel(
                sequence_key=sequence_key,
                fiscal_year=fiscal_year,
                last_value=1,
            )
            self._session.add(row)
        else:
            row.last_value += 1
        self._session.flush()
        return str(row.last_value)
