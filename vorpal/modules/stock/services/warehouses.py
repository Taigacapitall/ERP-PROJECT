"""Service applicatif de gestion des entrepots."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from vorpal.core.audit import AuditLog
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.stock.domain.errors import StockConflictError
from vorpal.modules.stock.domain.values import ZERO
from vorpal.modules.stock.domain.warehouses import Warehouse
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import load_warehouse, record_audit, require_permission


class WarehouseService:
    """Cree et desactive les entrepots (reserve a `stock.admin`)."""

    def __init__(
        self,
        uow_factory: Callable[[], StockUnitOfWork],
        audit: AuditLog,
        authz: AuthorizationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._authz = authz
        self._clock = clock

    def create_warehouse(self, actor: User, code: str, name: str) -> Warehouse:
        require_permission(self._authz, actor, "stock.admin")
        warehouse = Warehouse(code=code, name=name)
        with self._uow_factory() as uow:
            if uow.warehouses.get(warehouse.code) is not None:
                raise StockConflictError(f"Un entrepot '{warehouse.code}' existe deja.")
            uow.warehouses.add(warehouse)
        record_audit(
            self._audit,
            actor.username,
            "CREATE",
            "warehouse",
            warehouse.code,
            after={"name": warehouse.name},
            clock=self._clock,
        )
        return warehouse

    def deactivate_warehouse(self, actor: User, code: str) -> Warehouse:
        """Desactive un entrepot ; refuse tant qu'il contient du stock."""
        require_permission(self._authz, actor, "stock.admin")
        with self._uow_factory() as uow:
            warehouse = load_warehouse(uow, code)
            remaining = sum((lv.quantity for lv in uow.levels.list_by_warehouse(warehouse.code)), ZERO)
            if remaining > ZERO:
                raise StockConflictError(
                    f"Impossible de desactiver '{warehouse.code}' : il contient encore du stock."
                )
            warehouse.deactivate()
            uow.warehouses.add(warehouse)
        record_audit(
            self._audit,
            actor.username,
            "DEACTIVATE",
            "warehouse",
            warehouse.code,
            before={"is_active": True},
            after={"is_active": False},
            clock=self._clock,
        )
        return warehouse

    def reactivate_warehouse(self, actor: User, code: str) -> Warehouse:
        require_permission(self._authz, actor, "stock.admin")
        with self._uow_factory() as uow:
            warehouse = load_warehouse(uow, code)
            warehouse.reactivate()
            uow.warehouses.add(warehouse)
        record_audit(
            self._audit,
            actor.username,
            "REACTIVATE",
            "warehouse",
            warehouse.code,
            before={"is_active": False},
            after={"is_active": True},
            clock=self._clock,
        )
        return warehouse
