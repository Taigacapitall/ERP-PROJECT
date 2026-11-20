"""Tests de chargement et validation de SalesSettings."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from vorpal.core import Currency, ValidationError
from vorpal.modules.ventes.settings import SalesSettings


def test_load_default_sales_settings() -> None:
    settings = SalesSettings.load()
    assert settings.status == "A_VALIDER"
    assert settings.seller.nif == "A0000000X"
    assert settings.default_currency == Currency.USD
    assert settings.rounding.decimals_for(Currency.USD) == 2
    assert settings.rounding.decimals_for(Currency.CDF) == 2
    assert settings.get_tax_group("A") is not None
    assert settings.get_tax_group("A").vat_rate == Decimal("0.16")  # type: ignore[union-attr]
    assert settings.get_tax_group("INCONNU") is None


def test_load_missing_file_raises_validation_error(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    with pytest.raises(ValidationError):
        SalesSettings.load(missing)


def test_load_invalid_json_raises_validation_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValidationError):
        SalesSettings.load(bad_file)


def test_load_rejects_float_vat_rate_without_exposing_values(tmp_path: Path) -> None:
    cfg_file = tmp_path / "float_rate.json"
    payload = {
        "seller": {"name": "SECRET_SELLER_NAME", "nif": "NIF-123"},
        "tax_groups": [{"code": "A", "label": "TVA", "vat_rate": 0.16}],
    }
    cfg_file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        SalesSettings.load(cfg_file)
    assert "SECRET_SELLER_NAME" not in str(exc_info.value)
