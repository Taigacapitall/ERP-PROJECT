"""Tests unitaires du domaine Achats : totaux, fournisseurs, commandes, receptions et frais d'approche."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from vorpal.core.money import Currency, Money
from vorpal.core.partners import Partner
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseValidationError
from vorpal.modules.achats.domain.landed_costs import (
    LandedCostComponent,
    LandedCostDocument,
    allocate_landed_costs,
)
from vorpal.modules.achats.domain.purchase_orders import (
    PurchaseOrder,
    PurchaseOrderStatus,
)
from vorpal.modules.achats.domain.receipts import (
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
)
from vorpal.modules.achats.domain.suppliers import Supplier
from vorpal.modules.achats.domain.totals import aggregate_totals, compute_line_totals
from vorpal.modules.achats.settings import LandedCostAllocationMethod, PurchaseSettings


def test_settings_loads_defaults_from_json() -> None:
    cfg = PurchaseSettings.from_json_file()
    assert cfg.default_currency == Currency.USD
    assert cfg.default_payment_terms_days == 30
    assert cfg.default_vat_rate == Decimal("0.16")
    assert cfg.landed_cost_allocation_method == LandedCostAllocationMethod.BY_AMOUNT


def test_totals_reject_float_and_compute_decimal_accurately() -> None:
    with pytest.raises(PurchaseValidationError):
        compute_line_totals(1.5, Decimal("10.00"), Decimal("0.16"), Currency.USD)  # type: ignore[arg-type]

    with pytest.raises(PurchaseValidationError):
        compute_line_totals(Decimal("1"), 10.5, Decimal("0.16"), Currency.USD)  # type: ignore[arg-type]

    l1 = compute_line_totals(
        quantity=Decimal("3"),
        unit_price_ht=Decimal("100.00"),
        vat_rate=Decimal("0.16"),
        currency=Currency.USD,
        discount_rate=Decimal("10"),
    )
    assert l1.line_ht == Money(Decimal("270.00"), Currency.USD)
    assert l1.line_vat == Money(Decimal("43.20"), Currency.USD)
    assert l1.line_ttc == Money(Decimal("313.20"), Currency.USD)

    l2 = compute_line_totals(
        quantity=Decimal("2"),
        unit_price_ht=Decimal("50000.00"),
        vat_rate=Decimal("0.16"),
        currency=Currency.CDF,
    )
    doc_totals = aggregate_totals([l2], Currency.CDF)
    assert doc_totals.total_ht == Money(Decimal("100000.00"), Currency.CDF)
    assert doc_totals.total_vat == Money(Decimal("16000.00"), Currency.CDF)
    assert doc_totals.total_ttc == Money(Decimal("116000.00"), Currency.CDF)


def test_supplier_validation_and_order_lifecycle(
    sample_supplier: Supplier, sample_order: PurchaseOrder
) -> None:
    sample_supplier.ensure_can_order()
    sample_supplier.deactivate()
    with pytest.raises(PurchaseValidationError):
        sample_supplier.ensure_can_order()
    sample_supplier.activate()

    no_nif_supplier = Supplier(
        id="SUP-NO-NIF",
        partner=Partner(id="SUP-NO-NIF", name="Sans NIF", partner_type="fournisseur", nif=""),
    )
    with pytest.raises(PurchaseValidationError):
        no_nif_supplier.ensure_can_order()

    assert sample_order.status == PurchaseOrderStatus.DRAFT
    sample_order.confirm("BC-2026-00001")
    assert sample_order.status == PurchaseOrderStatus.CONFIRMED
    assert sample_order.totals().total_ht == Money(Decimal("1500.00"), Currency.USD)
    assert sample_order.totals().total_vat == Money(Decimal("240.00"), Currency.USD)
    assert sample_order.totals().total_ttc == Money(Decimal("1740.00"), Currency.USD)

    with pytest.raises(PurchaseConflictError):
        sample_order.confirm("BC-2026-00002")


def test_receipt_and_landed_cost_allocation_by_amount_and_quantity(
    sample_order: PurchaseOrder,
) -> None:
    sample_order.confirm("BC-2026-00001")

    receipt = GoodsReceipt(
        id="REC-001",
        purchase_order_id=sample_order.id,
        supplier_id=sample_order.supplier_id,
        receipt_date=date(2026, 11, 20),
        currency=Currency.USD,
        warehouse_code="LSH-WH1",
        lines=[
            GoodsReceiptLine(
                line_id="RL-1",
                order_line_id="L-1",
                item_code="POMPE-A1",
                designation="Pompe centrifuge",
                quantity_received=Decimal("2"),
                unit_cost_ht=Money(Decimal("500.00"), Currency.USD),
            ),
            GoodsReceiptLine(
                line_id="RL-2",
                order_line_id="L-2",
                item_code="JOINT-B2",
                designation="Kit joints renforces",
                quantity_received=Decimal("10"),
                unit_cost_ht=Money(Decimal("50.00"), Currency.USD),
            ),
        ],
    )
    receipt.validate("REC-2026-00001")
    assert receipt.status == GoodsReceiptStatus.VALIDATED
    assert receipt.total_base_ht() == Money(Decimal("1500.00"), Currency.USD)

    # 1000.00 HT sur RL-1 et 500.00 HT sur RL-2 -> 2/3 et 1/3 de 300.00 = 200.00 et 100.00
    lc_doc = LandedCostDocument(
        id="LC-001",
        goods_receipt_id=receipt.id,
        allocation_date=date(2026, 11, 20),
        currency=Currency.USD,
        method=LandedCostAllocationMethod.BY_AMOUNT,
        components=[
            LandedCostComponent(
                category="FREIGHT",
                label="Transport Lubumbashi",
                amount=Money(Decimal("200.00"), Currency.USD),
            ),
            LandedCostComponent(
                category="CUSTOMS",
                label="Dedouanement",
                amount=Money(Decimal("100.00"), Currency.USD),
            ),
        ],
    )
    allocs = allocate_landed_costs(lc_doc, receipt)
    assert len(allocs) == 2
    assert allocs[0].allocated_amount == Money(Decimal("200.00"), Currency.USD)
    assert allocs[1].allocated_amount == Money(Decimal("100.00"), Currency.USD)
    assert receipt.lines[0].effective_unit_cost_ht == Money(Decimal("600.00"), Currency.USD)
    assert receipt.lines[1].effective_unit_cost_ht == Money(Decimal("60.00"), Currency.USD)

    # Repartition par quantite : 2 sur 12 (1/6) et 10 sur 12 (5/6) de 120.00 = 20.00 et 100.00
    lc_qty = LandedCostDocument(
        id="LC-002",
        goods_receipt_id=receipt.id,
        allocation_date=date(2026, 11, 20),
        currency=Currency.USD,
        method=LandedCostAllocationMethod.BY_QUANTITY,
        components=[
            LandedCostComponent(
                category="HANDLING",
                label="Manutention",
                amount=Money(Decimal("120.00"), Currency.USD),
            ),
        ],
    )
    allocs_qty = allocate_landed_costs(lc_qty, receipt)
    assert allocs_qty[0].allocated_amount == Money(Decimal("20.00"), Currency.USD)
    assert allocs_qty[1].allocated_amount == Money(Decimal("100.00"), Currency.USD)
