"""Tests d'intégration du contrat FiscalCertifier Core vers le pipeline DGI."""

from datetime import date
from decimal import Decimal

import pytest

from vorpal.core.contracts import (
    CertifiedInvoiceDTO,
    InvoiceDTO,
    InvoiceLineDTO,
    PartyInfoDTO,
)
from vorpal.core.errors import ComplianceError
from vorpal.core.money import Currency
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.service import CertificationService


def make_core_invoice(
    number: str = "CORE-INV-001",
    *,
    tax_group: str = "TG-01",
    vat_rate: Decimal = Decimal("0.16"),
    invoice_type: str = "vente",
    original_invoice_ref: str | None = None,
    non_taxable_amount: Decimal = Decimal("0"),
    other_taxes: Decimal = Decimal("0"),
) -> InvoiceDTO:
    """Construit une facture Core valide pour les tests DGI."""

    return InvoiceDTO(
        number=number,
        invoice_date=date(2026, 11, 20),
        seller=PartyInfoDTO(
            name="Vorpal Systems RDC",
            nif="NIF-VND-900",
            rccm="CD/LSH/RCCM/24-B-100",
            address="Lubumbashi",
        ),
        customer=PartyInfoDTO(
            name="Client Test",
            nif="NIF-CLI-001",
            rccm="CD/LSH/RCCM/20-A-012",
            address="Lubumbashi",
        ),
        lines=[
            InvoiceLineDTO(
                designation="Prestation reseau et securite",
                quantity=Decimal("2"),
                unit_price_ht=Decimal("100.00"),
                tax_group=tax_group,
                vat_rate=vat_rate,
                item_code="SRV-01",
            )
        ],
        currency=Currency.USD,
        total_ht=Decimal("200.00"),
        total_vat=Decimal("32.00"),
        total_ttc=Decimal("232.00"),
        invoice_type=invoice_type,
        original_invoice_ref=original_invoice_ref,
        non_taxable_amount=non_taxable_amount,
        other_taxes=other_taxes,
    )


def test_core_adapter_certifies_valid_invoice(
    certification_service: CertificationService,
) -> None:
    invoice = make_core_invoice()

    result = certification_service.certify(invoice)

    assert isinstance(result, CertifiedInvoiceDTO)
    assert result.number == invoice.number
    assert result.device_id == "MCF-RDC-01"
    assert result.auth_code.startswith("AUTH-")
    assert result.qr_code.startswith("DGI-RDC|")
    assert result.certified_at is not None
    assert result.counters == {}
    assert result.total_ttc == Decimal("232.00")


def test_core_adapter_is_idempotent_after_certification(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
) -> None:
    invoice = make_core_invoice(number="CORE-INV-IDEMPOTENT")

    first_result = certification_service.certify(invoice)
    second_result = certification_service.certify(invoice)

    assert first_result.auth_code == second_result.auth_code
    assert first_result.qr_code == second_result.qr_code
    assert first_result.device_id == second_result.device_id

    # Une seule soumission enregistrée pour ce numéro de facture.
    assert list(mock_client._submitted_by_key) == [
        "CORE-INV-IDEMPOTENT"
    ]


def test_core_adapter_rejects_unknown_tax_group(
    certification_service: CertificationService,
) -> None:
    invoice = make_core_invoice(tax_group="UNKNOWN-GROUP")

    with pytest.raises(ComplianceError):
        certification_service.certify(invoice)


def test_core_adapter_rejects_tax_rate_inconsistent_with_group(
    certification_service: CertificationService,
) -> None:
    invoice = make_core_invoice(vat_rate=Decimal("0.18"))

    with pytest.raises(ComplianceError, match="Incohérence entre le taux TVA"):
        certification_service.certify(invoice)


def test_core_adapter_rejects_credit_note(
    certification_service: CertificationService,
) -> None:
    invoice = make_core_invoice(
        number="CORE-CN-001",
        invoice_type="avoir",
        original_invoice_ref="CORE-INV-ORIGINAL",
    )

    with pytest.raises(ComplianceError, match="Certification d'avoir impossible"):
        certification_service.certify(invoice)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("non_taxable_amount", Decimal("10.00")),
        ("other_taxes", Decimal("5.00")),
    ],
)
def test_core_adapter_does_not_certify_incompatible_extra_amounts(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
    field: str,
    value: Decimal,
) -> None:
    invoice = make_core_invoice(**{field: value})

    with pytest.raises(ComplianceError, match="incompatible"):
        certification_service.certify(invoice)

    # Le refus doit intervenir avant tout contact avec le dispositif.
    assert mock_client._submitted_by_key == {}


def test_core_adapter_raises_when_device_is_temporarily_unavailable(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
) -> None:
    invoice = make_core_invoice(number="CORE-INV-OUTAGE")
    mock_client.simulate_outage(enabled=True)

    with pytest.raises(ComplianceError, match="Statut DGI : RETRY"):
        certification_service.certify(invoice)


def test_core_adapter_raises_when_device_rejects_invoice(
    certification_service: CertificationService,
    mock_client: MockMCFClient,
) -> None:
    invoice = make_core_invoice(number="CORE-INV-REJECT")
    mock_client.reject_serials.add(invoice.number)

    with pytest.raises(ComplianceError, match="Statut DGI : REJECTED"):
        certification_service.certify(invoice)


def test_core_adapter_rejects_default_unmapped_tax_group(
    certification_service: CertificationService,
) -> None:
    invoice = make_core_invoice()
    invoice.lines[0] = InvoiceLineDTO(
        designation="Prestation reseau et securite",
        quantity=Decimal("2"),
        unit_price_ht=Decimal("100.00"),
        vat_rate=Decimal("0.16"),
        item_code="SRV-01",
    )

    with pytest.raises(ComplianceError):
        certification_service.certify(invoice)


def test_core_adapter_implements_fiscal_certifier(
    certification_service: CertificationService,
) -> None:
    from vorpal.core.contracts import FiscalCertifier

    assert isinstance(certification_service, FiscalCertifier)
