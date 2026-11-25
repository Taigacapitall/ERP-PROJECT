"""Service applicatif des mouvements manuels : entree, sortie, transfert."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TypeVar

from vorpal.core.audit import AuditLog
from vorpal.core.money import Money
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.stock.domain.errors import StockValidationError
from vorpal.modules.stock.domain.movements import (
    RESERVED_SOURCE_TYPES,
    SOURCE_MANUAL,
    StockMovement,
)
from vorpal.modules.stock.domain.values import ensure_unit_cost, normalize_code
from vorpal.modules.stock.ports.publisher import StockEventPublisher
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import (
    load_active_item,
    load_active_warehouse,
    movement_payload,
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

_SOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class ReceiptLine:
    """Ligne d'entree : quantite et cout unitaire HT dans la devise de l'article."""

    item_code: str
    quantity: Decimal | int | str
    unit_cost: Money


@dataclass(frozen=True)
class QuantityLine:
    """Ligne de sortie ou de transfert."""

    item_code: str
    quantity: Decimal | int | str


TLine = TypeVar("TLine", ReceiptLine, QuantityLine)


class MovementService:
    """Entrees, sorties et transferts saisis par un utilisateur (permission `stock.create`).

    Les mouvements sont traites par ordre croissant de code article (verrouillage deterministe) ;
    le resultat est retourne dans cet ordre. Une erreur sur une ligne annule toute l'operation.
    """

    def __init__(
        self,
        uow_factory: Callable[[], StockUnitOfWork],
        settings: StockSettings,
        audit: AuditLog,
        authz: AuthorizationService,
        ledger: StockLedger,
        id_factory: Callable[[], str],
        publisher: StockEventPublisher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._authz = authz
        self._ledger = ledger
        self._id_factory = id_factory
        self._publisher = publisher
        self._clock = clock

    # -- aides ------------------------------------------------------------------------------

    def _origin_base(self, source_type: str, reference: str) -> tuple[str, str]:
        if not _SOURCE_TYPE_RE.match(source_type):
            raise StockValidationError(
                "Le type de source doit etre en minuscules (lettres, chiffres, '_'), 32 caracteres max."
            )
        if source_type in RESERVED_SOURCE_TYPES:
            raise StockValidationError(f"Le type de source '{source_type}' est reserve.")
        ref = reference.strip()
        return source_type, ref if ref else self._id_factory()

    @staticmethod
    def _sorted_unique(lines: Sequence[TLine]) -> list[TLine]:
        if not lines:
            raise StockValidationError("Au moins une ligne est requise.")
        codes = [normalize_code(line.item_code, "Le code article") for line in lines]
        if len(codes) != len(set(codes)):
            raise StockValidationError("Un article ne peut apparaitre qu'une fois par operation.")
        return sorted(lines, key=lambda line: line.item_code.strip())

    def _finish(
        self,
        actor: User,
        action: str,
        posted: list[PostedMovement],
        correlation_id: str,
    ) -> list[StockMovement]:
        movements = emit_posted(self._publisher, posted, correlation_id)
        for movement in movements:
            record_audit(
                self._audit,
                actor.username,
                action,
                "stock_movement",
                movement.number,
                after=dict(movement_payload(movement)),
                clock=self._clock,
            )
        return movements

    # -- operations -------------------------------------------------------------------------

    def receive_stock(
        self,
        actor: User,
        warehouse_code: str,
        movement_date: date,
        lines: Sequence[ReceiptLine],
        reference: str = "",
        reason: str = "",
        source_type: str = SOURCE_MANUAL,
    ) -> list[StockMovement]:
        """Entree manuelle (stock initial, retour, don...) ; une reference deja utilisee est refusee."""
        require_permission(self._authz, actor, "stock.create")
        source, ref = self._origin_base(source_type, reference)
        ordered = self._sorted_unique(lines)
        posted: list[PostedMovement] = []
        with self._uow_factory() as uow:
            warehouse = load_active_warehouse(uow, warehouse_code)
            for raw in ordered:
                item = load_active_item(uow, raw.item_code)
                if raw.unit_cost.currency != item.currency:
                    raise StockValidationError(
                        f"Le cout de '{item.code}' doit etre en {item.currency.value}."
                    )
                posted.append(
                    self._ledger.receive(
                        uow,
                        item,
                        warehouse.code,
                        movement_date,
                        item.quantity(raw.quantity),
                        ensure_unit_cost(raw.unit_cost.amount),
                        MovementOrigin(source, ref, item.code, reason, actor.username),
                    )
                )
        return self._finish(actor, "RECEIVE", posted, ref)

    def issue_stock(
        self,
        actor: User,
        warehouse_code: str,
        movement_date: date,
        lines: Sequence[QuantityLine],
        reference: str = "",
        reason: str = "",
        source_type: str = SOURCE_MANUAL,
    ) -> list[StockMovement]:
        """Sortie au cout moyen ; refusee si le stock est insuffisant (pas de stock negatif)."""
        require_permission(self._authz, actor, "stock.create")
        source, ref = self._origin_base(source_type, reference)
        ordered = self._sorted_unique(lines)
        posted: list[PostedMovement] = []
        with self._uow_factory() as uow:
            warehouse = load_active_warehouse(uow, warehouse_code)
            for raw in ordered:
                item = load_active_item(uow, raw.item_code)
                posted.append(
                    self._ledger.issue(
                        uow,
                        item,
                        warehouse.code,
                        movement_date,
                        item.quantity(raw.quantity),
                        MovementOrigin(source, ref, item.code, reason, actor.username),
                    )
                )
        return self._finish(actor, "ISSUE", posted, ref)

    def transfer_stock(
        self,
        actor: User,
        from_warehouse: str,
        to_warehouse: str,
        movement_date: date,
        lines: Sequence[QuantityLine],
        reference: str = "",
        reason: str = "",
        source_type: str = SOURCE_MANUAL,
    ) -> list[StockMovement]:
        """Transfert entre deux entrepots actifs ; la valeur est conservee a l'identique."""
        require_permission(self._authz, actor, "stock.create")
        source, ref = self._origin_base(source_type, reference)
        ordered = self._sorted_unique(lines)
        posted: list[PostedMovement] = []
        with self._uow_factory() as uow:
            origin_wh = load_active_warehouse(uow, from_warehouse)
            target_wh = load_active_warehouse(uow, to_warehouse)
            if origin_wh.code == target_wh.code:
                raise StockValidationError("Les entrepots source et destination doivent differer.")
            for raw in ordered:
                item = load_active_item(uow, raw.item_code)
                posted.extend(
                    self._ledger.transfer(
                        uow,
                        item,
                        origin_wh.code,
                        target_wh.code,
                        movement_date,
                        item.quantity(raw.quantity),
                        MovementOrigin(source, ref, item.code, reason, actor.username),
                    )
                )
        return self._finish(actor, "TRANSFER", posted, ref)
