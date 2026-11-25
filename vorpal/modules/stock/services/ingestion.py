"""Ingestion des evenements Achats : receptions validees et frais d'approche.

Aucun import du module Achats : on lit uniquement la charge utile des evenements publies
(`achats.goods_receipt.validated`, `achats.landed_cost.allocated`). Le traitement est idempotent :
rejouer le meme evenement (replay du bus, republication) ne cree jamais un second mouvement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from vorpal.core.audit import AuditLog
from vorpal.core.money import Currency
from vorpal.modules.stock.domain.errors import StockNotFoundError, StockValidationError
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.movements import (
    SOURCE_GOODS_RECEIPT,
    SOURCE_LANDED_COST,
    MovementType,
    StockMovement,
)
from vorpal.modules.stock.domain.values import ZERO, ensure_unit_cost, normalize_code
from vorpal.modules.stock.domain.warehouses import Warehouse
from vorpal.modules.stock.manifest import EVENT_LANDED_COST_NOT_CAPITALIZED
from vorpal.modules.stock.ports.publisher import StockEventPublisher
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import SYSTEM_ACTOR, record_audit
from vorpal.modules.stock.services.ledger import (
    MovementOrigin,
    PostedMovement,
    StockLedger,
    emit_posted,
)
from vorpal.modules.stock.settings import StockSettings

_AUDIT_USER = "system:achats"


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StockValidationError(f"Evenement Achats invalide : champ '{key}' absent ou vide.")
    return value.strip()


def _rows(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise StockValidationError(f"Evenement Achats invalide : '{key}' doit etre une liste non vide.")
    rows: list[Mapping[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise StockValidationError(f"Evenement Achats invalide : ligne de '{key}' mal formee.")
        rows.append(row)
    return rows


def _decimal(row: Mapping[str, Any], key: str) -> Decimal:
    raw = row.get(key)
    if isinstance(raw, bool) or not isinstance(raw, str | int | Decimal):
        raise StockValidationError(f"Evenement Achats invalide : '{key}' absent ou de type invalide.")
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise StockValidationError(f"Evenement Achats invalide : '{key}' n'est pas un nombre.") from exc


def _iso_date(payload: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(_text(payload, key)[:10])
    except ValueError as exc:
        raise StockValidationError(f"Evenement Achats invalide : '{key}' n'est pas une date ISO.") from exc


def _currency(payload: Mapping[str, Any]) -> Currency:
    try:
        return Currency(_text(payload, "currency"))
    except ValueError as exc:
        raise StockValidationError("Evenement Achats invalide : devise inconnue.") from exc


class PurchaseIngestionService:
    """Transforme les evenements Achats en mouvements de stock (acteur systeme, sans permission)."""

    def __init__(
        self,
        uow_factory: Callable[[], StockUnitOfWork],
        settings: StockSettings,
        audit: AuditLog,
        ledger: StockLedger,
        publisher: StockEventPublisher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._ledger = ledger
        self._publisher = publisher
        self._clock = clock

    def _item_for(self, uow: StockUnitOfWork, code: str) -> StockItem:
        key = normalize_code(code, "Le code article")
        item = uow.items.get(key)
        if item is None:
            if not self._settings.auto_create_missing_masters:
                raise StockNotFoundError(
                    f"Article '{key}' inconnu du stock : creez-le puis rejouez l'evenement."
                )
            item = StockItem(
                code=key,
                name=key,
                unit=self._settings.default_unit,
                currency=self._settings.default_currency,
                quantity_decimals=self._settings.default_quantity_decimals,
            )
            uow.items.add(item)
        item.ensure_active()
        return item

    def _warehouse_for(self, uow: StockUnitOfWork, code: str) -> Warehouse:
        key = normalize_code(code, "Le code entrepot")
        warehouse = uow.warehouses.get(key)
        if warehouse is None:
            if not self._settings.auto_create_missing_masters:
                raise StockNotFoundError(
                    f"Entrepot '{key}' inconnu du stock : creez-le puis rejouez l'evenement."
                )
            warehouse = Warehouse(code=key, name=key)
            uow.warehouses.add(warehouse)
        warehouse.ensure_active()
        return warehouse

    def ingest_goods_receipt(self, payload: Mapping[str, Any]) -> list[StockMovement]:
        """Entre en stock les lignes d'une reception validee, au cout unitaire HT de la ligne."""
        receipt_id = _text(payload, "goods_receipt_id")
        receipt_date = _iso_date(payload, "receipt_date")
        currency = _currency(payload)
        rows = _rows(payload, "lines")
        posted: list[PostedMovement] = []
        with self._uow_factory() as uow:
            warehouse = self._warehouse_for(uow, _text(payload, "warehouse_code"))
            for row in sorted(rows, key=lambda r: _text(r, "line_id")):
                item = self._item_for(uow, _text(row, "item_code"))
                if item.currency != currency:
                    raise StockValidationError(
                        f"Reception {receipt_id} en {currency.value} mais l'article "
                        f"'{item.code}' est valorise en {item.currency.value}."
                    )
                line_id = _text(row, "line_id")
                posted.append(
                    self._ledger.receive(
                        uow,
                        item,
                        warehouse.code,
                        receipt_date,
                        item.quantity(_decimal(row, "quantity_received"), "La quantite recue"),
                        ensure_unit_cost(_decimal(row, "unit_cost_ht")),
                        MovementOrigin(
                            SOURCE_GOODS_RECEIPT,
                            receipt_id,
                            line_id,
                            f"Reception {payload.get('number', receipt_id)}",
                            SYSTEM_ACTOR,
                        ),
                        skip_duplicates=True,
                    )
                )
        movements = emit_posted(self._publisher, posted, receipt_id)
        for movement in movements:
            record_audit(
                self._audit,
                _AUDIT_USER,
                "RECEIVE",
                "stock_movement",
                movement.number,
                after={
                    "item_code": movement.item_code,
                    "warehouse_code": movement.warehouse_code,
                    "quantity_delta": str(movement.quantity_delta),
                    "source_ref": movement.source_ref,
                },
                clock=self._clock,
            )
        return [p.movement for p in posted]

    def ingest_landed_cost(self, payload: Mapping[str, Any]) -> list[StockMovement]:
        """Capitalise des frais d'approche sur le stock restant des lignes de reception visees.

        Chaque document est un ajout cumulatif. Si la reception n'est pas encore entree en stock,
        `StockNotFoundError` est levee : le bus reessaiera au rejeu de l'evenement.
        """
        landed_cost_id = _text(payload, "landed_cost_id")
        receipt_id = _text(payload, "goods_receipt_id")
        allocated_on = _iso_date(payload, "allocated_at")
        currency = _currency(payload)
        rows = _rows(payload, "allocations")
        posted: list[PostedMovement] = []
        with self._uow_factory() as uow:
            for row in sorted(rows, key=lambda r: _text(r, "receipt_line_id")):
                line_id = _text(row, "receipt_line_id")
                amount = _decimal(row, "allocated_amount")
                if amount < ZERO:
                    raise StockValidationError("Un frais d'approche alloue ne peut pas etre negatif.")
                origin_receipt = uow.movements.find_by_key(
                    SOURCE_GOODS_RECEIPT, receipt_id, line_id, MovementType.RECEIPT
                )
                if origin_receipt is None:
                    raise StockNotFoundError(
                        f"La reception '{receipt_id}' (ligne '{line_id}') n'est pas encore en stock."
                    )
                item = self._item_for(uow, origin_receipt.item_code)
                if item.currency != currency:
                    raise StockValidationError(
                        f"Frais en {currency.value} mais l'article '{item.code}' est valorise "
                        f"en {item.currency.value}."
                    )
                if amount == ZERO:
                    continue
                posted.append(
                    self._ledger.revalue(
                        uow,
                        item,
                        origin_receipt.warehouse_code,
                        allocated_on,
                        amount,
                        MovementOrigin(
                            SOURCE_LANDED_COST,
                            landed_cost_id,
                            line_id,
                            f"Frais d'approche {landed_cost_id}",
                            SYSTEM_ACTOR,
                        ),
                    )
                )
        movements = emit_posted(self._publisher, posted, landed_cost_id)
        for p in posted:
            if p.created and not p.capitalized and self._publisher is not None:
                self._publisher.publish_event(
                    event_type=EVENT_LANDED_COST_NOT_CAPITALIZED,
                    payload={
                        "landed_cost_id": landed_cost_id,
                        "goods_receipt_id": receipt_id,
                        "item_code": p.movement.item_code,
                        "warehouse_code": p.movement.warehouse_code,
                        "movement_number": p.movement.number,
                        "reason": p.movement.reason,
                    },
                    correlation_id=landed_cost_id,
                )
        for movement in movements:
            record_audit(
                self._audit,
                _AUDIT_USER,
                "REVALUE",
                "stock_movement",
                movement.number,
                after={
                    "item_code": movement.item_code,
                    "value_delta": str(movement.value_delta),
                    "source_ref": movement.source_ref,
                },
                clock=self._clock,
            )
        return [p.movement for p in posted]
