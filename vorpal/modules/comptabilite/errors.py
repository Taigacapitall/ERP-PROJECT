"""Erreurs du module Comptabilité (spécialisent les erreurs du Core, sans les modifier)."""

from __future__ import annotations

from vorpal.core.errors import ComplianceError, ConflictError, NotFoundError, ValidationError


class AccountingValidationError(ValidationError):
    """Donnée comptable invalide (compte, journal, montant, devise)."""


class UnbalancedEntryError(AccountingValidationError):
    """Écriture non équilibrée ou mal formée."""


class AccountingNotFoundError(NotFoundError):
    """Compte, journal, exercice ou écriture introuvable."""


class AccountingConflictError(ConflictError):
    """Conflit : doublon de référence source, exercice déjà clos, etc."""


class PeriodClosedError(ComplianceError):
    """Écriture refusée : exercice absent/clos ou date verrouillée."""


class AccountingPermissionError(ComplianceError):
    """Permission comptable manquante."""
