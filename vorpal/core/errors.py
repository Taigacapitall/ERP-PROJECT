from __future__ import annotations


class VorpalError(Exception):
    """Erreur de base du domaine Vorpal."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__


class ValidationError(VorpalError):
    """Données ou paramètres invalides."""


class ConflictError(VorpalError):
    """Conflit d'état, de version ou d'unicité."""


class NotFoundError(VorpalError):
    """Ressource ou entité introuvable."""


class ExternalServiceError(VorpalError):
    """Erreur lors de l'appel à un service externe ou une dépendance."""


class ComplianceError(VorpalError):
    """Violation de règle métier, fiscale ou réglementaire."""
