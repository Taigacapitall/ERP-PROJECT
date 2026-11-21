"""Idempotence stricte de SalesInvoice.mark_certified (jamais d'ecrasement du cachet)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from vorpal.core import Currency, Money
from vorpal.core.contracts import CertifiedInvoiceDTO, InvoiceLineDTO, PartyInfoDTO
from vorpal.modules.ventes.domain.errors import SalesConflictError
from vorpal.modules.ventes.domain.invoices import InvoiceLine, InvoiceStatus, SalesInvoice

USD = Currency.USD
CERT_AT = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


def validated() -> SalesInvoice:
    return SalesInvoice(
        id="I1", customer_id="C1", invoice_date=date(2026, 11, 20), due_date=date(2026, 12, 20),
        currency=USD, number="F-1", status=InvoiceStatus.VALIDATED,
        lines=(InvoiceLine(item_code="X", designation="d", quantity=Decimal("1"),
                           unit_price_ht=Money(Decimal("100.00"), USD)),),
        total_ht=Money(Decimal("100.00"), USD), total_vat=Money(Decimal("16.00"), USD),
        total_ttc=Money(Decimal("116.00"), USD),
    )


def dto(auth: str = "AUTH-1", at: datetime = CERT_AT) -> CertifiedInvoiceDTO:
    return CertifiedInvoiceDTO(
        number="F-1", invoice_date=date(2026, 11, 20),
        seller=PartyInfoDTO(name="V"), customer=PartyInfoDTO(name="C"),
        lines=[InvoiceLineDTO(designation="d", quantity=Decimal("1"),
                              unit_price_ht=Decimal("100.00"), vat_rate=Decimal("0.16"))],
        currency=USD, total_ht=Decimal("100.00"), total_vat=Decimal("16.00"),
        total_ttc=Decimal("116.00"), device_id="MCF-01", auth_code=auth, qr_code="QR-1",
        certified_at=at,
    )


def test_same_stamp_is_idempotent():
    first = validated().mark_certified(dto())
    assert first.status == InvoiceStatus.CERTIFIED
    assert first.mark_certified(dto()) is first


def test_different_stamp_raises_and_keeps_original():
    first = validated().mark_certified(dto())
    with pytest.raises(SalesConflictError):
        first.mark_certified(dto(auth="AUTH-2"))
    assert first.fiscal_stamp is not None and first.fiscal_stamp.auth_code == "AUTH-1"


def test_naive_datetime_after_sql_reload_is_same_stamp():
    first = validated().mark_certified(dto())
    assert first.fiscal_stamp is not None
    reloaded = replace(first, fiscal_stamp=replace(first.fiscal_stamp, certified_at=CERT_AT.replace(tzinfo=None)))
    assert reloaded.mark_certified(dto()) is reloaded


def test_certified_without_stamp_cannot_be_silently_accepted():
    broken = replace(validated(), status=InvoiceStatus.CERTIFIED, fiscal_stamp=None)
    with pytest.raises(SalesConflictError):
        broken.mark_certified(dto())
