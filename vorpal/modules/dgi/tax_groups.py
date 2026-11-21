"""Table versionnee par date des groupes de taxe DGI et correspondances de taxes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from vorpal.core.errors import ComplianceError
from vorpal.modules.dgi.models import strict_decimal

DEFAULT_TAX_GROUPS_JSON = Path(__file__).resolve().parent / "data" / "tax_groups.json"


@dataclass(frozen=True)
class TaxGroup:
    """Groupe de taxe DGI avec plage de validite temporelle."""

    code: str
    label: str
    rate: Decimal
    valid_from: date
    valid_to: date | None = None
    out_of_scope: bool = False

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ComplianceError("Le code du groupe de taxe est obligatoire.")
        if not self.label.strip():
            raise ComplianceError("Le libelle du groupe de taxe est obligatoire.")
        rate_dec = strict_decimal(self.rate, "rate")
        if rate_dec < Decimal("0"):
            raise ComplianceError("Le taux d'un groupe de taxe ne peut pas etre negatif.")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ComplianceError("La date de fin de validite doit etre >= date de debut.")
        object.__setattr__(self, "rate", rate_dec)

    def is_active_on(self, target_date: date) -> bool:
        if target_date < self.valid_from:
            return False
        if self.valid_to is not None and target_date > self.valid_to:
            return False
        return True


@dataclass(frozen=True)
class TaxMapping:
    """Correspondance entre un code/taux de taxe interne et un code de groupe DGI."""

    internal_tax_key: str
    dgi_group_code: str


class TaxGroupTable:
    """Table versionnee par date chargee depuis un fichier JSON declaratif."""

    def __init__(
        self,
        groups: list[TaxGroup],
        mappings: dict[str, str] | None = None,
    ) -> None:
        self._groups: list[TaxGroup] = list(groups)
        self._mappings: dict[str, str] = {
            k.strip().upper(): v.strip() for k, v in (mappings or {}).items()
        }

    @classmethod
    def from_json_file(cls, json_path: Path | None = None) -> TaxGroupTable:
        path = json_path if json_path is not None else DEFAULT_TAX_GROUPS_JSON
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_groups = payload.get("groups", [])
        groups: list[TaxGroup] = []
        for item in raw_groups:
            valid_from = date.fromisoformat(str(item["valid_from"]))
            raw_to = item.get("valid_to")
            valid_to = date.fromisoformat(str(raw_to)) if raw_to else None
            groups.append(
                TaxGroup(
                    code=str(item["code"]),
                    label=str(item["label"]),
                    rate=strict_decimal(item["rate"], "rate"),
                    valid_from=valid_from,
                    valid_to=valid_to,
                    out_of_scope=bool(item.get("out_of_scope", False)),
                )
            )
        raw_mappings = payload.get("mappings", {})
        mappings = {str(k): str(v) for k, v in raw_mappings.items()} if isinstance(raw_mappings, dict) else {}
        return cls(groups=groups, mappings=mappings)

    def resolve_group(self, group_code: str, target_date: date) -> TaxGroup:
        """Retourne le groupe de taxe actif pour le code et la date indiques."""
        matching = [g for g in self._groups if g.code == group_code and g.is_active_on(target_date)]
        if not matching:
            raise ComplianceError(
                f"Groupe de taxe '{group_code}' introuvable ou inactif a la date {target_date.isoformat()}."
            )
        return matching[-1]

    def map_internal_tax(self, internal_tax_key: str, target_date: date) -> TaxGroup:
        """Resout une taxe interne vers le groupe DGI actif a la date cible."""
        normalized_key = internal_tax_key.strip().upper()
        mapped_code = self._mappings.get(normalized_key, internal_tax_key.strip())
        return self.resolve_group(mapped_code, target_date)

    def list_active_on(self, target_date: date) -> list[TaxGroup]:
        return [g for g in self._groups if g.is_active_on(target_date)]
