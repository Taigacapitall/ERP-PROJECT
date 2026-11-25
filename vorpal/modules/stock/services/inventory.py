"""Service applicatif d'inventaire physique : ouverture, comptage, validation, annulation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from vorpal.core.audit import AuditLog
from vorpal.core.money import Money
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.stock.domain.errors import (
    StockConflictError,
    StockNotFoundError,
    StockValidationError,
)
from vorpal.modules.stock.domain.inventory import InventoryCount, InventoryLine, InventoryStatus
from vorpal.modules.stock.domain.movements import SOURCE_INVENTORY, StockMovement
from vorpal.modules.stock.domain.values import ZERO, ensure_unit_cost, normalize_code
from vorpal.modules.stock.manifest import EVENT_INVENTORY_VALIDATED
from vorpal.modules.stock.ports.publisher import StockEventPublisher
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import (
    format_number,
    load_active_item,
    load_active_warehouse,
    record_audit,
    require_permission,
)
from vorpal.modules.stock.services.ledger import (
    MovementOrigin,
    PostedMovement,
    StockLedger,
    emit_posted,
)
from vorpal.modules.stock.settings import StockSettings


@dataclass(frozen=True)
class InventoryVariance:
    """Ecart constate a la validation (compare au solde courant, pas a l'ouverture)."""

    item_code: str
    theoretical_quantity: Decimal
    counted_quantity: Decimal
    quantity_delta: Decimal
    value_delta: Decimal


@dataclass(frozen=True)
class InventoryResult:
    count: InventoryCount
    variances: list[InventoryVariance]
    movements: list[StockMovement]


class InventoryService:
    """Inventaire physique (permission `stock.inventory`).

    A la validation, chaque ligne comptee ramene le solde courant a la quantite comptee. Les lignes
    non comptees sont ignorees. Un ecart positif sur un article a stock nul exige un cout unitaire.
    """

    def __init__(
        self,
        uow_factory: Callable[[], StockUnitOfWork],
        settings: StockSettings,
        audit: AuditLog,
        authz: AuthorizationService,
        ledger: StockLedger,
        publisher: StockEventPublisher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._authz = authz
        self._ledger = ledger
        self._publisher = publisher
        self._clock = clock

    def open_count(
        self,
        actor: User,
        count_id: str,
        warehouse_code: str,
        count_date: date,
        item_codes: Sequence[str] | None = None,
        notes: str = "",
    ) -> InventoryCount:
        """Ouvre une fiche : releve le stock theorique (tout l'entrepot, ou les articles listes)."""
        require_permission(self._authz, actor, "stock.inventory")
        with self._uow_factory() as uow:
            warehouse = load_active_warehouse(uow, warehouse_code)
            cid = normalize_code(count_id, "L'identifiant de la fiche d'inventaire")
            if uow.inventories.get(cid) is not None:
                raise StockConflictError(f"Une fiche d'inventaire '{cid}' existe deja.")
            levels = {lv.item_code: lv.quantity for lv in uow.levels.list_by_warehouse(warehouse.code)}
            if item_codes is None:
                wanted = sorted(code for code, qty in levels.items() if qty > ZERO)
            else:
                wanted = sorted({normalize_code(c, "Le code article") for c in item_codes})
                for code in wanted:
                    load_active_item(uow, code)
            count = InventoryCount(
                id=cid,
                warehouse_code=warehouse.code,
                count_date=count_date,
                notes=notes,
                lines=[InventoryLine(code, levels.get(code, ZERO)) for code in wanted],
            )
            uow.inventories.add(count)
        record_audit(
            self._audit,
            actor.username,
            "OPEN",
            "inventory_count",
            count.id,
            after={"warehouse_code": count.warehouse_code, "lines": len(count.lines)},
            clock=self._clock,
        )
        return count

    def record_count(
        self,
        actor: User,
        count_id: str,
        item_code: str,
        counted_quantity: Decimal | int | str,
        unit_cost: Money | None = None,
    ) -> InventoryCount:
        """Saisit ou corrige le comptage d'un article (l'ajoute a la fiche si besoin)."""
        require_permission(self._authz, actor, "stock.inventory")
        with self._uow_factory() as uow:
            count = uow.inventories.get(normalize_code(count_id, "L'identifiant de la fiche"))
            if count is None:
                raise StockNotFoundError(f"Fiche d'inventaire '{count_id}' introuvable.")
            item = load_active_item(uow, item_code)
            cost: Decimal | None = None
            if unit_cost is not None:
                if unit_cost.currency != item.currency:
                    raise StockValidationError(
                        f"Le cout de '{item.code}' doit etre en {item.currency.value}."
                    )
                cost = ensure_unit_cost(unit_cost.amount)
            level = uow.levels.get(item.code, count.warehouse_code)
            count.set_count(
                item.code,
                item.quantity(counted_quantity, "La quantite comptee", allow_zero=True),
                level.quantity if level is not None else ZERO,
                cost,
            )
            uow.inventories.add(count)
        return count

    def validate_count(self, actor: User, count_id: str) -> InventoryResult:
        """Valide la fiche : cree un mouvement d'ajustement par ecart et fige la fiche."""
        require_permission(self._authz, actor, "stock.inventory")
        posted: list[PostedMovement] = []
        variances: list[InventoryVariance] = []
        with self._uow_factory() as uow:
            count = uow.inventories.get(normalize_code(count_id, "L'identifiant de la fiche"))
            if count is None:
                raise StockNotFoundError(f"Fiche d'inventaire '{count_id}' introuvable.")
            if count.status != InventoryStatus.DRAFT:
                raise StockConflictError(
                    f"Impossible de valider une fiche d'inventaire en etat {count.status.value}."
                )
            warehouse = load_active_warehouse(uow, count.warehouse_code)
            for line in sorted(count.counted_lines(), key=lambda ln: ln.item_code):
                assert line.counted_quantity is not None
                item = load_active_item(uow, line.item_code)
                current = uow.levels.get(item.code, warehouse.code)
                theoretical = current.quantity if current is not None else ZERO
                result = self._ledger.adjust(
                    uow,
                    item,
                    warehouse.code,
                    count.count_date,
                    line.counted_quantity,
                    line.unit_cost,
                    MovementOrigin(
                        SOURCE_INVENTORY,
                        count.id,
                        item.code,
                        f"Inventaire {count.id}",
                        actor.username,
                    ),
                )
                if result is not None:
                    posted.append(result)
                variances.append(
                    InventoryVariance(
                        item_code=item.code,
                        theoretical_quantity=theoretical,
                        counted_quantity=line.counted_quantity,
                        quantity_delta=result.movement.quantity_delta if result else ZERO,
                        value_delta=result.movement.value_delta if result else ZERO,
                    )
                )
            raw = uow.sequences.next_number("inventory", count.count_date.year)
            number = format_number(
                self._settings.sequence("inventory"), count.count_date.year, raw
            )
            count.mark_validated(number)
            uow.inventories.add(count)
        movements = emit_posted(self._publisher, posted, count.number or count.id)
        for movement in movements:
            record_audit(
                self._audit,
                actor.username,
                "ADJUST",
                "stock_movement",
                movement.number,
                after={
                    "item_code": movement.item_code,
                    "quantity_delta": str(movement.quantity_delta),
                    "value_delta": str(movement.value_delta),
                },
                clock=self._clock,
            )
        record_audit(
            self._audit,
            actor.username,
            "VALIDATE",
            "inventory_count",
            count.id,
            before={"status": "DRAFT"},
            after={"status": count.status.value, "number": count.number},
            clock=self._clock,
        )
        if self._publisher is not None:
            self._publisher.publish_event(
                event_type=EVENT_INVENTORY_VALIDATED,
                payload={
                    "inventory_id": count.id,
                    "number": count.number,
                    "warehouse_code": count.warehouse_code,
                    "count_date": count.count_date.isoformat(),
                    "variances": [
                        {
                            "item_code": v.item_code,
                            "theoretical_quantity": str(v.theoretical_quantity),
                            "counted_quantity": str(v.counted_quantity),
                            "quantity_delta": str(v.quantity_delta),
                            "value_delta": str(v.value_delta),
                        }
                        for v in variances
                    ],
                },
                correlation_id=count.number,
            )
        return InventoryResult(count=count, variances=variances, movements=movements)

    def cancel_count(self, actor: User, count_id: str) -> InventoryCount:
        require_permission(self._authz, actor, "stock.inventory")
        with self._uow_factory() as uow:
            count = uow.inventories.get(normalize_code(count_id, "L'identifiant de la fiche"))
            if count is None:
                raise StockNotFoundError(f"Fiche d'inventaire '{count_id}' introuvable.")
            count.cancel()
            uow.inventories.add(count)
        record_audit(
            self._audit,
            actor.username,
            "CANCEL",
            "inventory_count",
            count.id,
            before={"status": "DRAFT"},
            after={"status": count.status.value},
            clock=self._clock,
        )
        return count
