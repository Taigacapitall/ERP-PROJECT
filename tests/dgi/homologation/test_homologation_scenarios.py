"""Suite de tests d'homologation rejouables pour le module Conformite DGI."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.dgi.conftest import make_valid_invoice
from vorpal.core.money import Currency, Money
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.credit_note import CreditNoteRequest
from vorpal.modules.dgi.models import CertificationStatus, InvoiceDTO, InvoiceLineDTO, PartyTaxInfo
from vorpal.modules.dgi.service import CertificationService


def test_homologation_scenario_multi_tax_and_out_of_scope(
    certification_service: CertificationService,
) -> None:
    """Cas d'homologation : facture multi-taxes (TVA + exonere + hors champ avec code article)."""
    l1 = InvoiceLineDTO(
        line_id="H-L1",
        item_code="ART-STD",
        designation="Equipement standard",
        quantity=Decimal("1"),
        unit_price_ht=Money(Decimal("100.00"), Currency.USD),
        line_total_ht=Money(Decimal("100.00"), Currency.USD),
        vat_rate=Decimal("0.16"),
        vat_amount=Money(Decimal("16.00"), Currency.USD),
        line_total_ttc=Money(Decimal("116.00"), Currency.USD),
        tax_group_code="TG-01",
        out_of_scope=False,
    )
    l2 = InvoiceLineDTO(
        line_id="H-L2",
        item_code="ART-HC",
        designation="Debours hors champ",
        quantity=Decimal("1"),
        unit_price_ht=Money(Decimal("40.00"), Currency.USD),
        line_total_ht=Money(Decimal("40.00"), Currency.USD),
        vat_rate=Decimal("0.00"),
        vat_amount=Money(Decimal("0.00"), Currency.USD),
        line_total_ttc=Money(Decimal("40.00"), Currency.USD),
        tax_group_code="TG-HC",
        out_of_scope=True,
    )
    inv = InvoiceDTO(
        invoice_id="HOM-MULTI-001",
        serial_number="SN-HOM-MULTI-001",
        issue_date=date(2026, 11, 20),
        currency=Currency.USD,
        seller=PartyTaxInfo(
            name="Vorpal Systems RDC",
            tax_id="NIF-VND-900",
            rccm="CD/LSH/RCCM/24-B-100",
            address="Lubumbashi",
        ),
        customer=PartyTaxInfo(
            name="Client Homologation",
            tax_id="NIF-HOM-001",
            rccm="CD/LSH/RCCM/22-B-005",
            address="Lubumbashi",
        ),
        lines=[l1, l2],
        total_ht=Money(Decimal("140.00"), Currency.USD),
        total_vat=Money(Decimal("16.00"), Currency.USD),
        non_taxable_amount=Money(Decimal("0.00"), Currency.USD),
        other_taxes_amount=Money(Decimal("0.00"), Currency.USD),
        total_ttc=Money(Decimal("156.00"), Currency.USD),
    )

    certified = certification_service.certify_invoice(inv)
    assert certified.status == CertificationStatus.CERTIFIED
    assert certified.certification is not None


def test_homologation_scenario_rejection_outage_recovery_and_credit_note(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
    dgi_clock,
) -> None:
    """Cas d'homologation rejouable : rejet dispositif -> coupure -> reprise -> avoir."""
    mock_client.reject_serials.add("SN-HOM-REJ")
    rejected_inv = make_valid_invoice(invoice_id="HOM-REJ", serial_number="SN-HOM-REJ")
    res_rej = certification_service.certify_invoice(rejected_inv)
    assert res_rej.status == CertificationStatus.REJECTED

    mock_client.simulate_outage(enabled=True)
    inv_retry = make_valid_invoice(invoice_id="HOM-REC-1", serial_number="SN-HOM-REC-1")
    res_retry = certification_service.certify_invoice(inv_retry)
    assert res_retry.status == CertificationStatus.RETRY

    mock_client.simulate_outage(enabled=False)
    recovered_list = certification_service.process_retry_queue(now=dgi_clock + timedelta(minutes=1))
    assert len(recovered_list) == 1
    assert recovered_list[0].status == CertificationStatus.CERTIFIED

    cn = make_valid_invoice(invoice_id="HOM-CN-1", serial_number="SN-HOM-CN-1")
    cn.total_ht = Money(Decimal("50.00"), Currency.USD)
    cn.total_vat = Money(Decimal("8.00"), Currency.USD)
    cn.total_ttc = Money(Decimal("58.00"), Currency.USD)
    cn.lines = [
        InvoiceLineDTO(
            line_id="HCN-1",
            item_code="SRV-01",
            designation="Avoir partiel homologation",
            quantity=Decimal("1"),
            unit_price_ht=Money(Decimal("50.00"), Currency.USD),
            line_total_ht=Money(Decimal("50.00"), Currency.USD),
            vat_rate=Decimal("0.16"),
            vat_amount=Money(Decimal("8.00"), Currency.USD),
            line_total_ttc=Money(Decimal("58.00"), Currency.USD),
            tax_group_code="TG-01",
        )
    ]
    certified_cn = certification_service.certify_credit_note(
        CreditNoteRequest(
            credit_note_invoice=cn,
            original_invoice=recovered_list[0],
            reason="Ajustement homologation",
        )
    )
    assert certified_cn.status == CertificationStatus.CERTIFIED
    assert certification_service._journal.verify_integrity() is True
