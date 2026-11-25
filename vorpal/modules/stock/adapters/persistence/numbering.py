"""Numerotation sequentielle sans trou adossee a la base de donnees."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vorpal.modules.stock.adapters.persistence.models import StockSequenceModel
from vorpal.modules.stock.domain.errors import StockConflictError
from vorpal.modules.stock.ports.repositories import StockNumberSequence


class SqlStockNumberSequence(StockNumberSequence):
    """Alloue le numero suivant par UPDATE atomique dans la transaction courante."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        """Pas de lecture-puis-ecriture : deux transactions concurrentes n'obtiennent jamais le meme numero.

        Premiere allocation d'une sequence : insertion sous SAVEPOINT, nouvel essai si une autre
        transaction a cree la ligne entre-temps (meme mecanisme que la numerotation de Ventes).
        """
        same_sequence = (
            StockSequenceModel.sequence_key == sequence_key,
            StockSequenceModel.fiscal_year == fiscal_year,
        )
        for _ in range(5):
            value = self._session.execute(
                update(StockSequenceModel)
                .where(*same_sequence)
                .values(last_value=StockSequenceModel.last_value + 1)
                .returning(StockSequenceModel.last_value)
            ).scalar_one_or_none()
            if value is not None:
                return str(value)
            try:
                with self._session.begin_nested():
                    self._session.add(
                        StockSequenceModel(
                            sequence_key=sequence_key, fiscal_year=fiscal_year, last_value=1
                        )
                    )
                    self._session.flush()
            except IntegrityError:
                continue
            return "1"
        raise StockConflictError("Impossible d'allouer un numero : sequence trop sollicitee, reessayez.")
