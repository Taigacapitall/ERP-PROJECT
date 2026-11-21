"""Utilitaires communs aux services applicatifs du module Achats."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from vorpal.core.audit import AuditLog
from vorpal.core.errors import ComplianceError
from vorpal.core.security import AuthorizationService, User


def current_timestamp(clock: Callable[[], datetime] | None = None) -> datetime:
    """Retourne l'horodatage courant en UTC via l'horloge injectee."""
    if clock is not None:
        return clock()
    return datetime.now(UTC)


def require_permission(
    authz: AuthorizationService,
    actor: User,
    permission: str,
) -> None:
    """Verifie qu'un utilisateur actif possede la permission requise, sinon leve ComplianceError."""
    if not authz.check(actor, permission):
        raise ComplianceError(
            f"Permission '{permission}' refusee pour l'utilisateur '{actor.username}'."
        )


def record_audit(
    audit: AuditLog,
    actor: User,
    action: str,
    entity: str,
    entity_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Enregistre une entree dans le journal d'audit chaine."""
    audit.record(
        user=actor.username,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        timestamp=current_timestamp(clock),
    )
