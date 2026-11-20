from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

from vorpal.core.errors import NotFoundError, ValidationError


class Currency(str, Enum):
    USD = "USD"
    CDF = "CDF"


CURRENCY_DECIMALS: dict[Currency, int] = {
    Currency.USD: 2,
    Currency.CDF: 2,
}


def _to_decimal(value: Decimal | str | float) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError("Les booléens ne sont pas des montants valides.")
    if isinstance(value, float):
        raise ValidationError("Les montants en float sont interdits; utiliser Decimal.")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValidationError(f"Montant décimal invalide: {value!r}") from exc
    else:
        raise ValidationError(f"Type de montant non supporté: {type(value).__name__}")
    if not dec.is_finite():
        raise ValidationError("Montant invalide: NaN et Infinity sont interdits.")
    return dec


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        dec = _to_decimal(self.amount)
        if not isinstance(self.currency, Currency):
            raise ValidationError(f"Devise invalide: {self.currency!r}")
        object.__setattr__(self, "amount", dec)

    def round(self, decimals: int | None = None, rounding: str = ROUND_HALF_UP) -> Money:
        target_decimals = CURRENCY_DECIMALS[self.currency] if decimals is None else decimals
        if target_decimals < 0:
            raise ValidationError("Le nombre de décimales doit être >= 0.")
        quantum = Decimal(1) if target_decimals == 0 else Decimal(f"1e-{target_decimals}")
        rounded = self.amount.quantize(quantum, rounding=rounding)
        return Money(amount=rounded, currency=self.currency)

    def _ensure_same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise ValidationError("Opération impossible avec un objet non-Money.")
        if self.currency != other.currency:
            raise ValidationError(
                f"Incompatibilité de devises: {self.currency.value} vs {other.currency.value}."
            )

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: object) -> Money:
        if isinstance(factor, float):
            raise ValidationError("La multiplication par float est interdite; utiliser Decimal ou int.")
        if isinstance(factor, Money):
            raise ValidationError("La multiplication de deux Money est interdite.")
        dec_factor = _to_decimal(factor)  # type: ignore[arg-type]
        return Money(amount=self.amount * dec_factor, currency=self.currency)

    def __rmul__(self, factor: object) -> Money:
        return self.__mul__(factor)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._ensure_same_currency(other)
        return self.amount >= other.amount


class ExchangeRateProvider(ABC):
    @abstractmethod
    def get_rate(self, from_currency: Currency, to_currency: Currency, on_date: date) -> Decimal:
        """Retourne le taux de conversion tel que `montant * taux = montant_converti`."""


class InMemoryRateProvider(ExchangeRateProvider):
    """Fournisseur de taux en mémoire indexé par (from, to, date)."""

    def __init__(
        self,
        rates: dict[tuple[Currency, Currency, date], Decimal] | None = None,
    ) -> None:
        self._rates: dict[tuple[Currency, Currency, date], Decimal] = {}
        if rates:
            for (src, dst, dt), rate in rates.items():
                self.set_rate(src, dst, dt, rate)

    def set_rate(
        self,
        from_currency: Currency,
        to_currency: Currency,
        on_date: date,
        rate: Decimal,
    ) -> None:
        dec_rate = _to_decimal(rate)
        if dec_rate <= Decimal(0):
            raise ValidationError("Le taux de change doit être strictement positif.")
        self._rates[(from_currency, to_currency, on_date)] = dec_rate

    def get_rate(self, from_currency: Currency, to_currency: Currency, on_date: date) -> Decimal:
        if from_currency == to_currency:
            return Decimal(1)
        key = (from_currency, to_currency, on_date)
        if key in self._rates:
            return self._rates[key]
        reverse_key = (to_currency, from_currency, on_date)
        if reverse_key in self._rates:
            return Decimal(1) / self._rates[reverse_key]
        raise NotFoundError(
            f"Taux introuvable pour {from_currency.value}->{to_currency.value} à la date {on_date.isoformat()}."
        )


def convert(
    money: Money,
    to: Currency,
    on_date: date,
    provider: ExchangeRateProvider,
    round_result: bool = True,
) -> Money:
    rate = provider.get_rate(money.currency, to, on_date)
    converted = Money(amount=money.amount * rate, currency=to)
    return converted.round() if round_result else converted
