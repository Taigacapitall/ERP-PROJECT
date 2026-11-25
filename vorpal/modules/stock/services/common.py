"""Utilitaires communs aux services applicatifs du module Stock."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from vorpal.core.audit import AuditLog
from vorpal.core.errors import ComplianceError
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.stock.domain.errors import StockNotFoundError
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.movements import StockMovement
from vorpal.modules.stock.domain.values import normalize_code
from vorpal.modules.stock.domain.warehouses import Warehouse
from vorpal.modules.stock.manifest import EVENT_MOVEMENT_RECORDED
from vorpal.modules.stock.ports.publisher import StockEventPublisher
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.settings import SequenceConfig

SYSTEM_ACTOR = "system"


def current_timestamp(clock: Callable[[], datetime] | None = None) -> datetime:
    """Retourne l'horodatage courant en UTC via l'horloge injectee."""
    if clock is not None:
        return clock()
    return datetime.now(UTC)


def require_permission(authz: AuthorizationService, actor: User, permission: str) -> None:
    """Verifie qu'un utilisateur actif possede la permission requise, sinon leve ComplianceError."""
    if not authz.check(actor, permission):
        raise ComplianceError(
            f"Permission '{permission}' refusee pour l'utilisateur '{actor.username}'."
        )


def record_audit(
    audit: AuditLog,
    user: str,
    action: str,
    entity: str,
    entity_id: str,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Enregistre une entree dans le journal d'audit chaine."""
    audit.record(
        user=user,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        timestamp=current_timestamp(clock),
    )


def format_number(config: SequenceConfig, fiscal_year: int, raw_value: str) -> str:
    """Formate un numero de document : PREFIXE-ANNEE-000001."""
    sep = config.year_separator
    return f"{config.prefix}{sep}{fiscal_year}{sep}{raw_value.zfill(config.padding)}"


def movement_payload(movement: StockMovement) -> dict[str, Any]:
    """Charge utile serialisable (JSON) d'un mouvement, pour evenements et audit."""
    return {
        "movement_id": movement.id,
        "number": movement.number,
        "movement_type": movement.movement_type.value,
        "item_code": movement.item_code,
        "warehouse_code": movement.warehouse_code,
        "movement_date": movement.movement_date.isoformat(),
        "quantity_delta": str(movement.quantity_delta),
        "value_delta": str(movement.value_delta),
        "currency": movement.currency.value,
        "quantity_after": str(movement.quantity_after),
        "value_after": str(movement.value_after),
        "source_type": movement.source_type,
        "source_ref": movement.source_ref,
        "source_line_ref": movement.source_line_ref,
        "reason": movement.reason,
    }


def load_item(uow: StockUnitOfWork, code: str) -> StockItem:
    item = uow.items.get(normalize_code(code, "Le code article"))
    if item is None:
        raise StockNotFoundError(f"Article '{code}' introuvable.")
    return item


def load_active_item(uow: StockUnitOfWork, code: str) -> StockItem:
    item = load_item(uow, code)
    item.ensure_active()
    return item


def load_warehouse(uow: StockUnitOfWork, code: str) -> Warehouse:
    warehouse = uow.warehouses.get(normalize_code(code, "Le code entrepot"))
    if warehouse is None:
        raise StockNotFoundError(f"Entrepot '{code}' introuvable.")
    return warehouse


def load_active_warehouse(uow: StockUnitOfWork, code: str) -> Warehouse:
    warehouse = load_warehouse(uow, code)
    warehouse.ensure_active()
    return warehouse


def publish_movements(
    publisher: StockEventPublisher | None,
    movements: Iterable[StockMovement],
    correlation_id: str,
) -> None:
    """Publie `stock.movement.recorded` pour chaque mouvement (a appeler apres commit)."""
    if publisher is None:
        return
    for movement in movements:
        publisher.publish_event(
            event_type=EVENT_MOVEMENT_RECORDED,
            payload=movement_payload(movement),
            correlation_id=correlation_id,
        )
