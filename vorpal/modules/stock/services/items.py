"""Service applicatif de gestion des articles de stock."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from vorpal.core.audit import AuditLog
from vorpal.core.money import Currency
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.stock.domain.errors import StockConflictError
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.values import ZERO, ensure_decimal, normalize_code
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import load_item, record_audit, require_permission
from vorpal.modules.stock.settings import StockSettings


class ItemService:
    """Cree, modifie et desactive les articles."""

    def __init__(
        self,
        uow_factory: Callable[[], StockUnitOfWork],
        settings: StockSettings,
        audit: AuditLog,
        authz: AuthorizationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._authz = authz
        self._clock = clock

    def create_item(
        self,
        actor: User,
        code: str,
        name: str,
        unit: str | None = None,
        currency: Currency | None = None,
        quantity_decimals: int | None = None,
        category: str = "",
        reorder_threshold: Decimal | int | str | None = None,
    ) -> StockItem:
        require_permission(self._authz, actor, "stock.create")
        item = StockItem(
            code=code,
            name=name,
            unit=unit if unit is not None else self._settings.default_unit,
            currency=currency if currency is not None else self._settings.default_currency,
            quantity_decimals=(
                quantity_decimals
                if quantity_decimals is not None
                else self._settings.default_quantity_decimals
            ),
            category=category,
            reorder_threshold=(
                ensure_decimal(reorder_threshold, "Le seuil de reapprovisionnement")
                if reorder_threshold is not None
                else None
            ),
        )
        with self._uow_factory() as uow:
            if uow.items.get(item.code) is not None:
                raise StockConflictError(f"Un article '{item.code}' existe deja.")
            uow.items.add(item)
        record_audit(
            self._audit,
            actor.username,
            "CREATE",
            "stock_item",
            item.code,
            after={"name": item.name, "unit": item.unit, "currency": item.currency.value},
            clock=self._clock,
        )
        return item

    def update_item(
        self,
        actor: User,
        code: str,
        name: str | None = None,
        category: str | None = None,
        reorder_threshold: Decimal | int | str | None = None,
        clear_reorder_threshold: bool = False,
    ) -> StockItem:
        require_permission(self._authz, actor, "stock.create")
        with self._uow_factory() as uow:
            item = load_item(uow, code)
            before: dict[str, object] = {
                "name": item.name,
                "category": item.category,
                "reorder_threshold": str(item.reorder_threshold),
            }
            item.update_details(name, category, reorder_threshold, clear_reorder_threshold)
            uow.items.add(item)
        record_audit(
            self._audit,
            actor.username,
            "UPDATE",
            "stock_item",
            item.code,
            before=before,
            after={
                "name": item.name,
                "category": item.category,
                "reorder_threshold": str(item.reorder_threshold),
            },
            clock=self._clock,
        )
        return item

    def deactivate_item(self, actor: User, code: str) -> StockItem:
        """Desactive un article ; refuse tant qu'il reste du stock (quantite non nulle)."""
        require_permission(self._authz, actor, "stock.admin")
        with self._uow_factory() as uow:
            item = load_item(uow, normalize_code(code, "Le code article"))
            remaining = sum((lv.quantity for lv in uow.levels.list_by_item(item.code)), ZERO)
            if remaining > ZERO:
                raise StockConflictError(
                    f"Impossible de desactiver '{item.code}' : il reste {remaining} en stock."
                )
            item.deactivate()
            uow.items.add(item)
        record_audit(
            self._audit,
            actor.username,
            "DEACTIVATE",
            "stock_item",
            item.code,
            before={"is_active": True},
            after={"is_active": False},
            clock=self._clock,
        )
        return item

    def reactivate_item(self, actor: User, code: str) -> StockItem:
        require_permission(self._authz, actor, "stock.admin")
        with self._uow_factory() as uow:
            item = load_item(uow, code)
            item.reactivate()
            uow.items.add(item)
        record_audit(
            self._audit,
            actor.username,
            "REACTIVATE",
            "stock_item",
            item.code,
            before={"is_active": False},
            after={"is_active": True},
            clock=self._clock,
        )
        return item
