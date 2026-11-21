"""Erreurs metier du module Achats."""

from __future__ import annotations

from vorpal.core.errors import ConflictError, NotFoundError, ValidationError


class PurchaseValidationError(ValidationError):
    """Erreur de validation des donnees d'achat."""


class PurchaseConflictError(ConflictError):
    """Erreur de transition d'etat, doublon ou quantite excedentaire."""


class PurchaseNotFoundError(NotFoundError):
    """Entite d'achat introuvable."""
