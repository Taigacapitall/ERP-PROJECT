"""Configuration declarative du module Stock (valeurs par defaut en code, JSON optionnel)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vorpal.core.errors import ValidationError
from vorpal.core.money import Currency
from vorpal.modules.stock.domain.values import MAX_QUANTITY_DECIMALS

_ALLOWED_KEYS = frozenset(
    {
        "default_currency",
        "default_unit",
        "default_quantity_decimals",
        "auto_create_missing_masters",
        "sequences",
        "metadata",
    }
)
SEQUENCE_KEYS: tuple[str, ...] = ("movement", "inventory")


@dataclass(frozen=True)
class SequenceConfig:
    """Format d'une sequence de numerotation : PREFIXE-ANNEE-000001."""

    prefix: str
    year_separator: str = "-"
    padding: int = 5

    def __post_init__(self) -> None:
        if not isinstance(self.prefix, str) or not 1 <= len(self.prefix) <= 20:
            raise ValidationError("Le prefixe de sequence doit contenir de 1 a 20 caracteres.")
        if not isinstance(self.year_separator, str) or len(self.year_separator) > 5:
            raise ValidationError("Le separateur de sequence doit contenir au plus 5 caracteres.")
        if isinstance(self.padding, bool) or not isinstance(self.padding, int):
            raise ValidationError("Le remplissage de sequence doit etre un entier.")
        if not 1 <= self.padding <= 12:
            raise ValidationError("Le remplissage de sequence doit etre compris entre 1 et 12.")


def _default_sequences() -> dict[str, SequenceConfig]:
    return {
        "movement": SequenceConfig(prefix="MVT", padding=6),
        "inventory": SequenceConfig(prefix="INV", padding=5),
    }


@dataclass(frozen=True)
class StockSettings:
    """Parametres du module Stock.

    TODO(A_VALIDER): formats de numerotation et precision par defaut des quantites.
    """

    default_currency: Currency = Currency.USD
    default_unit: str = "unite"
    default_quantity_decimals: int = 3
    auto_create_missing_masters: bool = False
    sequences: Mapping[str, SequenceConfig] = field(default_factory=_default_sequences)

    def __post_init__(self) -> None:
        if not isinstance(self.default_currency, Currency):
            raise ValidationError("La devise par defaut du stock est invalide.")
        if not isinstance(self.default_unit, str) or not self.default_unit.strip():
            raise ValidationError("L'unite par defaut est obligatoire.")
        if (
            isinstance(self.default_quantity_decimals, bool)
            or not isinstance(self.default_quantity_decimals, int)
            or not 0 <= self.default_quantity_decimals <= MAX_QUANTITY_DECIMALS
        ):
            raise ValidationError(
                f"La precision par defaut doit etre un entier entre 0 et {MAX_QUANTITY_DECIMALS}."
            )
        if not isinstance(self.auto_create_missing_masters, bool):
            raise ValidationError("auto_create_missing_masters doit etre un booleen.")
        for key in SEQUENCE_KEYS:
            if key not in self.sequences:
                raise ValidationError(f"La sequence '{key}' est absente de la configuration.")

    def sequence(self, key: str) -> SequenceConfig:
        return self.sequences[key]

    @classmethod
    def from_json_file(cls, path: Path | None = None) -> StockSettings:
        """Charge un JSON de surcharge ; sans chemin, retourne les valeurs par defaut.

        Les cles inconnues sont refusees (une faute de frappe ne doit pas etre ignoree).
        """
        if path is None:
            return cls()
        if not path.exists() or not path.is_file():
            raise ValidationError(f"Fichier de configuration introuvable : {path.name}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "Impossible de lire ou parser la configuration JSON du stock."
            ) from exc
        if not isinstance(data, dict):
            raise ValidationError("La racine de la configuration du stock doit etre un objet JSON.")
        unknown = sorted(set(data) - _ALLOWED_KEYS)
        if unknown:
            raise ValidationError(f"Cles de configuration du stock inconnues : {', '.join(unknown)}.")
        return cls._from_mapping(data)

    @classmethod
    def _from_mapping(cls, data: dict[str, Any]) -> StockSettings:
        kwargs: dict[str, Any] = {}
        if "default_currency" in data:
            try:
                kwargs["default_currency"] = Currency(data["default_currency"])
            except ValueError as exc:
                raise ValidationError("La devise par defaut du stock est invalide.") from exc
        for key in ("default_unit", "default_quantity_decimals", "auto_create_missing_masters"):
            if key in data:
                kwargs[key] = data[key]
        if "sequences" in data:
            raw = data["sequences"]
            if not isinstance(raw, dict):
                raise ValidationError("'sequences' doit etre un objet JSON.")
            sequences = _default_sequences()
            for name, cfg in raw.items():
                if name not in SEQUENCE_KEYS:
                    raise ValidationError(f"Sequence inconnue dans la configuration : '{name}'.")
                if not isinstance(cfg, dict):
                    raise ValidationError(f"La sequence '{name}' doit etre un objet JSON.")
                try:
                    sequences[name] = SequenceConfig(**cfg)
                except TypeError as exc:
                    raise ValidationError(f"Parametres invalides pour la sequence '{name}'.") from exc
            kwargs["sequences"] = sequences
        return cls(**kwargs)
