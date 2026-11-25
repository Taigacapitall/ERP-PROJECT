"""Résultats des états : balance, grand livre, états financiers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from vorpal.modules.comptabilite.reporting.engine import StatementLine

ZERO = Decimal("0")


def split_sides(net: Decimal) -> tuple[Decimal, Decimal]:
    """Solde net (débit - crédit) -> (solde débiteur, solde créditeur)."""
    return (net, ZERO) if net >= 0 else (ZERO, -net)


@dataclass(frozen=True)
class TrialBalanceRow:
    code: str
    name: str
    opening_debit: Decimal
    opening_credit: Decimal
    period_debit: Decimal
    period_credit: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


@dataclass(frozen=True)
class TrialBalance:
    date_from: date
    date_to: date
    rows: tuple[TrialBalanceRow, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def _sum(self, attr: str) -> Decimal:
        return sum((getattr(r, attr) for r in self.rows), ZERO)

    @property
    def total_opening_debit(self) -> Decimal:
        return self._sum("opening_debit")

    @property
    def total_opening_credit(self) -> Decimal:
        return self._sum("opening_credit")

    @property
    def total_period_debit(self) -> Decimal:
        return self._sum("period_debit")

    @property
    def total_period_credit(self) -> Decimal:
        return self._sum("period_credit")

    @property
    def total_closing_debit(self) -> Decimal:
        return self._sum("closing_debit")

    @property
    def total_closing_credit(self) -> Decimal:
        return self._sum("closing_credit")

    @property
    def is_balanced(self) -> bool:
        return (
            self.total_opening_debit == self.total_opening_credit
            and self.total_period_debit == self.total_period_credit
            and self.total_closing_debit == self.total_closing_credit
        )


@dataclass(frozen=True)
class LedgerLine:
    entry_date: date
    number: str
    journal_code: str
    label: str
    reference: str
    partner_ref: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal  # solde cumulé (débit - crédit) après cette ligne


@dataclass(frozen=True)
class LedgerAccount:
    code: str
    name: str
    opening_balance: Decimal
    lines: tuple[LedgerLine, ...]

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), ZERO)

    @property
    def closing_balance(self) -> Decimal:
        return self.opening_balance + self.total_debit - self.total_credit


@dataclass(frozen=True)
class GeneralLedger:
    date_from: date
    date_to: date
    accounts: tuple[LedgerAccount, ...]


@dataclass(frozen=True)
class Statement:
    code: str
    title: str
    status: str
    date_from: date | None
    date_to: date
    lines: tuple[StatementLine, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def value(self, line_code: str) -> Decimal:
        for line in self.lines:
            if line.code == line_code:
                return line.value
        raise KeyError(line_code)
