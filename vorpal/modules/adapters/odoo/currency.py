"""Conversion devise de transaction -> devise société, avec écriture toujours équilibrée."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .dto import ZERO, JournalEntryDTO, PreparedLine
from .errors import UnbalancedEntryError

CENT = Decimal("0.01")
DEFAULT_TOLERANCE = Decimal("0.05")


def q(amount: Decimal) -> Decimal:
    """Arrondi monétaire (2 décimales, demi vers le haut). TODO(A_VALIDER) : règle USD/CDF."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def convert_entry(
    entry: JournalEntryDTO,
    rate: Decimal,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> tuple[PreparedLine, ...]:
    """Convertit chaque ligne au taux donné ; l'écart d'arrondi est absorbé par la plus
    grande ligne du côté le plus léger, sinon l'écriture est refusée."""
    if rate <= 0:
        raise UnbalancedEntryError("taux de change invalide")
    debits = [q(ln.debit * rate) for ln in entry.lines]
    credits = [q(ln.credit * rate) for ln in entry.lines]
    residual = sum(debits, ZERO) - sum(credits, ZERO)
    if residual != 0:
        if abs(residual) > tolerance:
            raise UnbalancedEntryError(f"écart de conversion {residual} > tolérance {tolerance}")
        if residual > 0:  # trop de débit -> on augmente le plus grand crédit
            idx = max(range(len(credits)), key=lambda i: credits[i])
            credits[idx] += residual
        else:
            idx = max(range(len(debits)), key=lambda i: debits[i])
            debits[idx] += -residual
    return tuple(
        PreparedLine(line=ln, debit_company=d, credit_company=c)
        for ln, d, c in zip(entry.lines, debits, credits, strict=True)
    )
