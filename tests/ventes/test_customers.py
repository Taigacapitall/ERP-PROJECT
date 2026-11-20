"""Tests des entites domaine Customer, PaymentTerms et TaxIdValidator."""

import pytest

from vorpal.core import Currency, Partner
from vorpal.modules.ventes.domain.customers import (
    ContactPerson,
    Customer,
    PaymentTerms,
)
from vorpal.modules.ventes.domain.errors import SalesComplianceError, SalesValidationError


class CustomPrefixedNifValidator:
    def validate(self, nif: str | None) -> bool:
        return bool(nif and nif.startswith("A") and len(nif) == 9)


def test_customer_creation_and_invoicing_eligibility() -> None:
    partner = Partner(
        id="CUST-01",
        name="KATANGA MINING SUPPLY",
        partner_type="client",
        nif="A1234567B",
        rccm="CD/LSH/RCCM/20-B-001",
        address="Lubumbashi",
    )
    customer = Customer(
        partner=partner,
        contacts=(ContactPerson(name="Jean Mwamba", email="j.mwamba@kms.cd"),),
        payment_terms=PaymentTerms(days=45, label="Net 45 jours"),
        preferred_currency=Currency.USD,
    )
    assert customer.id == "CUST-01"
    assert customer.name == "KATANGA MINING SUPPLY"
    customer.ensure_eligible_for_invoicing()
    customer.ensure_eligible_for_invoicing(CustomPrefixedNifValidator())


def test_customer_without_nif_rejected_for_invoicing() -> None:
    partner = Partner(id="CUST-02", name="CLIENT SANS NIF", partner_type="client", nif=None)
    customer = Customer(partner=partner)
    with pytest.raises(SalesComplianceError):
        customer.ensure_eligible_for_invoicing()


def test_deactivated_customer_keeps_data_but_cannot_be_invoiced() -> None:
    partner = Partner(id="CUST-03", name="CLIENT INACTIF", partner_type="client", nif="A9999999Z")
    customer = Customer(partner=partner).deactivate()
    assert customer.is_active is False
    assert customer.name == "CLIENT INACTIF"
    with pytest.raises(SalesComplianceError):
        customer.ensure_eligible_for_invoicing()


def test_customer_rejects_non_client_partner() -> None:
    supplier = Partner(id="SUP-01", name="FOURNISSEUR", partner_type="fournisseur", nif="A1111111B")
    with pytest.raises(SalesValidationError):
        Customer(partner=supplier)
