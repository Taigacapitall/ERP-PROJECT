"""Mappers explicites entre objets du domaine Achats et modeles ORM SQLAlchemy."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from vorpal.core.money import Currency, Money
from vorpal.core.partners import Partner
from vorpal.modules.achats.adapters.persistence.models import (
    GoodsReceiptLineModel,
    GoodsReceiptModel,
    LandedCostModel,
    PurchaseOrderLineModel,
    PurchaseOrderModel,
    SupplierModel,
)
from vorpal.modules.achats.domain.landed_costs import (
    LandedCostAllocationLine,
    LandedCostComponent,
    LandedCostDocument,
)
from vorpal.modules.achats.domain.purchase_orders import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from vorpal.modules.achats.domain.receipts import (
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
)
from vorpal.modules.achats.domain.suppliers import Supplier, SupplierContact
from vorpal.modules.achats.settings import LandedCostAllocationMethod


def supplier_to_model(supplier: Supplier) -> SupplierModel:
    contacts_data = [
        {
            "name": c.name,
            "role": c.role,
            "email": c.email,
            "phone": c.phone,
        }
        for c in supplier.contacts
    ]
    return SupplierModel(
        id=supplier.id,
        name=supplier.partner.name,
        nif=supplier.partner.nif,
        rccm=supplier.partner.rccm,
        address=supplier.partner.address,
        payment_terms_days=supplier.payment_terms_days,
        preferred_currency=supplier.preferred_currency,
        contacts_json=json.dumps(contacts_data, ensure_ascii=True),
        is_active=supplier.is_active,
    )


def supplier_from_model(model: SupplierModel) -> Supplier:
    raw_contacts: list[dict[str, Any]] = (
        json.loads(model.contacts_json) if model.contacts_json else []
    )
    contacts = [
        SupplierContact(
            name=str(item.get("name", "")),
            role=str(item.get("role", "")),
            email=str(item.get("email", "")),
            phone=str(item.get("phone", "")),
        )
        for item in raw_contacts
    ]
    partner = Partner(
        id=model.id,
        name=model.name,
        partner_type="fournisseur",
        nif=model.nif,
        rccm=model.rccm,
        address=model.address,
    )
    return Supplier(
        id=model.id,
        partner=partner,
        payment_terms_days=model.payment_terms_days,
        preferred_currency=Currency(model.preferred_currency),
        contacts=contacts,
        is_active=model.is_active,
    )


def order_to_model(order: PurchaseOrder) -> tuple[PurchaseOrderModel, list[PurchaseOrderLineModel]]:
    header = PurchaseOrderModel(
        id=order.id,
        number=order.number,
        supplier_id=order.supplier_id,
        order_date=order.order_date,
        currency=order.currency,
        status=order.status,
        notes=order.notes,
    )
    lines = [
        PurchaseOrderLineModel(
            line_id=line.line_id,
            purchase_order_id=order.id,
            item_code=line.item_code,
            designation=line.designation,
            quantity_ordered=Decimal(str(line.quantity_ordered)),
            unit_price_ht_amount=Decimal(str(line.unit_price_ht.amount)),
            unit_price_ht_currency=line.unit_price_ht.currency,
            vat_rate=Decimal(str(line.vat_rate)),
            discount_rate=Decimal(str(line.discount_rate)),
            quantity_received=Decimal(str(line.quantity_received)),
        )
        for line in order.lines
    ]
    return header, lines


def order_from_model(
    header: PurchaseOrderModel, line_models: list[PurchaseOrderLineModel]
) -> PurchaseOrder:
    lines = [
        PurchaseOrderLine(
            line_id=lm.line_id,
            item_code=lm.item_code,
            designation=lm.designation,
            quantity_ordered=Decimal(str(lm.quantity_ordered)),
            unit_price_ht=Money(
                Decimal(str(lm.unit_price_ht_amount)), Currency(lm.unit_price_ht_currency)
            ),
            vat_rate=Decimal(str(lm.vat_rate)),
            discount_rate=Decimal(str(lm.discount_rate)),
            quantity_received=Decimal(str(lm.quantity_received)),
        )
        for lm in line_models
    ]
    return PurchaseOrder(
        id=header.id,
        supplier_id=header.supplier_id,
        order_date=header.order_date,
        currency=Currency(header.currency),
        lines=lines,
        status=PurchaseOrderStatus(header.status),
        number=header.number,
        notes=header.notes,
    )


def receipt_to_model(
    receipt: GoodsReceipt,
) -> tuple[GoodsReceiptModel, list[GoodsReceiptLineModel]]:
    header = GoodsReceiptModel(
        id=receipt.id,
        number=receipt.number,
        purchase_order_id=receipt.purchase_order_id,
        supplier_id=receipt.supplier_id,
        receipt_date=receipt.receipt_date,
        currency=receipt.currency,
        warehouse_code=receipt.warehouse_code,
        status=receipt.status,
    )
    lines = [
        GoodsReceiptLineModel(
            line_id=line.line_id,
            goods_receipt_id=receipt.id,
            order_line_id=line.order_line_id,
            item_code=line.item_code,
            designation=line.designation,
            quantity_received=Decimal(str(line.quantity_received)),
            unit_cost_ht_amount=Decimal(str(line.unit_cost_ht.amount)),
            unit_cost_ht_currency=line.unit_cost_ht.currency,
            landed_cost_allocated_amount=Decimal(
                str(
                    (
                        line.landed_cost_allocated
                        or Money(Decimal("0.00"), line.unit_cost_ht.currency)
                    ).amount
                )
            ),
            landed_cost_allocated_currency=line.unit_cost_ht.currency,
        )
        for line in receipt.lines
    ]
    return header, lines


def receipt_from_model(
    header: GoodsReceiptModel, line_models: list[GoodsReceiptLineModel]
) -> GoodsReceipt:
    lines = [
        GoodsReceiptLine(
            line_id=lm.line_id,
            order_line_id=lm.order_line_id,
            item_code=lm.item_code,
            designation=lm.designation,
            quantity_received=Decimal(str(lm.quantity_received)),
            unit_cost_ht=Money(
                Decimal(str(lm.unit_cost_ht_amount)), Currency(lm.unit_cost_ht_currency)
            ),
            landed_cost_allocated=Money(
                Decimal(str(lm.landed_cost_allocated_amount)),
                Currency(lm.landed_cost_allocated_currency),
            ),
        )
        for lm in line_models
    ]
    return GoodsReceipt(
        id=header.id,
        purchase_order_id=header.purchase_order_id,
        supplier_id=header.supplier_id,
        receipt_date=header.receipt_date,
        currency=Currency(header.currency),
        warehouse_code=header.warehouse_code,
        lines=lines,
        status=GoodsReceiptStatus(header.status),
        number=header.number,
    )


def landed_cost_to_model(doc: LandedCostDocument) -> LandedCostModel:
    comp_data = [
        {
            "category": c.category,
            "label": c.label,
            "amount": str(c.amount.amount),
            "currency": c.amount.currency.value,
        }
        for c in doc.components
    ]
    alloc_data = [
        {
            "receipt_line_id": a.receipt_line_id,
            "item_code": a.item_code,
            "allocated_amount": str(a.allocated_amount.amount),
            "currency": a.allocated_amount.currency.value,
        }
        for a in doc.allocations
    ]
    return LandedCostModel(
        id=doc.id,
        goods_receipt_id=doc.goods_receipt_id,
        allocation_date=doc.allocation_date,
        currency=doc.currency,
        method=doc.method,
        components_json=json.dumps(comp_data, ensure_ascii=True),
        allocations_json=json.dumps(alloc_data, ensure_ascii=True),
    )


def landed_cost_from_model(model: LandedCostModel) -> LandedCostDocument:
    raw_comp: list[dict[str, Any]] = (
        json.loads(model.components_json) if model.components_json else []
    )
    raw_alloc: list[dict[str, Any]] = (
        json.loads(model.allocations_json) if model.allocations_json else []
    )

    components = [
        LandedCostComponent(
            category=str(item["category"]),
            label=str(item["label"]),
            amount=Money(Decimal(str(item["amount"])), Currency(str(item["currency"]))),
        )
        for item in raw_comp
    ]
    allocations = [
        LandedCostAllocationLine(
            receipt_line_id=str(item["receipt_line_id"]),
            item_code=str(item["item_code"]),
            allocated_amount=Money(
                Decimal(str(item["allocated_amount"])), Currency(str(item["currency"]))
            ),
        )
        for item in raw_alloc
    ]
    return LandedCostDocument(
        id=model.id,
        goods_receipt_id=model.goods_receipt_id,
        allocation_date=model.allocation_date,
        currency=Currency(model.currency),
        method=LandedCostAllocationMethod(model.method),
        components=components,
        allocations=allocations,
    )
