from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from vorpal.core.contracts.dto import (
    CertifiedInvoiceDTO,
    InvoiceDTO,
    InvoiceLineDTO,
    PartyInfoDTO,
)
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED, OFFICIAL_EVENTS
from vorpal.core.money import Currency


def _line(**over: object) -> InvoiceLineDTO:
    data: dict[str, object] = {
        "designation": "Service",
        "quantity": Decimal(1),
        "unit_price_ht": Decimal("100.00"),
        "vat_rate": Decimal("0.16"),
    }
    data.update(over)
    return InvoiceLineDTO(**data)  # type: ignore[arg-type]


def _invoice(**over: object) -> InvoiceDTO:
    data: dict[str, object] = {
        "number": "F-1",
        "invoice_date": date(2026, 11, 20),
        "seller": PartyInfoDTO(name="V", nif="N1", rccm="R1", address="Lubumbashi"),
        "customer": PartyInfoDTO(name="C", nif="N2"),
        "lines": [_line()],
        "currency": Currency.USD,
        "total_ht": Decimal("100.00"),
        "total_vat": Decimal("16.00"),
        "total_ttc": Decimal("116.00"),
    }
    data.update(over)
    return InvoiceDTO(**data)  # type: ignore[arg-type]


def test_line_item_code_is_optional_and_not_empty() -> None:
    assert _line().item_code is None
    assert _line(item_code="ART-001").item_code == "ART-001"
    with pytest.raises(PydanticValidationError):
        _line(item_code="")


def test_invoice_non_taxable_and_other_taxes_default_to_zero() -> None:
    inv = _invoice()
    assert inv.non_taxable_amount == Decimal(0)
    assert inv.other_taxes == Decimal(0)


def test_invoice_accepts_non_taxable_and_other_taxes_without_changing_totals_rules() -> None:
    inv = _invoice(non_taxable_amount=Decimal("20.00"), other_taxes=Decimal("5.00"))
    assert inv.non_taxable_amount == Decimal("20.00")
    assert inv.other_taxes == Decimal("5.00")
    assert inv.total_ttc == Decimal("116.00")
    with pytest.raises(PydanticValidationError):
        _invoice(non_taxable_amount=Decimal("20.00"), total_ttc=Decimal("120.00"))


@pytest.mark.parametrize("field", ["non_taxable_amount", "other_taxes"])
@pytest.mark.parametrize("bad", [1.5, True, Decimal("-1")])
def test_non_taxable_and_other_taxes_reject_float_bool_and_negative(
    field: str, bad: object
) -> None:
    with pytest.raises(PydanticValidationError):
        _invoice(**{field: bad})


def test_certified_invoice_inherits_new_fields() -> None:
    base = _invoice(
        lines=[_line(item_code="ART-001")],
        non_taxable_amount=Decimal("20.00"),
        other_taxes=Decimal("5.00"),
    )
    cert = CertifiedInvoiceDTO(
        **base.model_dump(),
        device_id="DEF-1",
        auth_code="AUTH",
        qr_code="QR",
        certified_at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    assert cert.non_taxable_amount == Decimal("20.00")
    assert cert.other_taxes == Decimal("5.00")
    assert cert.lines[0].item_code == "ART-001"


def test_handler_failed_is_a_system_event_outside_the_official_catalogue() -> None:
    assert EVENT_HANDLER_FAILED == "event.handler_failed"
    assert EVENT_HANDLER_FAILED not in OFFICIAL_EVENTS
