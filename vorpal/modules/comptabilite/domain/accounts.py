"""Comptes et journaux."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from vorpal.modules.comptabilite.errors import AccountingValidationError


class JournalKind(str, Enum):
    SALES = "SALES"
    PURCHASES = "PURCHASES"
    CASH = "CASH"
    BANK = "BANK"
    MISC = "MISC"
    PAYROLL = "PAYROLL"
    FIXED_ASSETS = "FIXED_ASSETS"


@dataclass(frozen=True)
class Account:
    """Compte du plan comptable. Un compte est postable s'il est actif."""

    code: str
    name: str
    reconcilable: bool = False
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.code.isdigit() or not 1 <= len(self.code) <= 10:
            raise AccountingValidationError(
                f"Code de compte invalide '{self.code}' : 1 à 10 chiffres attendus."
            )
        if self.code[0] == "0":
            raise AccountingValidationError("Un code de compte ne peut pas commencer par 0.")
        if not self.name.strip():
            raise AccountingValidationError(f"Le libellé du compte {self.code} est obligatoire.")

    @property
    def account_class(self) -> int:
        """Classe OHADA (1 à 9) déduite du premier chiffre."""
        return int(self.code[0])


@dataclass(frozen=True)
class Journal:
    code: str
    name: str
    kind: JournalKind
    currency: str | None = None  # None = devise de la société
    is_active: bool = True
    default_account: str | None = None  # compte de trésorerie par défaut (banque, caisse)

    def __post_init__(self) -> None:
        if not self.code.strip() or len(self.code) > 16 or " " in self.code:
            raise AccountingValidationError(f"Code de journal invalide '{self.code}'.")
        if not self.name.strip():
            raise AccountingValidationError(f"Le libellé du journal {self.code} est obligatoire.")
