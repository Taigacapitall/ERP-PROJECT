"""Grand livre interne : seul endroit ou un solde de stock est modifie.

Chaque methode travaille dans l'Unit of Work ouverte par l'appelant : elle verrouille le solde,
applique la regle de valorisation du domaine, ecrit le mouvement et le solde. Les evenements et
l'audit sont emis par les services APRES le commit, jamais ici.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from vorpal.modules.stock.domain.errors import StockConflictError
from vorpal.modules.stock.domain.items import StockItem
from vorpal.modules.stock.domain.levels import StockLevel
from vorpal.modules.stock.domain.movements import MovementType, StockMovement
from vorpal.modules.stock.domain.values import ZERO
from vorpal.modules.stock.manifest import EVENT_LEVEL_LOW
from vorpal.modules.stock.ports.publisher import StockEventPublisher
from vorpal.modules.stock.ports.unit_of_work import StockUnitOfWork
from vorpal.modules.stock.services.common import (
    SYSTEM_ACTOR,
    current_timestamp,
    format_number,
    publish_movements,
)
from vorpal.modules.stock.settings import StockSettings


@dataclass(frozen=True)
class MovementOrigin:
    """Origine d'un mouvement : fait source (cle d'idempotence), motif et auteur."""

    source_type: str
    source_ref: str
    source_line_ref: str = ""
    reason: str = ""
    actor: str = SYSTEM_ACTOR


@dataclass(frozen=True)
class LowStockAlert:
    """Le total d'un article vient de passer sous (ou sur) son seuil de reapprovisionnement."""

    item_code: str
    quantity: Decimal
    threshold: Decimal


@dataclass(frozen=True)
class PostedMovement:
    """Resultat d'une ecriture.

    `created` vaut False si le mouvement existait deja (rejeu). `capitalized` vaut False quand une
    revalorisation n'a pu etre rattachee a aucun stock (stock nul).
    """

    movement: StockMovement
    low_stock: LowStockAlert | None = None
    created: bool = True
    capitalized: bool = True


