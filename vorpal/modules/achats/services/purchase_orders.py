"""Service applicatif de gestion des bons de commande fournisseurs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from vorpal.core.audit import AuditLog
from vorpal.core.money import Currency, Money
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseNotFoundError
from vorpal.modules.achats.domain.purchase_orders import PurchaseOrder, PurchaseOrderLine
from vorpal.modules.achats.domain.suppliers import SupplierTaxIdValidator
from vorpal.modules.achats.manifest import ACHATS_EVENTS_EMITTED
from vorpal.modules.achats.ports.publisher import PurchaseEventPublisher
from vorpal.modules.achats.ports.unit_of_work import PurchaseUnitOfWork
from vorpal.modules.achats.services.common import (
    current_timestamp,
    record_audit,
    require_permission,
)
from vorpal.modules.achats.settings import PurchaseSettings


class PurchaseOrderService:
    """Orchestre la creation, l'ajout de lignes, la confirmation et l'annulation des bons de commande."""

    def __init__(
        self,
        uow_factory: Callable[[], PurchaseUnitOfWork],
        settings: PurchaseSettings,
        audit: AuditLog,
        authz: AuthorizationService,
        publisher: PurchaseEventPublisher | None = None,
        clock: Callable[[], datetime] | None = None,
        tax_id_validator: SupplierTaxIdValidator | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._authz = authz
        self._publisher = publisher
        self._clock = clock
        self._tax_id_validator = tax_id_validator

    def create_order(
        self,
        actor: User,
        order_id: str,
        supplier_id: str,
        order_date: date,
        currency: Currency | None = None,
        lines: list[PurchaseOrderLine] | None = None,
        notes: str = "",
    ) -> PurchaseOrder:
        require_permission(self._authz, actor, "achats.create")

        with self._uow_factory() as uow:
            if uow.purchase_orders.get(order_id) is not None:
                raise PurchaseConflictError(f"Un bon de commande '{order_id}' existe deja.")
            supplier = uow.suppliers.get(supplier_id)
            if supplier is None:
                raise PurchaseNotFoundError(f"Fournisseur '{supplier_id}' introuvable.")

            order_currency = currency if currency is not None else supplier.preferred_currency
            order = PurchaseOrder(
                id=order_id,
                supplier_id=supplier_id,
                order_date=order_date,
                currency=order_currency,
                lines=list(lines) if lines else [],
                notes=notes,
            )
            uow.purchase_orders.add(order)

        record_audit(
            self._audit,
            actor=actor,
            action="CREATE",
            entity="purchase_order",
            entity_id=order.id,
            before=None,
            after={
                "supplier_id": order.supplier_id,
                "currency": order.currency.value,
                "status": order.status.value,
            },
            clock=self._clock,
        )
        return order

    def add_line(
        self,
        actor: User,
        order_id: str,
        line_id: str,
        item_code: str,
        designation: str,
        quantity_ordered: Decimal | int | str,
        unit_price_ht: Money,
        vat_rate: Decimal | int | str | None = None,
        discount_rate: Decimal | int | str = Decimal("0"),
    ) -> PurchaseOrder:
        require_permission(self._authz, actor, "achats.create")

        with self._uow_factory() as uow:
            order = uow.purchase_orders.get(order_id)
            if order is None:
                raise PurchaseNotFoundError(f"Bon de commande '{order_id}' introuvable.")

            resolved_vat = vat_rate if vat_rate is not None else self._settings.default_vat_rate
            line = PurchaseOrderLine(
                line_id=line_id,
                item_code=item_code,
                designation=designation,
                quantity_ordered=Decimal(str(quantity_ordered))
                if not isinstance(quantity_ordered, Decimal)
                else quantity_ordered,
                unit_price_ht=unit_price_ht,
                vat_rate=Decimal(str(resolved_vat))
                if not isinstance(resolved_vat, Decimal)
                else resolved_vat,
                discount_rate=Decimal(str(discount_rate))
                if not isinstance(discount_rate, Decimal)
                else discount_rate,
            )
            order.add_line(line)
            uow.purchase_orders.add(order)

        record_audit(
            self._audit,
            actor=actor,
            action="ADD_LINE",
            entity="purchase_order",
            entity_id=order.id,
            before=None,
            after={"line_id": line.line_id, "item_code": line.item_code},
            clock=self._clock,
        )
        return order

    def confirm_order(self, actor: User, order_id: str) -> PurchaseOrder:
        require_permission(self._authz, actor, "achats.approve")

        with self._uow_factory() as uow:
            order = uow.purchase_orders.get(order_id)
            if order is None:
                raise PurchaseNotFoundError(f"Bon de commande '{order_id}' introuvable.")

            supplier = uow.suppliers.get(order.supplier_id)
            if supplier is None:
                raise PurchaseNotFoundError(f"Fournisseur '{order.supplier_id}' introuvable.")
            supplier.ensure_can_order(self._tax_id_validator)

            before_status = order.status.value
            seq_cfg = self._settings.sequences.get("purchase_order")
            prefix = seq_cfg.prefix if seq_cfg is not None else "BC"
            sep = seq_cfg.year_separator if seq_cfg is not None else "-"
            padding = seq_cfg.padding if seq_cfg is not None else 5

            raw_seq = uow.sequences.next_number("purchase_order", order.order_date.year)
            assigned_number = f"{prefix}{sep}{order.order_date.year}{sep}{raw_seq.zfill(padding)}"
            order.confirm(assigned_number)
            uow.purchase_orders.add(order)

            totals = order.totals()

        record_audit(
            self._audit,
            actor=actor,
            action="CONFIRM",
            entity="purchase_order",
            entity_id=order.id,
            before={"status": before_status},
            after={
                "status": order.status.value,
                "number": order.number,
                "total_ttc": str(totals.total_ttc.amount),
                "currency": order.currency.value,
            },
            clock=self._clock,
        )

        if self._publisher is not None and order.number is not None:
            self._publisher.publish_event(
                event_type=ACHATS_EVENTS_EMITTED[0],
                payload={
                    "purchase_order_id": order.id,
                    "number": order.number,
                    "supplier_id": order.supplier_id,
                    "order_date": order.order_date.isoformat(),
                    "currency": order.currency.value,
                    "total_ht": str(totals.total_ht.amount),
                    "total_vat": str(totals.total_vat.amount),
                    "total_ttc": str(totals.total_ttc.amount),
                    "confirmed_at": current_timestamp(self._clock).isoformat(),
                },
                correlation_id=order.number,
            )

        return order

    def cancel_order(self, actor: User, order_id: str) -> PurchaseOrder:
        require_permission(self._authz, actor, "achats.approve")

        with self._uow_factory() as uow:
            order = uow.purchase_orders.get(order_id)
            if order is None:
                raise PurchaseNotFoundError(f"Bon de commande '{order_id}' introuvable.")

            before_status = order.status.value
            order.cancel()
            uow.purchase_orders.add(order)

        record_audit(
            self._audit,
            actor=actor,
            action="CANCEL",
            entity="purchase_order",
            entity_id=order.id,
            before={"status": before_status},
            after={"status": order.status.value},
            clock=self._clock,
        )
        return order
