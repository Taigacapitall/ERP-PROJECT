"""Exercices comptables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from vorpal.modules.comptabilite.errors import AccountingValidationError


class FiscalYearStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class FiscalYear:
    """Exercice comptable (code ex. « 2026 »). Les exercices ne se chevauchent pas."""

    code: str
    start: date
    end: date
    status: FiscalYearStatus = FiscalYearStatus.OPEN

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise AccountingValidationError("Le code de l'exercice est obligatoire.")
        if self.end < self.start:
            raise AccountingValidationError("La fin de l'exercice précède son début.")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @classmethod
    def calendar(cls, year: int) -> FiscalYear:
        return cls(code=str(year), start=date(year, 1, 1), end=date(year, 12, 31))
