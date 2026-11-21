"""Service applicatif de calcul et d'allocation des frais d'approche."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from vorpal.core.audit import AuditLog
from vorpal.core.money import Currency
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseNotFoundError
from vorpal.modules.achats.domain.landed_costs import (
    LandedCostComponent,
    LandedCostDocument,
    allocate_landed_costs,
)
from vorpal.modules.achats.manifest import ACHATS_EVENTS_EMITTED
from vorpal.modules.achats.ports.extensions import StockReceiptNotifier
from vorpal.modules.achats.ports.publisher import PurchaseEventPublisher
from vorpal.modules.achats.ports.unit_of_work import PurchaseUnitOfWork
from vorpal.modules.achats.services.common import (
    current_timestamp,
    record_audit,
    require_permission,
)
from vorpal.modules.achats.settings import LandedCostAllocationMethod, PurchaseSettings


class LandedCostService:
    """Orchestre l'enregistrement et la repartition des frais d'approche sur un bon de reception."""

    def __init__(
        self,
        uow_factory: Callable[[], PurchaseUnitOfWork],
        settings: PurchaseSettings,
        audit: AuditLog,
        authz: AuthorizationService,
        publisher: PurchaseEventPublisher | None = None,
        stock_notifier: StockReceiptNotifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._authz = authz
        self._publisher = publisher
        self._stock_notifier = stock_notifier
        self._clock = clock

    def allocate_for_receipt(
        self,
        actor: User,
        landed_cost_id: str,
        goods_receipt_id: str,
        allocation_date: date,
        components: list[LandedCostComponent],
        method: LandedCostAllocationMethod | None = None,
        currency: Currency | None = None,
    ) -> LandedCostDocument:
        require_permission(self._authz, actor, "achats.landed_cost")

        with self._uow_factory() as uow:
            if uow.landed_costs.get(landed_cost_id) is not None:
                raise PurchaseConflictError(
                    f"Un document de frais d'approche '{landed_cost_id}' existe deja."
                )
            receipt = uow.goods_receipts.get(goods_receipt_id)
            if receipt is None:
                raise PurchaseNotFoundError(f"Bon de reception '{goods_receipt_id}' introuvable.")

            resolved_method = (
                method if method is not None else self._settings.landed_cost_allocation_method
            )
            resolved_currency = currency if currency is not None else receipt.currency

            doc = LandedCostDocument(
                id=landed_cost_id,
                goods_receipt_id=goods_receipt_id,
                allocation_date=allocation_date,
                currency=resolved_currency,
                method=resolved_method,
                components=list(components),
            )
            allocate_landed_costs(doc, receipt)

            uow.goods_receipts.add(receipt)
            uow.landed_costs.add(doc)

            event_payload = {
                "landed_cost_id": doc.id,
                "goods_receipt_id": receipt.id,
                "goods_receipt_number": receipt.number,
                "method": doc.method.value,
                "currency": doc.currency.value,
                "total_landed_cost": str(doc.total_cost().amount),
                "allocations": [
                    {
                        "receipt_line_id": alloc.receipt_line_id,
                        "item_code": alloc.item_code,
                        "allocated_amount": str(alloc.allocated_amount.amount),
                    }
                    for alloc in doc.allocations
                ],
                "allocated_at": current_timestamp(self._clock).isoformat(),
            }

        record_audit(
            self._audit,
            actor=actor,
            action="ALLOCATE_LANDED_COST",
            entity="landed_cost",
            entity_id=doc.id,
            before=None,
            after={
                "goods_receipt_id": receipt.id,
                "method": doc.method.value,
                "total_landed_cost": str(doc.total_cost().amount),
            },
            clock=self._clock,
        )

        if self._publisher is not None:
            self._publisher.publish_event(
                event_type=ACHATS_EVENTS_EMITTED[2],
                payload=event_payload,
                correlation_id=receipt.number or receipt.id,
            )

        if self._stock_notifier is not None:
            self._stock_notifier.notify_landed_cost_allocated(event_payload)

        return doc
