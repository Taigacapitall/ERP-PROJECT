"""Correspondances comptables des écritures automatiques (comptes et journaux par événement)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vorpal.modules.comptabilite.errors import AccountingValidationError

DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent / "data" / "account_mapping.json"


@dataclass(frozen=True)
class SalesMapping:
    journal: str
    receivable: str
    revenue: str
    vat_collected: str


@dataclass(frozen=True)
class PaymentMapping:
    receivable: str
    routes: dict[str, str] = field(default_factory=dict)  # "METHODE:DEVISE" -> code journal


@dataclass(frozen=True)
class AccountMapping:
    """TODO(A_VALIDER) : à confirmer par un expert-comptable OHADA RDC."""

    sales: SalesMapping
    payments: PaymentMapping

    @classmethod
    def load(cls, path: Path | None = None) -> AccountMapping:
        try:
            data: dict[str, Any] = json.loads((path or DEFAULT_MAPPING_PATH).read_text(encoding="utf-8"))
            s, p = data["sales"], data["payments"]
            return cls(
                sales=SalesMapping(s["journal"], s["receivable"], s["revenue"], s["vat_collected"]),
                payments=PaymentMapping(p["receivable"], {str(k): str(v) for k, v in p["routes"].items()}),
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AccountingValidationError(f"Correspondance comptable invalide : {exc}") from exc
