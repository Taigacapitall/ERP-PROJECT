"""Reception reelle de invoice.ready_for_certification par DGI via l'EventBus (SQLite non requis ici)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from vorpal.core.contracts import InvoiceDTO, InvoiceLineDTO, PartyInfoDTO
from vorpal.core.events import Event, EventBus, InMemoryEventStore
from vorpal.core.money import Currency
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.event_handlers import InvoiceReadyForCertificationHandler
from vorpal.modules.dgi.journal import FiscalJournal
from vorpal.modules.dgi.models import CertificationStatus
from vorpal.modules.dgi.outbox import OutboxRepository
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.service import CertificationService
from vorpal.modules.dgi.tax_groups import TaxGroupTable

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
NUMBER = "FAC-2026-000001"
READY = "invoice.ready_for_certification"


def core_invoice(tax_group: str = "A") -> InvoiceDTO:
    return InvoiceDTO(
        number=NUMBER,
        invoice_date=date(2026, 11, 20),
        seller=PartyInfoDTO(name="Vorpal", nif="NIF-V", rccm="RCCM-V", address="Lubumbashi"),
        customer=PartyInfoDTO(name="Client", nif="NIF-C", rccm="RCCM-C", address="Lubumbashi"),
        lines=[
            InvoiceLineDTO(
                designation="Prestation", quantity=Decimal("2"), unit_price_ht=Decimal("100.00"),
                tax_group=tax_group, vat_rate=Decimal("0.16"), item_code="SRV-01",
            )
        ],
        currency=Currency.USD,
        total_ht=Decimal("200.00"), total_vat=Decimal("32.00"), total_ttc=Decimal("232.00"),
    )


def ready_event(invoice: InvoiceDTO) -> Event:
    return Event(type=READY, payload={"invoice": invoice.model_dump(mode="json")}, occurred_at=NOW,
                 correlation_id=invoice.number, source_module="ventes")


def setup(mapped: bool = True):
    base = TaxGroupTable.from_json_file()
    # Configuration fiscale de TEST, declaree explicitement (aucune homologation).
    table = TaxGroupTable(
        groups=base.list_active_on(date(2026, 11, 20)),
        mappings={"A": "TG-01"} if mapped else {},
    )
    bus = EventBus(store=InMemoryEventStore())
    client = MockMCFClient(_device_id="MCF-TEST", clock=lambda: NOW)
    service = CertificationService(
        device_client=client, tax_table=table, outbox=OutboxRepository(), journal=FiscalJournal(),
        event_bus=bus, retry_policy=RetryPolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300),
        clock=lambda: NOW,
    )
    bus.subscribe(READY, InvoiceReadyForCertificationHandler(service))
    certified: list[Event] = []
    rejected: list[Event] = []

    def distinct(bucket: list[Event]):
        seen: set[str] = set()

        def handler(event: Event) -> None:
            if event.id not in seen:  # bus.replay() rejoue les evenements : on compte les emissions
                seen.add(event.id)
                bucket.append(event)

        return handler

    bus.subscribe("invoice.certified", distinct(certified))
    bus.subscribe("invoice.rejected", distinct(rejected))
    return bus, service, client, certified, rejected


def test_dgi_really_receives_event_and_publishes_certified_with_stamp():
    bus, service, _, certified, rejected = setup()
    bus.publish(ready_event(core_invoice()))
    assert service.certification_status(NUMBER) == CertificationStatus.CERTIFIED
    (event,) = certified
    for key in ("fiscal_device_id", "authentication_code", "qr_code", "certified_at"):
        assert event.payload[key]
    assert event.payload["serial_number"] == NUMBER
    assert rejected == []


def test_repeated_event_and_bus_replay_do_not_double_certify():
    bus, _, _, certified, _ = setup()
    bus.publish(ready_event(core_invoice()))
    bus.publish(ready_event(core_invoice()))  # autre id d'evenement, meme facture
    bus.replay()
    assert len(certified) == 1


def test_final_rejection_published_once_and_not_resubmitted_on_replay():
    bus, service, client, certified, rejected = setup()
    client.reject_serials.add(NUMBER)
    bus.publish(ready_event(core_invoice()))
    assert service.certification_status(NUMBER) == CertificationStatus.REJECTED
    assert certified == [] and len(rejected) == 1
    bus.replay()
    assert len(rejected) == 1


def test_temporary_outage_then_certified_after_retry():
    bus, service, client, certified, rejected = setup()
    client.simulate_outage(True)
    bus.publish(ready_event(core_invoice()))
    assert service.certification_status(NUMBER) == CertificationStatus.RETRY
    assert certified == [] and rejected == []
    client.simulate_outage(False)
    done = service.process_retry_queue(NOW + timedelta(hours=1))
    assert [d.status for d in done] == [CertificationStatus.CERTIFIED]
    assert len(certified) == 1


def test_retry_then_final_rejection_is_published():
    bus, service, client, certified, rejected = setup()
    client.simulate_outage(True)
    bus.publish(ready_event(core_invoice()))
    client.simulate_outage(False)
    client.reject_serials.add(NUMBER)
    service.process_retry_queue(NOW + timedelta(hours=1))
    assert certified == [] and len(rejected) == 1


def test_missing_tax_mapping_refuses_certification_with_clear_reason():
    bus, service, _, certified, rejected = setup(mapped=False)
    bus.publish(ready_event(core_invoice("A")))
    assert certified == []
    (event,) = rejected
    assert "Correspondance fiscale absente" in str(event.payload["reason"])
    assert event.payload["serial_number"] == NUMBER
    assert service.certification_status(NUMBER) is None
