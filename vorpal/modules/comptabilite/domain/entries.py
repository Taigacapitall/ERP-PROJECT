"""Écritures comptables : brouillon, écriture comptabilisée (immuable), conversion de devise."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from vorpal.modules.comptabilite.errors import AccountingValidationError, UnbalancedEntryError

ZERO = Decimal("0")
CENT = Decimal("0.01")
DEFAULT_TOLERANCE = Decimal("0.05")


class EntryKind(str, Enum):
    NORMAL = "NORMAL"
    CLOSING = "CLOSING"  # écriture de clôture : exclue des mouvements de l'exercice dans les états


def quantize(amount: Decimal) -> Decimal:
    """Arrondi monétaire (2 décimales, demi vers le haut). TODO(A_VALIDER) : règle USD/CDF."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _strict(value: Decimal, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, Decimal):
        raise AccountingValidationError(f"{name} : Decimal strict attendu (float/bool interdits).")
    if not value.is_finite():
        raise AccountingValidationError(f"{name} : montant non fini.")
    return value


@dataclass(frozen=True)
class DraftLine:
    """Ligne saisie, en devise de l'écriture. Exactement un côté est non nul."""

    account_code: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    label: str = ""
    partner_ref: str | None = None


@dataclass(frozen=True)
class DraftEntry:
    """Écriture à comptabiliser. ``source_ref`` assure l'idempotence (unique dans tout le grand livre)."""

    source_ref: str
    journal_code: str
    entry_date: date
    currency: str
    lines: tuple[DraftLine, ...]
    reference: str = ""
    label: str = ""
    exchange_rate: Decimal | None = None  # unités de devise société pour 1 unité de `currency`
    dgi_code: str | None = None
    dgi_qr: str | None = None

    def validate(self) -> None:
        if not self.source_ref.strip():
            raise UnbalancedEntryError("source_ref obligatoire.")
        if len(self.lines) < 2:
            raise UnbalancedEntryError("Une écriture exige au moins deux lignes.")
        total_d = total_c = ZERO
        for line in self.lines:
            debit = _strict(line.debit, f"débit ({line.account_code})")
            credit = _strict(line.credit, f"crédit ({line.account_code})")
            if debit < 0 or credit < 0:
                raise UnbalancedEntryError("Montants négatifs interdits.")
            if (debit > 0) == (credit > 0):
                raise UnbalancedEntryError(
                    f"La ligne {line.account_code} doit avoir exactement un côté non nul."
                )
            total_d += debit
            total_c += credit
        if total_d != total_c:
            raise UnbalancedEntryError(f"Débit {total_d} différent du crédit {total_c} ({self.currency}).")

    def fingerprint(self) -> tuple[object, ...]:
        """Empreinte métier pour détecter une même source_ref avec un contenu différent."""
        return (
            self.journal_code,
            self.entry_date,
            self.currency,
            tuple((ln.account_code, ln.debit, ln.credit) for ln in self.lines),
        )


@dataclass(frozen=True)
class PostedLine:
    """Ligne comptabilisée : montants en devise société (debit/credit) et d'origine (tx_*)."""

    account_code: str
    debit: Decimal
    credit: Decimal
    tx_debit: Decimal
    tx_credit: Decimal
    label: str = ""
    partner_ref: str | None = None


@dataclass(frozen=True)
class PostedEntry:
    """Écriture comptabilisée. Immuable : on corrige par extourne, jamais par modification."""

    id: str
    number: str
    journal_code: str
    entry_date: date
    fiscal_year: str
    source_ref: str
    currency: str
    company_currency: str
    exchange_rate: Decimal
    lines: tuple[PostedLine, ...]
    posted_at: datetime
    reference: str = ""
    label: str = ""
    kind: EntryKind = EntryKind.NORMAL
    reversal_of: str | None = None
    reversed_by: str | None = None
    dgi_code: str | None = None
    dgi_qr: str | None = None
    extra: dict[str, str] = field(default_factory=dict, hash=False, compare=False)

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO)

    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.journal_code,
            self.entry_date,
            self.currency,
            tuple((ln.account_code, ln.tx_debit, ln.tx_credit) for ln in self.lines),
        )


def convert_lines(
    draft: DraftEntry,
    rate: Decimal,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> tuple[PostedLine, ...]:
    """Convertit en devise société. L'écart d'arrondi (≤ tolérance) est absorbé par la plus grande
    ligne du côté le plus léger ; au-delà, l'écriture est refusée."""
    if rate <= 0:
        raise UnbalancedEntryError("Taux de change invalide.")
    debits = [quantize(ln.debit * rate) for ln in draft.lines]
    credits = [quantize(ln.credit * rate) for ln in draft.lines]
    residual = sum(debits, ZERO) - sum(credits, ZERO)
    if residual != 0:
        if abs(residual) > tolerance:
            raise UnbalancedEntryError(f"Écart de conversion {residual} supérieur à la tolérance {tolerance}.")
        if residual > 0:
            idx = max(range(len(credits)), key=lambda i: credits[i])
            credits[idx] += residual
        else:
            idx = max(range(len(debits)), key=lambda i: debits[i])
            debits[idx] += -residual
    return tuple(
        PostedLine(
            account_code=ln.account_code,
            debit=d,
            credit=c,
            tx_debit=ln.debit,
            tx_credit=ln.credit,
            label=ln.label,
            partner_ref=ln.partner_ref,
        )
        for ln, d, c in zip(draft.lines, debits, credits, strict=True)
    )
