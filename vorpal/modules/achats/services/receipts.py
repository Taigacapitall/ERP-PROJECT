"""Service applicatif de reception des marchandises et liaison avec les commandes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from vorpal.core.audit import AuditLog
from vorpal.core.money import Currency, Money
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseNotFoundError
from vorpal.modules.achats.domain.receipts import GoodsReceipt, GoodsReceiptLine
from vorpal.modules.achats.manifest import ACHATS_EVENTS_EMITTED
from vorpal.modules.achats.ports.extensions import StockReceiptNotifier
from vorpal.modules.achats.ports.publisher import PurchaseEventPublisher
from vorpal.modules.achats.ports.unit_of_work import PurchaseUnitOfWork
from vorpal.modules.achats.services.common import (
    current_timestamp,
    record_audit,
    require_permission,
)
from vorpal.modules.achats.settings import PurchaseSettings


class GoodsReceiptService:
    """Orchestre la creation et la validation des bons de reception."""

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

    def create_receipt(
        self,
        actor: User,
        receipt_id: str,
        purchase_order_id: str,
        receipt_date: date,
        warehouse_code: str = "MAIN",
        currency: Currency | None = None,
        lines: list[GoodsReceiptLine] | None = None,
    ) -> GoodsReceipt:
        require_permission(self._authz, actor, "achats.receive")

        with self._uow_factory() as uow:
            if uow.goods_receipts.get(receipt_id) is not None:
                raise PurchaseConflictError(f"Un bon de reception '{receipt_id}' existe deja.")
            order = uow.purchase_orders.get(purchase_order_id)
            if order is None:
                raise PurchaseNotFoundError(f"Bon de commande '{purchase_order_id}' introuvable.")

            receipt_currency = currency if currency is not None else order.currency
            receipt = GoodsReceipt(
                id=receipt_id,
                purchase_order_id=purchase_order_id,
                supplier_id=order.supplier_id,
                receipt_date=receipt_date,
                currency=receipt_currency,
                warehouse_code=warehouse_code,
                lines=list(lines) if lines else [],
            )
            uow.goods_receipts.add(receipt)

        record_audit(
            self._audit,
            actor=actor,
            action="CREATE",
            entity="goods_receipt",
            entity_id=receipt.id,
            before=None,
            after={
                "purchase_order_id": receipt.purchase_order_id,
                "supplier_id": receipt.supplier_id,
                "warehouse_code": receipt.warehouse_code,
                "status": receipt.status.value,
            },
            clock=self._clock,
        )
        return receipt

    def add_line(
        self,
        actor: User,
        receipt_id: str,
        line_id: str,
        order_line_id: str,
        item_code: str,
        designation: str,
        quantity_received: Decimal | int | str,
        unit_cost_ht: Money,
    ) -> GoodsReceipt:
        require_permission(self._authz, actor, "achats.receive")

        with self._uow_factory() as uow:
            receipt = uow.goods_receipts.get(receipt_id)
            if receipt is None:
                raise PurchaseNotFoundError(f"Bon de reception '{receipt_id}' introuvable.")

            line = GoodsReceiptLine(
                line_id=line_id,
                order_line_id=order_line_id,
                item_code=item_code,
                designation=designation,
                quantity_received=Decimal(str(quantity_received))
                if not isinstance(quantity_received, Decimal)
                else quantity_received,
                unit_cost_ht=unit_cost_ht,
            )
            receipt.add_line(line)
            uow.goods_receipts.add(receipt)

        return receipt

    def validate_receipt(self, actor: User, receipt_id: str) -> GoodsReceipt:
        require_permission(self._authz, actor, "achats.receive")

        with self._uow_factory() as uow:
            receipt = uow.goods_receipts.get(receipt_id)
            if receipt is None:
                raise PurchaseNotFoundError(f"Bon de reception '{receipt_id}' introuvable.")
            order = uow.purchase_orders.get(receipt.purchase_order_id)
            if order is None:
                raise PurchaseNotFoundError(
                    f"Bon de commande '{receipt.purchase_order_id}' introuvable."
                )

            qty_by_order_line: dict[str, Decimal] = {}
            for r_line in receipt.lines:
                qty_by_order_line[r_line.order_line_id] = (
                    qty_by_order_line.get(r_line.order_line_id, Decimal("0"))
                    + r_line.quantity_received
                )

            order.apply_receipt_quantities(qty_by_order_line)

            seq_cfg = self._settings.sequences.get("goods_receipt")
            prefix = seq_cfg.prefix if seq_cfg is not None else "REC"
            sep = seq_cfg.year_separator if seq_cfg is not None else "-"
            padding = seq_cfg.padding if seq_cfg is not None else 5

            raw_seq = uow.sequences.next_number("goods_receipt", receipt.receipt_date.year)
            assigned_number = (
                f"{prefix}{sep}{receipt.receipt_date.year}{sep}{raw_seq.zfill(padding)}"
            )
            receipt.validate(assigned_number)

            uow.purchase_orders.add(order)
            uow.goods_receipts.add(receipt)

            event_payload = {
                "goods_receipt_id": receipt.id,
                "number": receipt.number,
                "purchase_order_id": receipt.purchase_order_id,
                "purchase_order_status": order.status.value,
                "supplier_id": receipt.supplier_id,
                "warehouse_code": receipt.warehouse_code,
                "receipt_date": receipt.receipt_date.isoformat(),
                "currency": receipt.currency.value,
                "total_base_ht": str(receipt.total_base_ht().amount),
                "lines": [
                    {
                        "line_id": ln.line_id,
                        "order_line_id": ln.order_line_id,
                        "item_code": ln.item_code,
                        "quantity_received": str(ln.quantity_received),
                        "unit_cost_ht": str(ln.unit_cost_ht.amount),
                        "base_line_ht": str(ln.base_line_ht.amount),
                    }
                    for ln in receipt.lines
                ],
                "validated_at": current_timestamp(self._clock).isoformat(),
            }

        record_audit(
            self._audit,
            actor=actor,
            action="VALIDATE",
            entity="goods_receipt",
            entity_id=receipt.id,
            before={"status": "DRAFT"},
            after={"status": receipt.status.value, "number": receipt.number},
            clock=self._clock,
        )

        if self._publisher is not None and receipt.number is not None:
            self._publisher.publish_event(
                event_type=ACHATS_EVENTS_EMITTED[1],
                payload=event_payload,
                correlation_id=receipt.number,
            )

        if self._stock_notifier is not None:
            self._stock_notifier.notify_receipt_validated(event_payload)

        return receipt
