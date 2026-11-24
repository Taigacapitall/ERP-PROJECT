"""Formateur partage et implementations SQL / memoire des sequences de numerotation."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vorpal.modules.ventes.adapters.persistence.models import NumberSequenceModel
from vorpal.modules.ventes.domain.errors import SalesValidationError
from vorpal.modules.ventes.ports.repositories import NumberSequence
from vorpal.modules.ventes.settings import NumberingSettings, SequenceFormatConfig


def get_sequence_config(numbering: NumberingSettings, sequence_key: str) -> SequenceFormatConfig:
    """Retourne la configuration d'une cle de sequence connue."""
    mapping: dict[str, SequenceFormatConfig] = {
        "quote": numbering.quote,
        "order": numbering.order,
        "invoice": numbering.invoice,
        "credit_note": numbering.credit_note,
        "payment": numbering.payment,
    }
    cfg = mapping.get(sequence_key)
    if cfg is None:
        raise SalesValidationError(f"Cle de sequence inconnue : '{sequence_key}'.")
    return cfg


def format_document_number(
    config: SequenceFormatConfig,
    fiscal_year: int,
    sequence_value: int,
) -> str:
    """Formate un numero de document de maniere partagee entre SQL et memoire."""
    if sequence_value < 1:
        raise SalesValidationError("La valeur de sequence doit etre strictement positive.")
    padded = str(sequence_value).zfill(config.padding)
    if config.include_year:
        return f"{config.prefix}-{fiscal_year}-{padded}"
    return f"{config.prefix}-{padded}"


class SqlNumberSequence(NumberSequence):
    """Sequence continue sans trou dans la transaction SQLAlchemy courante."""

    def __init__(self, session: Session, numbering_settings: NumberingSettings) -> None:
        self._session = session
        self._numbering = numbering_settings

    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        """Alloue le numero suivant par UPDATE atomique (verrou de ligne tenu jusqu'au commit).

        Pas de lecture-puis-ecriture : deux transactions concurrentes ne peuvent pas obtenir le meme numero.
        Premiere allocation d'une sequence : insertion sous SAVEPOINT, puis nouvel essai si une autre
        transaction a cree la ligne entre-temps.
        """
        cfg = get_sequence_config(self._numbering, sequence_key)
        same_sequence = (
            NumberSequenceModel.sequence_key == sequence_key,
            NumberSequenceModel.fiscal_year == fiscal_year,
        )
        for _ in range(5):
            value = self._session.execute(
                update(NumberSequenceModel)
                .where(*same_sequence)
                .values(last_value=NumberSequenceModel.last_value + 1)
                .returning(NumberSequenceModel.last_value)
            ).scalar_one_or_none()
            if value is not None:
                return format_document_number(cfg, fiscal_year, int(value))
            try:
                with self._session.begin_nested():
                    self._session.add(
                        NumberSequenceModel(sequence_key=sequence_key, fiscal_year=fiscal_year, last_value=1)
                    )
                    self._session.flush()
            except IntegrityError:
                continue
            return format_document_number(cfg, fiscal_year, 1)
        raise SalesValidationError("Impossible d'allouer un numero : sequence trop sollicitee, reessayez.")


class InMemoryNumberSequence(NumberSequence):
    """Sequence en memoire avec support de snapshot/restauration pour rollback."""

    def __init__(
        self,
        numbering_settings: NumberingSettings,
        counters: dict[tuple[str, int], int] | None = None,
    ) -> None:
        self._numbering = numbering_settings
        self._counters: dict[tuple[str, int], int] = dict(counters) if counters is not None else {}

    def clone_state(self) -> dict[tuple[str, int], int]:
        return dict(self._counters)

    def restore_state(self, state: dict[tuple[str, int], int]) -> None:
        self._counters = dict(state)

    def next_number(self, sequence_key: str, fiscal_year: int) -> str:
        cfg = get_sequence_config(self._numbering, sequence_key)
        key = (sequence_key, fiscal_year)
        next_val = self._counters.get(key, 0) + 1
        self._counters[key] = next_val
        return format_document_number(cfg, fiscal_year, next_val)
