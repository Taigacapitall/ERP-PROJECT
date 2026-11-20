"""Configuration declarative du module Ventes."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from vorpal.core import Currency, ValidationError

DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "data" / "sales_settings.json"


class SellerSettings(BaseModel):
    """Informations legales du vendeur (jamais codees en dur)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    nif: str = Field(min_length=1)
    rccm: str | None = None
    address: str | None = None


class TaxGroupConfig(BaseModel):
    """Configuration declarative d'un groupe de taxe DGI."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1)
    vat_rate: Decimal = Field(ge=Decimal("0"))
    is_out_of_scope: bool = False
    status: str = "A_VALIDER"

    @field_validator("vat_rate", mode="before")
    @classmethod
    def _reject_float_rate(cls, value: Any) -> Any:
        if isinstance(value, float | bool):
            raise ValueError("Le taux de TVA doit etre fourni en Decimal ou chaine, jamais en float.")  # noqa: TRY004
        return value


class RoundingSettings(BaseModel):
    """Regles d'arrondi configurables par devise."""

    model_config = ConfigDict(frozen=True)

    # TODO(A_VALIDER): Confirmer le nombre de decimales et la strategie (par ligne vs global) selon DGI RDC.
    USD: int = Field(default=2, ge=0, le=6)
    CDF: int = Field(default=2, ge=0, le=6)
    mode: Literal["ROUND_HALF_UP"] = "ROUND_HALF_UP"
    strategy: Literal["PER_LINE", "GLOBAL"] = "PER_LINE"
    status: str = "A_VALIDER"

    def decimals_for(self, currency: Currency) -> int:
        """Retourne le nombre de decimales configure pour la devise."""
        if currency == Currency.USD:
            return self.USD
        return self.CDF


class SequenceFormatConfig(BaseModel):
    """Format declaratif d'une sequence de numerotation."""

    model_config = ConfigDict(frozen=True)

    # TODO(A_VALIDER): Format officiel de numerotation DGI RDC (prefixe, longueur, exercice).
    prefix: str = Field(min_length=1, max_length=16)
    padding: int = Field(default=6, ge=1, le=12)
    include_year: bool = True
    status: str = "A_VALIDER"


class NumberingSettings(BaseModel):
    """Configuration des sequences par type de document."""

    model_config = ConfigDict(frozen=True)

    quote: SequenceFormatConfig = Field(default_factory=lambda: SequenceFormatConfig(prefix="DEV", padding=5))
    order: SequenceFormatConfig = Field(default_factory=lambda: SequenceFormatConfig(prefix="CMD", padding=5))
    invoice: SequenceFormatConfig = Field(default_factory=lambda: SequenceFormatConfig(prefix="FAC", padding=6))
    credit_note: SequenceFormatConfig = Field(default_factory=lambda: SequenceFormatConfig(prefix="AVO", padding=6))
    payment: SequenceFormatConfig = Field(default_factory=lambda: SequenceFormatConfig(prefix="ENC", padding=6))


class ReminderLevelConfig(BaseModel):
    """Palier declaratif de relance d'impaye."""

    model_config = ConfigDict(frozen=True)

    # TODO(A_VALIDER): Delais et canaux de relance conformes aux usages commerciaux locaux.
    code: str = Field(min_length=1, max_length=32)
    days_after_due: int = Field(ge=1)
    channel: str = Field(min_length=1)
    template: str = Field(min_length=1)
    status: str = "A_VALIDER"


class SalesSettings(BaseModel):
    """Configuration globale du module Ventes."""

    model_config = ConfigDict(frozen=True)

    status: str = "A_VALIDER"
    seller: SellerSettings
    default_currency: Currency = Currency.USD
    # TODO(A_VALIDER): Delai de paiement par defaut en jours.
    default_payment_terms_days: int = Field(default=30, ge=0)
    tax_groups: tuple[TaxGroupConfig, ...] = ()
    rounding: RoundingSettings = Field(default_factory=RoundingSettings)
    numbering: NumberingSettings = Field(default_factory=NumberingSettings)
    reminder_levels: tuple[ReminderLevelConfig, ...] = ()
    # TODO(A_VALIDER): Regle DGI sur la conservation ou le re-attribution du numero apres rejet d'une facture.
    preserve_number_on_rejected_reopen: bool = True

    def get_tax_group(self, code: str) -> TaxGroupConfig | None:
        """Recherche un groupe de taxe configure par son code."""
        for group in self.tax_groups:
            if group.code == code:
                return group
        return None

    @classmethod
    def load(cls, path: Path | None = None) -> SalesSettings:
        """Charge et valide la configuration JSON sans exposer de valeurs sensibles dans les erreurs."""
        target = path or DEFAULT_SETTINGS_PATH
        try:
            raw_text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(f"Impossible de lire le fichier de configuration des ventes : {target.name}") from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValidationError("Le fichier de configuration des ventes n'est pas un JSON valide.") from exc

        if not isinstance(data, dict):
            raise ValidationError("La racine de la configuration des ventes doit etre un objet JSON.")

        try:
            return cls.model_validate(data)
        except PydanticValidationError as exc:
            fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
            raise ValidationError(f"Configuration des ventes invalide sur les champs : {fields}") from exc
