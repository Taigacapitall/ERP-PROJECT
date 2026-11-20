"""Tests de sérialisation des compteurs fiscaux des modèles Ventes."""

import json
from decimal import Decimal

import pytest

from vorpal.modules.ventes.adapters.persistence.models import (
    decode_typed_counters,
    encode_typed_counters,
)


def test_encode_typed_counters_preserves_supported_types() -> None:
    counters = {
        "texte": "ABC",
        "entier": 42,
        "decimal": Decimal("123.4500"),
    }

    encoded = encode_typed_counters(counters)
    decoded = json.loads(encoded)

    assert decoded == {
        "decimal": {"type": "decimal", "value": "123.4500"},
        "entier": {"type": "int", "value": "42"},
        "texte": {"type": "str", "value": "ABC"},
    }


def test_encode_typed_counters_sorts_keys() -> None:
    encoded = encode_typed_counters({
        "z": "dernier",
        "a": "premier",
    })

    assert list(json.loads(encoded)) == ["a", "z"]


def test_encode_typed_counters_accepts_empty_dictionary() -> None:
    assert encode_typed_counters({}) == "{}"


def test_encode_typed_counters_rejects_boolean() -> None:
    with pytest.raises(ValueError, match="booleens"):
        encode_typed_counters({"flag": True})


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_encode_typed_counters_rejects_non_finite_decimal(
    value: Decimal,
) -> None:
    with pytest.raises(ValueError, match="non finie"):
        encode_typed_counters({"montant": value})


def test_encode_typed_counters_rejects_unsupported_type() -> None:
    with pytest.raises(TypeError, match="float"):
        encode_typed_counters({"montant": 12.5})  # type: ignore[dict-item]


@pytest.mark.parametrize("raw_json", [None, "", " ", "\n\t"])
def test_decode_typed_counters_returns_empty_for_empty_input(
    raw_json: str | None,
) -> None:
    assert decode_typed_counters(raw_json) == {}


@pytest.mark.parametrize("raw_json", ['[]', '"texte"', "42", "null"])
def test_decode_typed_counters_returns_empty_for_non_object_json(
    raw_json: str,
) -> None:
    assert decode_typed_counters(raw_json) == {}


def test_decode_typed_counters_restores_supported_types() -> None:
    raw_json = json.dumps({
        "entier": {"type": "int", "value": "42"},
        "decimal": {"type": "decimal", "value": "123.4500"},
        "texte": {"type": "str", "value": "ABC"},
    })

    result = decode_typed_counters(raw_json)

    assert result == {
        "entier": 42,
        "decimal": Decimal("123.4500"),
        "texte": "ABC",
    }
    assert isinstance(result["entier"], int)
    assert isinstance(result["decimal"], Decimal)
    assert isinstance(result["texte"], str)


def test_decode_typed_counters_falls_back_to_string_for_unknown_type() -> None:
    raw_json = '{"valeur": {"type": "autre", "value": 123}}'

    assert decode_typed_counters(raw_json) == {"valeur": "123"}


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "int"},
        {"value": "123"},
        {"autre": "valeur"},
        "ancienne valeur",
        123,
        None,
    ],
)
def test_decode_typed_counters_supports_legacy_entries(entry: object) -> None:
    raw_json = json.dumps({"compteur": entry})

    assert decode_typed_counters(raw_json) == {
        "compteur": str(entry),
    }


def test_decode_typed_counters_rejects_invalid_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        decode_typed_counters("{json invalide")
