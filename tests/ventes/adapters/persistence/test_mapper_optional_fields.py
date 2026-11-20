"""Tests ciblés des champs optionnels des conversions SQL des ventes."""

from datetime import UTC, date, datetime
from decimal import Decimal

from vorpal.core import Currency, Money
from vorpal.modules.ventes.adapters.persistence.mappers import (
    invoice_from_model,
    order_from_model,
)
from vorpal.modules.ventes.adapters.persistence.models import (
    InvoiceLineModel,
    OrderLineModel,
    SalesInvoiceModel,
    SalesOrderModel,
)
from vorpal.modules.ventes.domain.invoices import FiscalStamp


def test_order_from_model_reconstructs_required_deposit() -> None:
    header = SalesOrderModel(
        id="CMD-TEST-001",
        number="CMD-2026-001",
        customer_id="CLIENT-001",
        order_date=date(2026, 11, 20),
        currency="USD",
        status="DRAFT",
        deposit_required_amount=Decimal("150.00"),
        deposit_required_currency="USD",
    )

    line = OrderLineModel(
        line_id="LIGNE-001",
        order_id="CMD-TEST-001",
        line_order=0,
        item_code="ART-001",
        designation="Article de test",
        quantity=Decimal("1"),
        unit_price_ht=Decimal("200.00"),
        currency="USD",
        discount_percent=Decimal("0"),
        tax_group="A",
        vat_rate=Decimal("0.16"),
        is_out_of_scope=False,
        invoiced_quantity=Decimal("0"),
    )

    result = order_from_model(header, [line])

    assert result.deposit_required == Money(
        Decimal("150.00"),
        Currency.USD,
    )
    assert result.deposit_required is not None
    assert result.deposit_required.amount == Decimal("150.00")
    assert result.deposit_required.currency == Currency.USD


def test_invoice_from_model_reconstructs_fiscal_stamp() -> None:
    certified_at = datetime(
        2026, 11, 20, 10, 30, 0, tzinfo=UTC
    )

    header = SalesInvoiceModel(
        id="FAC-TEST-001",
        customer_id="CLIENT-001",
        invoice_date=date(2026, 11, 20),
        due_date=date(2026, 12, 20),
        currency="USD",
        invoice_type="vente",
        status="CERTIFIED",
        stamp_device_id="DEVICE-001",
        stamp_auth_code="AUTH-001",
        stamp_qr_code="QR-TEST-001",
        stamp_certified_at=certified_at,
        stamp_counters_json="{}",
    )

    line = InvoiceLineModel(
        invoice_id="FAC-TEST-001",
        line_order=0,
        item_code="ART-001",
        designation="Article certifié",
        quantity=Decimal("1"),
        unit_price_ht=Decimal("200.00"),
        currency="USD",
        discount_percent=Decimal("0"),
        tax_group="A",
        vat_rate=Decimal("0.16"),
        is_out_of_scope=False,
        order_line_id=None,
    )

    result = invoice_from_model(header, [line])

    assert isinstance(result.fiscal_stamp, FiscalStamp)
    assert result.fiscal_stamp is not None
    assert result.fiscal_stamp.device_id == "DEVICE-001"
    assert result.fiscal_stamp.auth_code == "AUTH-001"
    assert result.fiscal_stamp.qr_code == "QR-TEST-001"
    assert result.fiscal_stamp.certified_at == certified_at
    assert result.fiscal_stamp.counters == {}
