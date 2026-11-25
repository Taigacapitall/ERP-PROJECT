"""Réglages du module Comptabilité."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PERM_VIEW = "compta.view"
PERM_POST = "compta.post"
PERM_ADMIN = "compta.admin"

SETTING_LOCKED_UNTIL = "locked_until"


@dataclass(frozen=True)
class AccountingSettings:
    """Réglages déclaratifs. TODO(A_VALIDER) : comptes de résultat et journal de clôture à confirmer."""

    company_currency: str = "USD"
    closing_journal: str = "OD"
    profit_account: str = "131"  # Résultat net : bénéfice
    loss_account: str = "139"  # Résultat net : perte
    conversion_tolerance: Decimal = Decimal("0.05")
