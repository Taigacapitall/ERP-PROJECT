"""Moteur déclaratif d'états financiers OHADA (logique pure, Decimal).

Une définition est un JSON : lignes feuilles (``accounts`` par préfixes, ``except``, ``sign``)
et lignes de calcul (``sum`` : références signées à des lignes précédentes).
Les soldes sont des (débit - crédit) par code de compte.
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
STATEMENTS = {"bilan": "ohada_bilan.json", "compte_resultat": "ohada_compte_resultat.json"}


@dataclass(frozen=True)
class StatementLine:
    code: str
    label: str
    level: int
    value: Decimal


class DefinitionError(ValueError):
    pass


def load_definition(name: str) -> dict[str, Any]:
    if name not in STATEMENTS:
        raise DefinitionError(f"état inconnu : {name}")
    data: dict[str, Any] = json.loads((DATA_DIR / STATEMENTS[name]).read_text(encoding="utf-8"))
    return data


def _matches(code: str, prefixes: list[str], excluded: list[str]) -> bool:
    return any(code.startswith(p) for p in prefixes) and not any(code.startswith(e) for e in excluded)


def evaluate(definition: Mapping[str, Any], balances: Mapping[str, Decimal]) -> list[StatementLine]:
    values: dict[str, Decimal] = {}
    out: list[StatementLine] = []
    for ln in definition["lines"]:
        code = ln["code"]
        if "accounts" in ln:
            raw = sum((b for c, b in balances.items()
                       if _matches(c, ln["accounts"], ln.get("except", []))), ZERO)
            value = raw * Decimal(ln.get("sign", 1))
        elif "sum" in ln:
            value = ZERO
            for term in ln["sum"]:
                sign, ref = (-1 if term[0] == "-" else 1), term.lstrip("+-")
                if ref not in values:
                    raise DefinitionError(f"{code} référence {ref} avant sa définition")
                value += sign * values[ref]
        else:
            raise DefinitionError(f"ligne {code} sans 'accounts' ni 'sum'")
        values[code] = value
        out.append(StatementLine(code, ln["label"], int(ln.get("level", 1)), value))
    return out


def coverage(definition: Mapping[str, Any], codes: list[str], scope: list[str]
             ) -> tuple[list[str], dict[str, list[str]]]:
    """(comptes du périmètre non couverts, comptes couverts par plusieurs lignes)."""
    unmapped: list[str] = []
    multiple: dict[str, list[str]] = {}
    leaves = [ln for ln in definition["lines"] if "accounts" in ln]
    for c in codes:
        if not any(c.startswith(s) for s in scope):
            continue
        hit = [ln["code"] for ln in leaves if _matches(c, ln["accounts"], ln.get("except", []))]
        if not hit:
            unmapped.append(c)
        elif len(hit) > 1:
            multiple[c] = hit
    return unmapped, multiple
