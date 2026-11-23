"""Plans d'amortissement (linéaire / dégressif), par exercice civil, en Decimal.

TODO(A_VALIDER) : coefficients dégressifs, prorata (mois entier) et durées fiscales RDC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")


@dataclass(frozen=True)
class DepreciationLine:
    year: int
    months: int
    amount: Decimal
    cumulative: Decimal
    net_value: Decimal


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def schedule(cost: Decimal, residual: Decimal, years: int, start: date,
             method: str = "linear", coefficient: Decimal = Decimal("1.5")) -> list[DepreciationLine]:
    """Plan d'amortissement. Le mois de mise en service compte pour un mois entier."""
    if cost <= 0 or residual < 0 or residual >= cost or years <= 0:
        raise ValueError("paramètres d'amortissement invalides")
    if method not in ("linear", "declining"):
        raise ValueError("méthode inconnue")
    base = cost - residual
    total_months = years * 12
    months_left = total_months
    year = start.year
    in_year = 13 - start.month
    cumulative = ZERO
    out: list[DepreciationLine] = []
    while months_left > 0:
        months = min(in_year, months_left)
        net = cost - cumulative
        remaining_base = net - residual
        linear = remaining_base * months / months_left
        if method == "linear":
            amount = linear
        else:
            declining = net * (coefficient / years) * months / 12
            amount = max(declining, linear)
        amount = remaining_base if months == months_left else min(_q(amount), remaining_base)
        cumulative += amount
        months_left -= months
        out.append(DepreciationLine(year, months, amount, cumulative, cost - cumulative))
        year += 1
        in_year = 12
    return out
