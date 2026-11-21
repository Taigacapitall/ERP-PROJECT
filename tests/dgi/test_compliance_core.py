"""Tests unitaires conformite DGI : mentions, groupes de taxe, etats, avoirs, rendu et journal."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.dgi.conftest import make_valid_invoice
from vorpal.core.errors import ComplianceError
from vorpal.core.events import Event
from vorpal.core.money import Currency, Money
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.credit_note import CreditNoteRequest
from vorpal.modules.dgi.errors_map import DgiErrorTranslator
from vorpal.modules.dgi.models import (
    CertificationStatus,
    InvoiceDTO,
    InvoiceLineDTO,
    strict_decimal,
)
from vorpal.modules.dgi.renderers import HtmlRenderer, TextRenderer
from vorpal.modules.dgi.service import CertificationService
from vorpal.modules.dgi.state_machine import (
    ensure_not_certified_immutable,
    transition_invoice_status,
)
from vorpal.modules.dgi.tax_groups import TaxGroupTable


def test_strict_decimal_rejects_float() -> None:
    with pytest.raises(ComplianceError):
        strict_decimal(12.34, "montant")  # type: ignore[arg-type]
    assert strict_decimal("12.34", "montant") == Decimal("12.34")


def test_tax_groups_versioned_by_date_and_out_of_scope_requires_item_code(
    tax_group_table: TaxGroupTable,
) -> None:
    legacy_group = tax_group_table.resolve_group("TG-LEGACY-01", date(2025, 6, 15))
    assert legacy_group.code == "TG-LEGACY-01"

    with pytest.raises(ComplianceError):
        tax_group_table.resolve_group("TG-LEGACY-01", date(2026, 1, 10))

    new_group = tax_group_table.resolve_group("TG-NEW-2026", date(2026, 1, 10))
    assert new_group.code == "TG-NEW-2026"

    with pytest.raises(ComplianceError):
        InvoiceLineDTO(
            line_id="HC-1",
            item_code=None,
            designation="Operation hors champ",
            quantity=Decimal("1"),
            unit_price_ht=Money(Decimal("50.00"), Currency.USD),
            line_total_ht=Money(Decimal("50.00"), Currency.USD),
            vat_rate=Decimal("0.00"),
            vat_amount=Money(Decimal("0.00"), Currency.USD),
            line_total_ttc=Money(Decimal("50.00"), Currency.USD),
            tax_group_code="TG-HC",
            out_of_scope=True,
        )


def test_certification_flow_mentions_immutability_credit_note_render_and_journal(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
    event_bus,
    dgi_clock,
) -> None:
    emitted: list[Event] = []
    event_bus.subscribe("*", lambda ev: emitted.append(ev))

    # Facture sans NIF client refusee avant envoi
    inv_no_nif = make_valid_invoice(invoice_id="INV-ERR-1", serial_number="SN-ERR-1", customer_tax_id="")
    with pytest.raises(ComplianceError):
        certification_service.certify_invoice(inv_no_nif)
    assert inv_no_nif.status == CertificationStatus.REJECTED

    # Certification nominale
    inv = make_valid_invoice(invoice_id="INV-2026-100", serial_number="SN-2026-00100")
    certified = certification_service.certify_invoice(inv)
    assert certified.status == CertificationStatus.CERTIFIED
    assert certified.certification is not None
    assert certified.certification.authentication_code.startswith("AUTH-")
    assert "DGI-RDC" in certified.certification.qr_code

    # Idempotence : ne re-soumet pas deux fois une facture deja certifiee
    certified_again = certification_service.certify_invoice(certified)
    assert certified_again.status == CertificationStatus.CERTIFIED

    # Immutabilite de la facture certifiee
    with pytest.raises(ComplianceError):
        ensure_not_certified_immutable(certified, operation="edition")
    with pytest.raises(ComplianceError):
        transition_invoice_status(certified, CertificationStatus.REJECTED)

    # Avoir superieur a la facture d'origine refuse
    oversized_cn = make_valid_invoice(invoice_id="CN-TOO-BIG", serial_number="SN-CN-BIG")
    oversized_cn.total_ht = Money(Decimal("300.00"), Currency.USD)
    oversized_cn.total_ttc = Money(Decimal("332.00"), Currency.USD)
    oversized_cn.lines = [
        InvoiceLineDTO(
            line_id="CNL-1",
            item_code="SRV-01",
            designation="Avoir partiel",
            quantity=Decimal("3"),
            unit_price_ht=Money(Decimal("100.00"), Currency.USD),
            line_total_ht=Money(Decimal("300.00"), Currency.USD),
            vat_rate=Decimal("0.16"),
            vat_amount=Money(Decimal("32.00"), Currency.USD),
            line_total_ttc=Money(Decimal("332.00"), Currency.USD),
            tax_group_code="TG-01",
        )
    ]
    with pytest.raises(ComplianceError):
        certification_service.certify_credit_note(
            CreditNoteRequest(
                credit_note_invoice=oversized_cn,
                original_invoice=certified,
                reason="Erreur quantite",
            )
        )

    # Avoir valide (<= facture d'origine)
    valid_cn = make_valid_invoice(invoice_id="CN-2026-001", serial_number="SN-CN-00001")
    valid_cn.total_ht = Money(Decimal("100.00"), Currency.USD)
    valid_cn.total_vat = Money(Decimal("16.00"), Currency.USD)
    valid_cn.total_ttc = Money(Decimal("116.00"), Currency.USD)
    valid_cn.lines = [
        InvoiceLineDTO(
            line_id="CNL-1",
            item_code="SRV-01",
            designation="Avoir partiel prestation",
            quantity=Decimal("1"),
            unit_price_ht=Money(Decimal("100.00"), Currency.USD),
            line_total_ht=Money(Decimal("100.00"), Currency.USD),
            vat_rate=Decimal("0.16"),
            vat_amount=Money(Decimal("16.00"), Currency.USD),
            line_total_ttc=Money(Decimal("116.00"), Currency.USD),
            tax_group_code="TG-01",
        )
    ]
    cert_cn = certification_service.certify_credit_note(
        CreditNoteRequest(
            credit_note_invoice=valid_cn,
            original_invoice=certified,
            reason="Reduction commerciale",
        )
    )
    assert cert_cn.status == CertificationStatus.CERTIFIED
    assert cert_cn.is_credit_note is True

    # Rendus texte et HTML avec les 12 mentions
    txt = TextRenderer().render(certified)
    html_out = HtmlRenderer().render(certified)
    assert "NIF-VND-900" in txt
    assert "NIF-CLI-001" in txt
    assert certified.certification.authentication_code in txt
    assert "<!DOCTYPE html>" in html_out

    # Rendu bloque si une mention manque sur facture non certifiee
    uncertified: InvoiceDTO = make_valid_invoice(invoice_id="INV-UNCONF", serial_number="SN-UNCONF")
    with pytest.raises(ComplianceError):
        TextRenderer().render(uncertified)

    # Journal fiscal : recherche, hash chaine et export CSV
    found = certification_service._journal.search(invoice_id="INV-2026-100")
    assert len(found) == 1
    assert certification_service._journal.verify_integrity() is True
    csv_text = certification_service._journal.export_csv()
    assert "entry_hash" in csv_text
    assert "INV-2026-100" in csv_text

    # Traducteur d'erreurs extensible
    translator = DgiErrorTranslator()
    assert "NIF" in translator.translate("DGI_INVALID_NIF")

    # Evenements emis
    event_names = {ev.type for ev in emitted}
    assert "invoice.certified" in event_names
    assert "invoice.rejected" in event_names
    assert "credit_note.certified" in event_names


def test_outage_and_automatic_retry_without_duplicates(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
    dgi_clock,
) -> None:
    inv = make_valid_invoice(invoice_id="INV-OUTAGE-1", serial_number="SN-OUTAGE-1")
    mock_client.simulate_outage(enabled=True)

    result_during_outage = certification_service.certify_invoice(inv)
    assert result_during_outage.status == CertificationStatus.RETRY

    # Retablissement du dispositif fiscal + avance horloge
    mock_client.simulate_outage(enabled=False)
    recovered = certification_service.process_retry_queue(now=dgi_clock + timedelta(minutes=5))
    assert len(recovered) == 1
    assert recovered[0].status == CertificationStatus.CERTIFIED
    assert recovered[0].certification is not None
