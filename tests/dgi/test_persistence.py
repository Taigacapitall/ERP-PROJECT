"""Outbox et journal DGI persistants (SQLite en memoire) : redemarrage, reconciliation, integrite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from vorpal.core.db import SchemaMigrationManager, create_db_engine
from vorpal.core.errors import ExternalServiceError
from vorpal.core.events import EventBus, InMemoryEventStore
from vorpal.modules.dgi.clients.base import DeviceResponse, FiscalDeviceClient
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.models import CertificationStatus, FiscalCertificationData, InvoiceDTO
from vorpal.modules.dgi.persistence import (
    DGI_MIGRATIONS,
    SqlFiscalJournal,
    SqlOutboxRepository,
    invoice_from_json,
    invoice_to_json,
)
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.service import CertificationService
from vorpal.modules.dgi.tax_groups import TaxGroupTable

from .conftest import make_valid_invoice

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
SERIAL = "SN-2026-00001"
GOOD = FiscalCertificationData("MCF-1", "AUTH-1", "QR-1", "2026-11-20T12:00:00+00:00")
BAD = FiscalCertificationData("MCF-1", "AUTH-1", "QR-1", None)


class ScriptedClient(FiscalDeviceClient):
    def __init__(self, submits: list[DeviceResponse | Exception], status: DeviceResponse | None = None) -> None:
        self._submits = list(submits)
        self._status = status or DeviceResponse(accepted=False, status_hint="PENDING")
        self.submit_calls = 0

    @property
    def device_id(self) -> str:
        return "SCRIPT"

    def submit(self, invoice: InvoiceDTO) -> DeviceResponse:
        self.submit_calls += 1
        item = self._submits.pop(0) if len(self._submits) > 1 else self._submits[0]
        if isinstance(item, Exception):
            raise item
        return item

    def get_status(self, invoice_serial_or_id: str) -> DeviceResponse:
        return self._status


@pytest.fixture
def factory():
    engine = create_db_engine("sqlite:///:memory:")
    SchemaMigrationManager(engine).apply("dgi", DGI_MIGRATIONS)
    yield sessionmaker(bind=engine)
    engine.dispose()


def make_service(factory, client, max_attempts: int = 5) -> CertificationService:
    return CertificationService(
        device_client=client,
        tax_table=TaxGroupTable.from_json_file(),
        outbox=SqlOutboxRepository(factory),
        journal=SqlFiscalJournal(factory),
        event_bus=EventBus(store=InMemoryEventStore()),
        retry_policy=RetryPolicy(max_attempts=max_attempts, base_delay_seconds=10, max_delay_seconds=300),
        clock=lambda: NOW,
    )


def test_invoice_json_roundtrip_is_lossless():
    invoice = make_valid_invoice()
    restored = invoice_from_json(invoice_to_json(invoice))
    assert invoice_to_json(restored) == invoice_to_json(invoice)
    assert restored.total_ttc.amount == Decimal("232.00")
    assert restored.lines[0].tax_group_code == "TG-01"


def test_pending_retry_survives_restart(factory):
    down = MockMCFClient(_device_id="MCF-1", clock=lambda: NOW)
    down.simulate_outage(True)
    assert make_service(factory, down).certify_invoice(make_valid_invoice()).status == CertificationStatus.RETRY

    restarted = make_service(factory, MockMCFClient(_device_id="MCF-1", clock=lambda: NOW))
    assert restarted.certification_status(SERIAL) == CertificationStatus.RETRY
    done = restarted.process_retry_queue(NOW + timedelta(hours=1))
    assert [d.status for d in done] == [CertificationStatus.CERTIFIED]
    assert make_service(factory, down).certification_status(SERIAL) == CertificationStatus.CERTIFIED


def test_network_error_then_reconciliation_without_resubmission(factory):
    status = DeviceResponse(accepted=True, status_hint="OK", certification=GOOD)
    client = ScriptedClient([ExternalServiceError("reseau coupe")], status=status)
    assert make_service(factory, client).certify_invoice(make_valid_invoice()).status == CertificationStatus.RETRY
    done = make_service(factory, client).process_retry_queue(NOW + timedelta(hours=1))
    assert [d.status for d in done] == [CertificationStatus.CERTIFIED]
    assert client.submit_calls == 1


def test_incomplete_stamp_at_exhaustion_is_reconciled_with_device_status(factory):
    status = DeviceResponse(accepted=True, status_hint="OK", certification=GOOD)
    client = ScriptedClient([DeviceResponse(accepted=True, status_hint="OK", certification=BAD)], status=status)
    result = make_service(factory, client, max_attempts=1).certify_invoice(make_valid_invoice())
    assert result.status == CertificationStatus.CERTIFIED
    assert result.certification is not None and result.certification.authentication_code == "AUTH-1"


def test_journal_chain_persists_and_detects_tampering(factory):
    make_service(factory, MockMCFClient(_device_id="MCF-1", clock=lambda: NOW)).certify_invoice(make_valid_invoice())
    journal = SqlFiscalJournal(factory)
    assert journal.entries() and journal.verify_integrity()
    journal.append("X", "Y", CertificationStatus.REJECTED, {}, {}, NOW)
    fresh = SqlFiscalJournal(factory)
    assert [e.sequence for e in fresh.entries()] == list(range(1, len(fresh.entries()) + 1))
    assert fresh.verify_integrity()
    with factory() as session:
        session.execute(text("UPDATE dgi_journal SET response_json = '{}' WHERE sequence = 1"))
        session.commit()
    assert not fresh.verify_integrity()


def test_rejected_entry_is_reopened_only_by_a_later_request(factory):
    refused = DeviceResponse(accepted=False, status_hint="REJECTED", error_code="DGI_INVALID_NIF")
    service = make_service(factory, ScriptedClient([refused]))
    assert service.certify_invoice(make_valid_invoice()).status == CertificationStatus.REJECTED
    assert service.reopen_rejected(SERIAL, NOW) is False
    assert service.reopen_rejected(SERIAL, NOW + timedelta(minutes=1)) is True
    assert service.certification_status(SERIAL) == CertificationStatus.PENDING
