"""Exceptions metier specifiques au module Ventes."""

from vorpal.core import ComplianceError, ConflictError, NotFoundError, ValidationError


class SalesValidationError(ValidationError):
    """Donnee metier invalide dans le module Ventes."""


class SalesConflictError(ConflictError):
    """Transition d'etat illegale ou doublon metier."""


class SalesNotFoundError(NotFoundError):
    """Entite de vente introuvable."""


class SalesComplianceError(ComplianceError):
    """Violation d'une regle fiscale, d'immutabilite ou d'autorisation."""