class StockLedger:
    """Ecrit les mouvements et met a jour les soldes, de maniere atomique avec l'UoW appelante."""

    def __init__(
        self,
        settings: StockSettings,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._id_factory = id_factory
        self._clock = clock

    # -- aides internes ---------------------------------------------------------------------

    def _existing(
        self,
        uow: StockUnitOfWork,
        origin: MovementOrigin,
        movement_type: MovementType,
        skip_duplicates: bool,
    ) -> StockMovement | None:
        found = uow.movements.find_by_key(
            origin.source_type, origin.source_ref, origin.source_line_ref, movement_type
        )
        if found is not None and not skip_duplicates:
            raise StockConflictError(
                f"Mouvement deja enregistre pour la source '{origin.source_type}' "
                f"'{origin.source_ref}' (ligne '{origin.source_line_ref}')."
            )
        return found

    def _lock(self, uow: StockUnitOfWork, item: StockItem, warehouse_code: str) -> StockLevel:
        level = uow.levels.lock_and_get(item.code, warehouse_code, item.currency)
        if level.currency != item.currency:
            raise StockConflictError(
                f"Le solde de '{item.code}' dans '{warehouse_code}' n'est pas dans la devise de l'article."
            )
        return level

    def _total_on_hand(self, uow: StockUnitOfWork, item_code: str) -> Decimal:
        return sum((lv.quantity for lv in uow.levels.list_by_item(item_code)), ZERO)

    def _alert(
        self, item: StockItem, before_total: Decimal, after_total: Decimal
    ) -> LowStockAlert | None:
        threshold = item.reorder_threshold
        if threshold is None:
            return None
        if before_total > threshold >= after_total:
            return LowStockAlert(item_code=item.code, quantity=after_total, threshold=threshold)
        return None

    def _write(
        self,
        uow: StockUnitOfWork,
        item: StockItem,
        level: StockLevel,
        movement_type: MovementType,
        movement_date: date,
        quantity_delta: Decimal,
        value_delta: Decimal,
        origin: MovementOrigin,
    ) -> StockMovement:
        """Enregistre le solde modifie et le mouvement correspondant."""
        raw = uow.sequences.next_number("movement", movement_date.year)
        number = format_number(self._settings.sequence("movement"), movement_date.year, raw)
        movement = StockMovement(
            id=self._id_factory(),
            number=number,
            movement_type=movement_type,
            item_code=item.code,
            warehouse_code=level.warehouse_code,
            movement_date=movement_date,
            quantity_delta=quantity_delta,
            value_delta=value_delta,
            currency=item.currency,
            quantity_after=level.quantity,
            value_after=level.value,
            source_type=origin.source_type,
            source_ref=origin.source_ref,
            created_at=current_timestamp(self._clock),
            source_line_ref=origin.source_line_ref,
            reason=origin.reason,
            actor=origin.actor,
        )
        uow.levels.save(level)
        uow.movements.add(movement)
        return movement

    # -- operations -------------------------------------------------------------------------

    def receive(
        self,
        uow: StockUnitOfWork,
        item: StockItem,
        warehouse_code: str,
        movement_date: date,
        quantity: Decimal,
        unit_cost: Decimal,
        origin: MovementOrigin,
        skip_duplicates: bool = False,
    ) -> PostedMovement:
        """Entree en stock au cout unitaire donne (cout moyen recalcule)."""
        existing = self._existing(uow, origin, MovementType.RECEIPT, skip_duplicates)
        if existing is not None:
            return PostedMovement(existing, created=False)
        level = self._lock(uow, item, warehouse_code)
        before_total = self._total_on_hand(uow, item.code)
        value_in = level.receive(quantity, unit_cost)
        movement = self._write(
            uow, item, level, MovementType.RECEIPT, movement_date, quantity, value_in, origin
        )
        return PostedMovement(movement, self._alert(item, before_total, before_total + quantity))

    def issue(
        self,
        uow: StockUnitOfWork,
        item: StockItem,
        warehouse_code: str,
        movement_date: date,
        quantity: Decimal,
        origin: MovementOrigin,
        skip_duplicates: bool = False,
    ) -> PostedMovement:
        """Sortie de stock au cout moyen ; refusee si le stock est insuffisant."""
        existing = self._existing(uow, origin, MovementType.ISSUE, skip_duplicates)
        if existing is not None:
            return PostedMovement(existing, created=False)
        level = self._lock(uow, item, warehouse_code)
        before_total = self._total_on_hand(uow, item.code)
        value_out = level.issue(quantity)
        movement = self._write(
            uow, item, level, MovementType.ISSUE, movement_date, -quantity, -value_out, origin
        )
        return PostedMovement(movement, self._alert(item, before_total, before_total - quantity))

    def transfer(
        self,
        uow: StockUnitOfWork,
        item: StockItem,
        from_warehouse: str,
        to_warehouse: str,
        movement_date: date,
        quantity: Decimal,
        origin: MovementOrigin,
    ) -> list[PostedMovement]:
        """Transfert entre entrepots : la valeur sortie au cout moyen est reprise a l'identique."""
        self._existing(uow, origin, MovementType.TRANSFER_OUT, skip_duplicates=False)
        self._existing(uow, origin, MovementType.TRANSFER_IN, skip_duplicates=False)
        # Verrouillage dans un ordre fixe (entrepot croissant) : pas d'interblocage entre transferts.
        levels: dict[str, StockLevel] = {}
        for code in sorted({from_warehouse, to_warehouse}):
            levels[code] = self._lock(uow, item, code)
        source, target = levels[from_warehouse], levels[to_warehouse]
        value_moved = source.issue(quantity)
        target.receive_value(quantity, value_moved)
        out = self._write(
            uow,
            item,
            source,
            MovementType.TRANSFER_OUT,
            movement_date,
            -quantity,
            -value_moved,
            origin,
        )
        into = self._write(
            uow,
            item,
            target,
            MovementType.TRANSFER_IN,
            movement_date,
            quantity,
            value_moved,
            origin,
        )
        return [PostedMovement(out), PostedMovement(into)]

    def adjust(
        self,
        uow: StockUnitOfWork,
        item: StockItem,
        warehouse_code: str,
        movement_date: date,
        counted_quantity: Decimal,
        unit_cost: Decimal | None,
        origin: MovementOrigin,
    ) -> PostedMovement | None:
        """Ramene le solde a la quantite comptee ; None si aucun ecart."""
        self._existing(uow, origin, MovementType.ADJUSTMENT, skip_duplicates=False)
        level = self._lock(uow, item, warehouse_code)
        before_total = self._total_on_hand(uow, item.code)
        variance, value_delta = level.adjust_to(counted_quantity, unit_cost)
        if variance == ZERO:
            return None
        movement = self._write(
            uow, item, level, MovementType.ADJUSTMENT, movement_date, variance, value_delta, origin
        )
        return PostedMovement(movement, self._alert(item, before_total, before_total + variance))

    def revalue(
        self,
        uow: StockUnitOfWork,
        item: StockItem,
        warehouse_code: str,
        movement_date: date,
        amount: Decimal,
        origin: MovementOrigin,
    ) -> PostedMovement:
        """Ajoute `amount` a la valeur du stock restant (idempotent : un rejeu est ignore).

        Si le stock est nul, rien n'est capitalise : un mouvement de valeur 0 est ecrit avec la mention
        NON_CAPITALISE dans le motif, pour que la perte de rattachement reste visible et auditable.
        """
        existing = self._existing(uow, origin, MovementType.REVALUATION, skip_duplicates=True)
        if existing is not None:
            return PostedMovement(existing, created=False)
        level = self._lock(uow, item, warehouse_code)
        if level.quantity <= ZERO:
            reason = f"NON_CAPITALISE: stock nul, montant {amount} {item.currency.value}"
            skipped = MovementOrigin(
                source_type=origin.source_type,
                source_ref=origin.source_ref,
                source_line_ref=origin.source_line_ref,
                reason=reason,
                actor=origin.actor,
            )
            movement = self._write(
                uow, item, level, MovementType.REVALUATION, movement_date, ZERO, ZERO, skipped
            )
            return PostedMovement(movement, capitalized=False)
        level.revalue(amount)
        movement = self._write(
            uow, item, level, MovementType.REVALUATION, movement_date, ZERO, amount, origin
        )
        return PostedMovement(movement)


def emit_posted(
    publisher: StockEventPublisher | None,
    posted: Iterable[PostedMovement],
    correlation_id: str,
) -> list[StockMovement]:
    """Publie, apres commit, les mouvements crees et les alertes de seuil ; retourne les crees."""
    created = [p for p in posted if p.created]
    publish_movements(publisher, (p.movement for p in created), correlation_id)
    if publisher is not None:
        last_alert: dict[str, LowStockAlert] = {}
        for p in created:
            if p.low_stock is not None:
                last_alert[p.low_stock.item_code] = p.low_stock
        for alert in last_alert.values():
            publisher.publish_event(
                event_type=EVENT_LEVEL_LOW,
                payload={
                    "item_code": alert.item_code,
                    "quantity": str(alert.quantity),
                    "threshold": str(alert.threshold),
                },
                correlation_id=correlation_id,
            )
    return [p.movement for p in created]
