"""Tests des sequences de numerotation SQL et memoire."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vorpal.modules.ventes.adapters.persistence.models import Base
from vorpal.modules.ventes.adapters.persistence.numbering import (
    InMemoryNumberSequence,
    SqlNumberSequence,
    format_document_number,
    get_sequence_config,
)
from vorpal.modules.ventes.domain.errors import SalesValidationError
from vorpal.modules.ventes.settings import (
    NumberingSettings,
    SequenceFormatConfig,
)


@pytest.fixture
def numbering() -> NumberingSettings:
    return NumberingSettings()


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)

    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("key", "attribute"),
    [
        ("quote", "quote"),
        ("order", "order"),
        ("invoice", "invoice"),
        ("credit_note", "credit_note"),
        ("payment", "payment"),
    ],
)
def test_get_sequence_config_known_keys(
    numbering: NumberingSettings,
    key: str,
    attribute: str,
) -> None:
    assert get_sequence_config(numbering, key) == getattr(numbering, attribute)


def test_get_sequence_config_unknown_key(
    numbering: NumberingSettings,
) -> None:
    with pytest.raises(SalesValidationError, match="Cle de sequence inconnue"):
        get_sequence_config(numbering, "unknown")


def test_format_document_number_with_year() -> None:
    config = SequenceFormatConfig(prefix="FAC", padding=6)

    assert format_document_number(config, 2026, 12) == "FAC-2026-000012"


def test_format_document_number_without_year() -> None:
    config = SequenceFormatConfig(
        prefix="ENC",
        padding=4,
        include_year=False,
    )

    assert format_document_number(config, 2026, 7) == "ENC-0007"


@pytest.mark.parametrize("value", [0, -1])
def test_format_document_number_rejects_non_positive_values(
    value: int,
) -> None:
    config = SequenceFormatConfig(prefix="FAC")

    with pytest.raises(
        SalesValidationError,
        match="strictement positive",
    ):
        format_document_number(config, 2026, value)


def test_sql_sequence_creates_and_increments(
    session: Session,
    numbering: NumberingSettings,
) -> None:
    sequence = SqlNumberSequence(session, numbering)

    assert sequence.next_number("quote", 2026) == "DEV-2026-00001"
    assert sequence.next_number("quote", 2026) == "DEV-2026-00002"


def test_sql_sequence_is_independent_by_fiscal_year(
    session: Session,
    numbering: NumberingSettings,
) -> None:
    sequence = SqlNumberSequence(session, numbering)

    assert sequence.next_number("invoice", 2026) == "FAC-2026-000001"
    assert sequence.next_number("invoice", 2027) == "FAC-2027-000001"
    assert sequence.next_number("invoice", 2026) == "FAC-2026-000002"


def test_sql_sequence_rejects_unknown_key(
    session: Session,
    numbering: NumberingSettings,
) -> None:
    sequence = SqlNumberSequence(session, numbering)

    with pytest.raises(SalesValidationError):
        sequence.next_number("unknown", 2026)


def test_memory_sequence_creates_and_increments(
    numbering: NumberingSettings,
) -> None:
    sequence = InMemoryNumberSequence(numbering)

    assert sequence.next_number("order", 2026) == "CMD-2026-00001"
    assert sequence.next_number("order", 2026) == "CMD-2026-00002"


def test_memory_sequence_uses_initial_counters(
    numbering: NumberingSettings,
) -> None:
    sequence = InMemoryNumberSequence(
        numbering,
        {("payment", 2026): 8},
    )

    assert sequence.next_number("payment", 2026) == "ENC-2026-000009"


def test_memory_sequence_clone_and_restore(
    numbering: NumberingSettings,
) -> None:
    sequence = InMemoryNumberSequence(numbering)

    sequence.next_number("quote", 2026)
    snapshot = sequence.clone_state()

    sequence.next_number("quote", 2026)
    sequence.restore_state(snapshot)

    assert sequence.next_number("quote", 2026) == "DEV-2026-00002"


def test_memory_sequence_restore_replaces_previous_state(
    numbering: NumberingSettings,
) -> None:
    sequence = InMemoryNumberSequence(
        numbering,
        {("quote", 2026): 10},
    )

    sequence.restore_state({("invoice", 2026): 3})

    assert sequence.next_number("invoice", 2026) == "FAC-2026-000004"
    assert sequence.next_number("quote", 2026) == "DEV-2026-00001"


def test_memory_sequence_rejects_unknown_key(
    numbering: NumberingSettings,
) -> None:
    sequence = InMemoryNumberSequence(numbering)

    with pytest.raises(SalesValidationError):
        sequence.next_number("unknown", 2026)
