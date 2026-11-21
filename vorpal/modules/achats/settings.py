"""Configuration declarative du module Achats."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vorpal.core.errors import ValidationError
from vorpal.core.money import Currency

DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "data" / "purchase_settings.json"


class LandedCostAllocationMethod(str, Enum):
    """Mode de repartition des frais d'approche sur les lignes de reception."""

    BY_AMOUNT = "BY_AMOUNT"
    BY_QUANTITY = "BY_QUANTITY"


class SequenceConfig(BaseModel):
    """Configuration d'une sequence de numerotation."""

    model_config = ConfigDict(frozen=True)

    prefix: str = Field(min_length=1, max_length=20)
    year_separator: str = Field(default="-", max_length=5)
    padding: int = Field(default=5, ge=1, le=12)


class PurchaseSettings(BaseModel):
    """Parametres du module Achats charges depuis JSON."""

    model_config = ConfigDict(frozen=True)

    company_name: str = Field(default="A_VALIDER", min_length=1)
    default_currency: Currency = Currency.USD
    default_payment_terms_days: int = Field(default=30, ge=0, le=365)
    default_vat_rate: Decimal = Field(default=Decimal("0.16"), ge=Decimal("0"), le=Decimal("1"))
    landed_cost_allocation_method: LandedCostAllocationMethod = LandedCostAllocationMethod.BY_AMOUNT
    sequences: dict[str, SequenceConfig] = Field(
        default_factory=lambda: {
            "purchase_order": SequenceConfig(prefix="BC", year_separator="-", padding=5),
            "goods_receipt": SequenceConfig(prefix="REC", year_separator="-", padding=5),
        }
    )

    @field_validator("default_vat_rate", mode="before")
    @classmethod
    def _validate_vat_rate(cls, value: Any) -> Decimal:
        if isinstance(value, bool) or isinstance(value, float):
            raise TypeError(
                "Le taux de TVA doit etre fourni en Decimal, int ou str, jamais en float."
            )
        try:
            parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Taux de TVA invalide.") from exc
        if parsed.is_nan() or parsed.is_infinite():
            raise ValueError("Taux de TVA non fini.")
        return parsed

    @classmethod
    def from_json_file(cls, path: Path | None = None) -> PurchaseSettings:
        """Charge et valide la configuration depuis un fichier JSON."""
        target = path if path is not None else DEFAULT_SETTINGS_PATH
        if not target.exists() or not target.is_file():
            raise ValidationError(f"Fichier de configuration introuvable : {target.name}")
        try:
            raw_text = target.read_text(encoding="utf-8")
            data = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "Impossible de lire ou parser la configuration JSON des achats."
            ) from exc

        if not isinstance(data, dict):
            raise ValidationError(
                "La racine de la configuration des achats doit etre un objet JSON."
            )

        clean_payload: dict[str, Any] = {k: v for k, v in data.items() if k != "metadata"}
        try:
            return cls.model_validate(clean_payload)
        except Exception as exc:
            raise ValidationError("Configuration des achats invalide.") from exc
