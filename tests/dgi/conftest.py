"""Fixtures partagees pour les tests et l'homologation du module DGI."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from vorpal.core.audit import AuditLog
from vorpal.core.events import EventBus, InMemoryEventStore
from vorpal.core.money import Currency, Money
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.journal import FiscalJournal
from vorpal.modules.dgi.models import InvoiceDTO, InvoiceLineDTO, PartyTaxInfo
from vorpal.modules.dgi.outbox import OutboxRepository
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.service import CertificationService
from vorpal.modules.dgi.tax_groups import TaxGroupTable


@pytest.fixture
def dgi_clock() -> datetime:
    return datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def dgi_clock_fn(dgi_clock: datetime):
    def _clock() -> datetime:
        return dgi_clock

    return _clock


@pytest.fixture
def tax_group_table() -> TaxGroupTable:
    return TaxGroupTable.from_json_file()


@pytest.fixture
def mock_client(dgi_clock_fn) -> MockMCFClient:
    return MockMCFClient(_device_id="MCF-RDC-01", clock=dgi_clock_fn)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus(store=InMemoryEventStore())


@pytest.fixture
def certification_service(
    mock_client: MockMCFClient,
    tax_group_table: TaxGroupTable,
    event_bus: EventBus,
    dgi_clock_fn,
) -> CertificationService:
    return CertificationService(
        device_client=mock_client,
        tax_table=tax_group_table,
        outbox=OutboxRepository(),
        journal=FiscalJournal(),
        event_bus=event_bus,
        audit=AuditLog(),
        retry_policy=RetryPolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300),
        clock=dgi_clock_fn,
    )


def make_valid_invoice(
    invoice_id: str = "INV-2026-001",
    serial_number: str = "SN-2026-00001",
    issue_date: date = date(2026, 11, 20),
    customer_tax_id: str | None = "NIF-CLI-001",
) -> InvoiceDTO:
    line = InvoiceLineDTO(
        line_id="LN-1",
        item_code="SRV-01",
        designation="Prestation reseau et securite",
        quantity=Decimal("2"),
        unit_price_ht=Money(Decimal("100.00"), Currency.USD),
        line_total_ht=Money(Decimal("200.00"), Currency.USD),
        vat_rate=Decimal("0.16"),
        vat_amount=Money(Decimal("32.00"), Currency.USD),
        line_total_ttc=Money(Decimal("232.00"), Currency.USD),
        tax_group_code="TG-01",
        out_of_scope=False,
    )
    return InvoiceDTO(
        invoice_id=invoice_id,
        serial_number=serial_number,
        issue_date=issue_date,
        currency=Currency.USD,
        seller=PartyTaxInfo(
            name="Vorpal Systems RDC",
            tax_id="NIF-VND-900",
            rccm="CD/LSH/RCCM/24-B-100",
            address="Av. Mobutu, Lubumbashi",
        ),
        customer=PartyTaxInfo(
            name="Hopital General de Reference",
            tax_id=customer_tax_id,
            rccm="CD/LSH/RCCM/20-A-012",
            address="Commune de Lubumbashi",
        ),
        lines=[line],
        total_ht=Money(Decimal("200.00"), Currency.USD),
        total_vat=Money(Decimal("32.00"), Currency.USD),
        non_taxable_amount=Money(Decimal("0.00"), Currency.USD),
        other_taxes_amount=Money(Decimal("0.00"), Currency.USD),
        total_ttc=Money(Decimal("232.00"), Currency.USD),
    )
