"""Politique de reprise avec backoff exponentiel et plafond de tentatives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class RetryPolicy:
    """Calcule l'eligibilite et le delai de prochaine tentative en cas de coupure ou erreur transitoire."""

    max_attempts: int = 5
    base_delay_seconds: int = 30
    max_delay_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts doit etre >= 1.")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Les delais de reprise ne peuvent pas etre negatifs.")

    def can_retry(self, attempts_made: int) -> bool:
        return attempts_made < self.max_attempts

    def delay_for_attempt(self, attempt_number: int) -> timedelta:
        if attempt_number < 1:
            attempt_number = 1
        raw_delay = self.base_delay_seconds * (2 ** (attempt_number - 1))
        capped = min(raw_delay, self.max_delay_seconds)
        return timedelta(seconds=capped)

    def next_attempt_at(self, now: datetime, attempt_number: int) -> datetime:
        return now + self.delay_for_attempt(attempt_number)
