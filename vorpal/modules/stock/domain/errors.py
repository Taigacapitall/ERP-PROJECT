"""Erreurs metier du module Stock."""

from __future__ import annotations

from vorpal.core.errors import ConflictError, NotFoundError, ValidationError


class StockValidationError(ValidationError):
    """Donnee de stock invalide."""


class StockConflictError(ConflictError):
    """Conflit d'etat : doublon, stock insuffisant, transition interdite."""


class StockNotFoundError(NotFoundError):
    """Entite de stock introuvable."""
