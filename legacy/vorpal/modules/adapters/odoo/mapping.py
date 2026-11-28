"""Chargement et validation de la table de correspondance événement -> comptes/journaux."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import MappingError

DEFAULT_PATH = Path(__file__).parent / "data" / "account_mapping.json"

_REQUIRED: dict[str, tuple[str, ...]] = {
    "invoice.certified": ("journal", "receivable", "revenue", "vat_collected"),
    "credit_note.certified": ("journal", "receivable", "revenue", "vat_collected"),
    "payment.received": ("receivable", "routes"),
    "payroll.validated": ("journal", "currency", "gross_salary_expense", "employer_social_expense",
                          "net_pay_due", "social_security_due", "income_tax_due"),
}


class AccountMapping:
    def __init__(self, data: dict[str, Any]) -> None:
        events = data.get("events")
        if not isinstance(events, dict):
            raise MappingError("mapping : section 'events' absente")
        for name, keys in _REQUIRED.items():
            cfg = events.get(name)
            if not isinstance(cfg, dict):
                raise MappingError(f"mapping : événement {name} absent")
            for k in keys:
                if k not in cfg:
                    raise MappingError(f"mapping : {name}.{k} absent")
        self._events: dict[str, dict[str, Any]] = events
        self.status = str(data.get("status", ""))

    @classmethod
    def load(cls, path: Path | None = None) -> AccountMapping:
        return cls(json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8")))

    def event(self, name: str) -> dict[str, Any]:
        return self._events[name]

    def account_codes(self) -> set[str]:
        """Tous les codes de comptes cités (pour contrôle contre le plan)."""
        out: set[str] = set()

        def walk(v: Any) -> None:
            if isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, str) and v.isdigit():
                out.add(v)

        for name, cfg in self._events.items():
            walk({k: v for k, v in cfg.items() if k != "routes"})
        return out
