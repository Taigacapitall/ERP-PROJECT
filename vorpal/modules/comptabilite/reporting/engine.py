"""Moteur déclaratif d'états financiers OHADA (logique pure, Decimal).

Une définition JSON contient des lignes feuilles (``accounts`` par préfixes, ``except``, ``sign``) et
des lignes de calcul (``sum`` : références signées à des lignes précédentes). Les soldes fournis sont
des (débit - crédit) par code de compte.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

ZERO = Decimal("0")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATEMENT_FILES: dict[str, str] = {
    "bilan": "ohada_bilan.json",
    "compte_resultat": "ohada_compte_resultat.json",
}


class DefinitionError(ValueError):
    """Définition d'état invalide."""


@dataclass(frozen=True)
class StatementLine:
    code: str
    label: str
    level: int
    value: Decimal


def load_definition(name: str) -> dict[str, Any]:
    if name not in STATEMENT_FILES:
        raise DefinitionError(f"État inconnu : {name}")
    data: dict[str, Any] = json.loads((DATA_DIR / STATEMENT_FILES[name]).read_text(encoding="utf-8"))
    return data


def _matches(code: str, prefixes: list[str], excluded: list[str]) -> bool:
    return any(code.startswith(p) for p in prefixes) and not any(code.startswith(e) for e in excluded)


def evaluate(definition: Mapping[str, Any], balances: Mapping[str, Decimal]) -> list[StatementLine]:
    values: dict[str, Decimal] = {}
    out: list[StatementLine] = []
    for line in definition["lines"]:
        code = str(line["code"])
        if "accounts" in line:
            raw = sum(
                (b for c, b in balances.items() if _matches(c, line["accounts"], line.get("except", []))),
                ZERO,
            )
            value = raw * Decimal(line.get("sign", 1))
        elif "sum" in line:
            value = ZERO
            for term in line["sum"]:
                sign = -1 if term[0] == "-" else 1
                ref = term.lstrip("+-")
                if ref not in values:
                    raise DefinitionError(f"{code} référence {ref} avant sa définition")
                value += sign * values[ref]
        else:
            raise DefinitionError(f"Ligne {code} sans 'accounts' ni 'sum'")
        values[code] = value
        out.append(StatementLine(code, str(line["label"]), int(line.get("level", 1)), value))
    return out


def coverage(
    definition: Mapping[str, Any], codes: list[str], scope: list[str]
) -> tuple[list[str], dict[str, list[str]]]:
    """(comptes du périmètre non couverts, comptes couverts par plusieurs lignes)."""
    unmapped: list[str] = []
    multiple: dict[str, list[str]] = {}
    leaves = [ln for ln in definition["lines"] if "accounts" in ln]
    for code in codes:
        if not any(code.startswith(s) for s in scope):
            continue
        hit = [ln["code"] for ln in leaves if _matches(code, ln["accounts"], ln.get("except", []))]
        if not hit:
            unmapped.append(code)
        elif len(hit) > 1:
            multiple[code] = hit
    return unmapped, multiple
