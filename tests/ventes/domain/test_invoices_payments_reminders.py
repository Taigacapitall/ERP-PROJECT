from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from vorpal.core import Currency, Money, Partner
from vorpal.core.contracts import CertifiedInvoiceDTO, InvoiceLineDTO, PartyInfoDTO
from vorpal.modules.ventes.domain.customers import (
    ContactPerson,
    Customer,
    NonEmptyTaxIdValidator,
    PaymentTerms,
)
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.invoices import (
    CreditNote,
    InvoiceLine,
    InvoicePaymentStatus,
    InvoiceStatus,
    SalesInvoice,
)
from vorpal.modules.ventes.domain.payments import (
    Allocation,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from vorpal.modules.ventes.domain.reminders import (
    ReminderLevel,
    ReminderPolicy,
)
from vorpal.modules.ventes.settings import SellerSettings


def money(value: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(value), currency)


def customer(
    *,
    customer_id: str = "CUST-001",
    name: str = "Client Test",
    nif: str | None = "NIF-123",
    active: bool = True,
) -> Customer:
    return Customer(
        partner=Partner(
            id=customer_id,
            name=name,
            partner_type="client",
            nif=nif,
            rccm="RCCM-123",
            address="Lubumbashi",
        ),
        is_active=active,
    )


def invoice_line(
    *,
    quantity: str = "2",
    price: str = "100",
    currency: Currency = Currency.USD,
    discount: str = "0",
    vat: str = "0.16",
    out_of_scope: bool = False,
    item_code: str | None = "ITEM-001",
) -> InvoiceLine:
    return InvoiceLine(
        item_code=item_code,
        designation="Prestation informatique",
        quantity=Decimal(quantity),
        unit_price_ht=money(price, currency),
        discount_percent=Decimal(discount),
        vat_rate=Decimal(vat),
        is_out_of_scope=out_of_scope,
    )


def invoice(
    *,
    invoice_id: str = "INV-001",
    customer_id: str = "CUST-001",
    due_date: date = date(2026, 11, 10),
    status: InvoiceStatus = InvoiceStatus.DRAFT,
    invoice_type: str = "vente",
    original_invoice_id: str | None = None,
    currency: Currency = Currency.USD,
    lines: tuple[InvoiceLine, ...] | None = None,
) -> SalesInvoice:
    return SalesInvoice(
        id=invoice_id,
        customer_id=customer_id,
        invoice_date=date(2026, 11, 1),
        due_date=due_date,
        currency=currency,
        lines=lines if lines is not None else (invoice_line(currency=currency),),
        invoice_type=invoice_type,  # type: ignore[arg-type]
        status=status,
        original_invoice_id=original_invoice_id,
    )


def levels() -> list[ReminderLevel]:
    return [
        ReminderLevel("R1", 5, "EMAIL", "Facture {invoice_number}, retard {days_overdue} jours"),
        ReminderLevel("R2", 15, "SMS", "Solde {balance_due} {currency}, échéance {due_date}"),
        ReminderLevel("R3", 30, "EMAIL", "Dernière relance {invoice_number}"),
    ]


# ---------------------------------------------------------------------------
# CUSTOMERS
# ---------------------------------------------------------------------------

class TestCustomerEntities:
    def test_contact_requires_name(self) -> None:
        with pytest.raises(SalesValidationError):
            ContactPerson(name=" ")

    def test_contact_accepts_optional_fields(self) -> None:
        contact = ContactPerson("Josué", "j@example.com", "+243000", "Manager")
        assert contact.name == "Josué"
        assert contact.email == "j@example.com"

    def test_payment_terms_reject_negative_days(self) -> None:
        with pytest.raises(SalesValidationError):
            PaymentTerms(days=-1)

    def test_customer_requires_client_partner(self) -> None:
        partner = Partner("P-1", "Fournisseur", "fournisseur")
        with pytest.raises(SalesValidationError):
            Customer(partner=partner)

    def test_customer_requires_partner_name(self) -> None:
        partner = Partner("P-1", " ", "client")
        with pytest.raises(SalesValidationError):
            Customer(partner=partner)

    def test_customer_properties(self) -> None:
        client = customer()
        assert client.id == "CUST-001"
        assert client.name == "Client Test"
        assert client.nif == "NIF-123"
        assert client.rccm == "RCCM-123"
        assert client.address == "Lubumbashi"

    def test_customer_activation_and_deactivation(self) -> None:
        client = customer()
        inactive = client.deactivate()
        assert inactive.is_active is False
        assert client.is_active is True
        assert inactive.activate().is_active is True

    def test_default_tax_validator(self) -> None:
        validator = NonEmptyTaxIdValidator()
        assert validator.validate("NIF-123") is True
        assert validator.validate(None) is False
        assert validator.validate(" ") is False

    def test_inactive_customer_cannot_be_invoiced(self) -> None:
        with pytest.raises(SalesComplianceError):
            customer(active=False).ensure_eligible_for_invoicing()

    def test_customer_without_nif_cannot_be_invoiced(self) -> None:
        with pytest.raises(SalesComplianceError):
            customer(nif=None).ensure_eligible_for_invoicing()

    def test_custom_tax_validator_is_used(self) -> None:
        class RejectAll:
            def validate(self, nif: str | None) -> bool:
                return False

        with pytest.raises(SalesComplianceError):
            customer().ensure_eligible_for_invoicing(RejectAll())

    def test_customer_with_valid_nif_is_eligible(self) -> None:
        customer().ensure_eligible_for_invoicing()


# ---------------------------------------------------------------------------
# INVOICE LINES AND FISCAL RULES
# ---------------------------------------------------------------------------

class TestInvoiceLine:
    def test_rejects_blank_designation(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice_line(), designation=" ")

    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_rejects_non_positive_quantity(self, quantity: str) -> None:
        with pytest.raises(SalesValidationError):
            invoice_line(quantity=quantity)

    @pytest.mark.parametrize("discount", ["-1", "100.1"])
    def test_rejects_invalid_discount(self, discount: str) -> None:
        with pytest.raises(SalesValidationError):
            invoice_line(discount=discount)

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice_line(price="-1")

    def test_rejects_negative_vat(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice_line(vat="-0.01")

    def test_rejects_float_quantity(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice_line(), quantity=1.5)  # type: ignore[arg-type]

    def test_out_of_scope_requires_item_code(self) -> None:
        line = invoice_line(out_of_scope=True, item_code=None)
        with pytest.raises(SalesComplianceError):
            line.check_fiscal_rules()

    def test_out_of_scope_accepts_item_code(self) -> None:
        invoice_line(out_of_scope=True).check_fiscal_rules()

    def test_to_dto_applies_discount_to_net_unit_price(self) -> None:
        line = invoice_line(quantity="2", price="100", discount="10")
        dto = line.to_dto()
        assert dto.designation == line.designation
        assert dto.quantity == Decimal("2")
        assert dto.unit_price_ht == Decimal("90.00")
        assert dto.item_code == "ITEM-001"

    def test_to_dto_rejects_invalid_out_of_scope_line(self) -> None:
        line = invoice_line(out_of_scope=True, item_code=None)
        with pytest.raises(SalesComplianceError):
            line.to_dto()


# ---------------------------------------------------------------------------
# SALES INVOICES AND CREDIT NOTES
# ---------------------------------------------------------------------------

class TestSalesInvoice:
    def test_credit_note_alias(self) -> None:
        assert CreditNote is SalesInvoice

    def test_rejects_blank_invoice_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice(), id=" ")

    def test_rejects_blank_customer_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice(), customer_id=" ")

    def test_rejects_due_date_before_invoice_date(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice(), due_date=date(2026, 10, 31))

    def test_rejects_empty_lines(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice(), lines=())

    def test_rejects_line_currency_mismatch(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(invoice(), lines=(invoice_line(currency=Currency.CDF),))

    def test_credit_note_requires_original_invoice(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice(invoice_type="avoir")

    def test_effective_totals_are_computed_in_draft(self) -> None:
        inv = invoice()
        assert inv.effective_total_ttc == money("232.00")
        assert inv.effective_paid_amount == money("0.00")
        assert inv.balance_due == money("232.00")

    @pytest.mark.parametrize(
        ("paid", "expected"),
        [
            ("0", InvoicePaymentStatus.UNPAID),
            ("50", InvoicePaymentStatus.PARTIALLY_PAID),
            ("232", InvoicePaymentStatus.PAID),
            ("250", InvoicePaymentStatus.OVERPAID),
        ],
    )
    def test_payment_status(self, paid: str, expected: InvoicePaymentStatus) -> None:
        inv = replace(invoice(), paid_amount=money(paid))
        assert inv.payment_status == expected

    def test_balance_due_uses_frozen_total_when_available(self) -> None:
        inv = replace(
            invoice(),
            total_ttc=money("300"),
            paid_amount=money("50"),
        )
        assert inv.balance_due == money("250")

    def test_update_lines_in_draft(self) -> None:
        inv = invoice()
        new_lines = (invoice_line(quantity="1", price="50"),)
        updated = inv.update_lines(new_lines)
        assert updated.lines == new_lines
        assert inv.lines != new_lines

    @pytest.mark.parametrize("status", [InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED])
    def test_update_lines_for_fiscally_locked_invoice(self, status: InvoiceStatus) -> None:
        with pytest.raises(SalesComplianceError):
            replace(invoice(), status=status).update_lines((invoice_line(),))

    def test_update_lines_rejected_invoice_requires_reopening(self) -> None:
        with pytest.raises(SalesConflictError):
            replace(invoice(), status=InvoiceStatus.REJECTED).update_lines((invoice_line(),))

    def test_validate_invoice(self) -> None:
        result = invoice().validate(
            customer=customer(),
            assigned_number="FAC-001",
        )
        assert result.status == InvoiceStatus.VALIDATED
        assert result.number == "FAC-001"
        assert result.total_ht == money("200.00")
        assert result.total_vat == money("32.00")
        assert result.total_ttc == money("232.00")
        assert result.paid_amount == money("0.00")

    def test_validate_preserves_existing_number(self) -> None:
        inv = replace(invoice(), number="FAC-EXISTING")
        result = inv.validate(customer=customer(), assigned_number="FAC-NEW")
        assert result.number == "FAC-EXISTING"

    def test_validate_rejects_wrong_customer(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice().validate(customer=customer(customer_id="OTHER"), assigned_number="FAC-1")

    def test_validate_rejects_blank_assigned_number(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice().validate(customer=customer(), assigned_number=" ")

    def test_validate_rejects_inactive_customer(self) -> None:
        with pytest.raises(SalesComplianceError):
            invoice().validate(customer=customer(active=False), assigned_number="FAC-1")

    def test_validate_rejects_customer_without_nif(self) -> None:
        with pytest.raises(SalesComplianceError):
            invoice().validate(customer=customer(nif=None), assigned_number="FAC-1")

    def test_validate_rejects_non_draft_invoice(self) -> None:
        inv = replace(invoice(), status=InvoiceStatus.VALIDATED)
        with pytest.raises(SalesConflictError):
            inv.validate(customer=customer(), assigned_number="FAC-1")

    def test_validate_checks_fiscal_rules(self) -> None:
        inv = invoice(lines=(invoice_line(out_of_scope=True, item_code=None),))
        with pytest.raises(SalesComplianceError):
            inv.validate(customer=customer(), assigned_number="FAC-1")

    def test_validate_accepts_injected_tax_validator(self) -> None:
        class RejectAll:
            def validate(self, nif: str | None) -> bool:
                return False

        with pytest.raises(SalesComplianceError):
            invoice().validate(
                customer=customer(),
                assigned_number="FAC-1",
                tax_id_validator=RejectAll(),
            )

    def test_validate_credit_note(self) -> None:
        original = replace(
            invoice(),
            status=InvoiceStatus.CERTIFIED,
            number="FAC-ORIGINAL",
            total_ttc=money("500"),
        )
        credit = invoice(
            invoice_id="AV-001",
            invoice_type="avoir",
            original_invoice_id=original.id,
            lines=(invoice_line(quantity="1", price="100"),),
        )
        result = credit.validate(
            customer=customer(),
            assigned_number="AV-001",
            original_invoice=original,
        )
        assert result.status == InvoiceStatus.VALIDATED
        assert result.original_invoice_number == "FAC-ORIGINAL"

    def test_credit_note_requires_original_argument(self) -> None:
        credit = invoice(invoice_type="avoir", original_invoice_id="INV-ORIGINAL")
        with pytest.raises(SalesValidationError):
            credit.validate(customer=customer(), assigned_number="AV-1")

    def test_credit_note_rejects_credit_of_credit(self) -> None:
        original = replace(
            invoice(invoice_type="avoir", original_invoice_id="INV-0"),
            status=InvoiceStatus.CERTIFIED,
            number="AV-ORIGINAL",
        )
        credit = invoice(invoice_type="avoir", original_invoice_id=original.id)
        with pytest.raises(SalesComplianceError):
            credit.validate(
                customer=customer(),
                assigned_number="AV-2",
                original_invoice=original,
            )

    def test_credit_note_requires_certified_original(self) -> None:
        with pytest.raises(SalesComplianceError):
            invoice(invoice_type="avoir", original_invoice_id="INV-ORIGINAL").validate(
                customer=customer(),
                assigned_number="AV-1",
                original_invoice=invoice(),
            )

    def test_credit_note_requires_same_currency(self) -> None:
        original = replace(
            invoice(currency=Currency.USD),
            status=InvoiceStatus.CERTIFIED,
            number="FAC-1",
        )
        credit = invoice(
            invoice_type="avoir",
            original_invoice_id=original.id,
            currency=Currency.CDF,
        )
        with pytest.raises(SalesValidationError):
            credit.validate(
                customer=customer(),
                assigned_number="AV-1",
                original_invoice=original,
            )

    def test_credit_note_requires_official_original_number(self) -> None:
        original = replace(invoice(), status=InvoiceStatus.CERTIFIED, number=None)
        credit = invoice(invoice_type="avoir", original_invoice_id=original.id)
        with pytest.raises(SalesComplianceError):
            credit.validate(
                customer=customer(),
                assigned_number="AV-1",
                original_invoice=original,
            )

    def test_credit_note_cannot_exceed_original_total(self) -> None:
        original = replace(
            invoice(),
            status=InvoiceStatus.CERTIFIED,
            number="FAC-1",
            total_ttc=money("100"),
        )
        credit = invoice(
            invoice_type="avoir",
            original_invoice_id=original.id,
            lines=(invoice_line(quantity="2", price="100"),),
        )
        with pytest.raises(SalesComplianceError):
            credit.validate(
                customer=customer(),
                assigned_number="AV-1",
                original_invoice=original,
            )

    def test_credit_note_includes_previous_credits_in_limit(self) -> None:
        original = replace(
            invoice(),
            status=InvoiceStatus.CERTIFIED,
            number="FAC-1",
            total_ttc=money("300"),
        )
        credit = invoice(
            invoice_type="avoir",
            original_invoice_id=original.id,
            lines=(invoice_line(quantity="1", price="100"),),
        )
        with pytest.raises(SalesComplianceError):
            credit.validate(
                customer=customer(),
                assigned_number="AV-1",
                original_invoice=original,
                already_credited_ttc=money("200"),
            )

    def test_to_invoice_dto_requires_validated_status(self) -> None:
        with pytest.raises(SalesConflictError):
            invoice().to_invoice_dto(
                customer(),
                SellerSettings(name="Seller", nif="NIF-SELLER"),
            )

    def test_to_invoice_dto_requires_number(self) -> None:
        inv = replace(
            invoice(),
            status=InvoiceStatus.VALIDATED,
            total_ht=money("200"),
            total_vat=money("32"),
            total_ttc=money("232"),
        )
        with pytest.raises(SalesValidationError):
            inv.to_invoice_dto(customer(), SellerSettings(name="Seller", nif="NIF"))

    def test_to_invoice_dto_requires_totals(self) -> None:
        inv = replace(invoice(), status=InvoiceStatus.VALIDATED, number="FAC-1")
        with pytest.raises(SalesValidationError):
            inv.to_invoice_dto(customer(), SellerSettings(name="Seller", nif="NIF"))

    def test_to_invoice_dto_builds_core_contract(self) -> None:
        inv = invoice().validate(customer=customer(), assigned_number="FAC-1")
        dto = inv.to_invoice_dto(
            customer(),
            SellerSettings(
                name="Vorpal",
                nif="NIF-SELLER",
                rccm="RCCM-SELLER",
                address="Lubumbashi",
            ),
        )
        assert dto.number == "FAC-1"
        assert dto.seller.name == "Vorpal"
        assert dto.customer.name == "Client Test"
        assert dto.total_ttc == Decimal("232.00")
        assert len(dto.lines) == 1

    def test_mark_certified_requires_validated_status(self) -> None:
        dto = CertifiedInvoiceDTO(
            number="FAC-1",
            invoice_date=date(2026, 11, 1),
            seller=PartyInfoDTO(name="Seller"),
            customer=PartyInfoDTO(name="Customer"),
            lines=[
                InvoiceLineDTO(
                    designation="Service",
                    quantity=Decimal("2"),
                    unit_price_ht=Decimal("100"),
                    vat_rate=Decimal("0.16"),
                )
            ],
            currency=Currency.USD,
            total_ht=Decimal("200"),
            total_vat=Decimal("32"),
            total_ttc=Decimal("232"),
            device_id="DEVICE-1",
            auth_code="AUTH-1",
            qr_code="QR-1",
            certified_at=datetime(2026, 11, 2, 10, 0),
            counters={"invoice": 1},
        )
        with pytest.raises(SalesConflictError):
            invoice().mark_certified(dto)

    def test_mark_certified_sets_fiscal_stamp(self) -> None:
        dto = CertifiedInvoiceDTO(
            number="FAC-1",
            invoice_date=date(2026, 11, 1),
            seller=PartyInfoDTO(name="Seller"),
            customer=PartyInfoDTO(name="Customer"),
            lines=[
                InvoiceLineDTO(
                    designation="Service",
                    quantity=Decimal("2"),
                    unit_price_ht=Decimal("100"),
                    vat_rate=Decimal("0.16"),
                )
            ],
            currency=Currency.USD,
            total_ht=Decimal("200"),
            total_vat=Decimal("32"),
            total_ttc=Decimal("232"),
            device_id="DEVICE-1",
            auth_code="AUTH-1",
            qr_code="QR-1",
            certified_at=datetime(2026, 11, 2, 10, 0),
            counters={"invoice": 1},
        )
        validated = invoice().validate(customer=customer(), assigned_number="FAC-1")
        certified = validated.mark_certified(dto)
        assert certified.status == InvoiceStatus.CERTIFIED
        assert certified.fiscal_stamp is not None
        assert certified.fiscal_stamp.device_id == "DEVICE-1"
        assert certified.fiscal_stamp.counters["invoice"] == 1
        assert validated.status == InvoiceStatus.VALIDATED

    def test_mark_certified_is_idempotent(self) -> None:
        dto = CertifiedInvoiceDTO(
            number="FAC-1",
            invoice_date=date(2026, 11, 1),
            seller=PartyInfoDTO(name="Seller"),
            customer=PartyInfoDTO(name="Customer"),
            lines=[
                InvoiceLineDTO(
                    designation="Service",
                    quantity=Decimal("2"),
                    unit_price_ht=Decimal("100"),
                    vat_rate=Decimal("0.16"),
                )
            ],
            currency=Currency.USD,
            total_ht=Decimal("200"),
            total_vat=Decimal("32"),
            total_ttc=Decimal("232"),
            device_id="DEVICE-1",
            auth_code="AUTH-1",
            qr_code="QR-1",
            certified_at=datetime(2026, 11, 2, 10, 0),
            counters={"invoice": 1},
        )
        certified = invoice().validate(customer=customer(), assigned_number="FAC-1").mark_certified(dto)
        assert certified.mark_certified(dto) is certified

    def test_mark_rejected_requires_validated_status(self) -> None:
        with pytest.raises(SalesConflictError):
            invoice().mark_rejected("Erreur DGI")

    def test_mark_rejected_cleans_reason(self) -> None:
        validated = replace(invoice(), status=InvoiceStatus.VALIDATED)
        rejected = validated.mark_rejected("  NIF invalide  ")
        assert rejected.status == InvoiceStatus.REJECTED
        assert rejected.rejection_reason == "NIF invalide"

    def test_mark_rejected_uses_default_reason(self) -> None:
        validated = replace(invoice(), status=InvoiceStatus.VALIDATED)
        assert validated.mark_rejected(" ").rejection_reason == "Rejet DGI sans motif detaille"

    def test_mark_rejected_is_idempotent(self) -> None:
        rejected = replace(invoice(), status=InvoiceStatus.REJECTED)
        assert rejected.mark_rejected("Autre motif") is rejected

    def test_reopen_requires_rejected_status(self) -> None:
        with pytest.raises(SalesConflictError):
            invoice().reopen_after_rejection()

    def test_reopen_preserves_number_by_default(self) -> None:
        rejected = replace(
            invoice(),
            status=InvoiceStatus.REJECTED,
            number="FAC-1",
            total_ht=money("200"),
            total_vat=money("32"),
            total_ttc=money("232"),
        )
        reopened = rejected.reopen_after_rejection()
        assert reopened.status == InvoiceStatus.DRAFT
        assert reopened.number == "FAC-1"
        assert reopened.total_ht is None
        assert reopened.total_vat is None
        assert reopened.total_ttc is None

    def test_reopen_can_discard_number(self) -> None:
        rejected = replace(invoice(), status=InvoiceStatus.REJECTED, number="FAC-1")
        assert rejected.reopen_after_rejection(preserve_number=False).number is None

    def test_apply_paid_amount(self) -> None:
        updated = invoice().apply_paid_amount(money("50"))
        assert updated.paid_amount == money("50")

    def test_apply_paid_amount_rejects_wrong_currency(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice().apply_paid_amount(money("50", Currency.CDF))

    def test_apply_paid_amount_rejects_negative_amount(self) -> None:
        with pytest.raises(SalesValidationError):
            invoice().apply_paid_amount(money("-1"))


# ---------------------------------------------------------------------------
# PAYMENTS AND ALLOCATIONS
# ---------------------------------------------------------------------------

def allocation(
    *,
    payment_id: str = "PAY-1",
    invoice_id: str = "INV-1",
    amount: str = "50",
    currency: Currency = Currency.USD,
) -> Allocation:
    return Allocation(
        id="ALLOC-1",
        payment_id=payment_id,
        invoice_id=invoice_id,
        amount=money(amount, currency),
        allocated_at=date(2026, 11, 5),
    )


def payment(
    *,
    amount: str = "100",
    method: PaymentMethod = PaymentMethod.CASH,
    reference: str | None = None,
    allocations: tuple[Allocation, ...] = (),
) -> Payment:
    return Payment(
        id="PAY-1",
        customer_id="CUST-001",
        amount=money(amount),
        payment_date=date(2026, 11, 5),
        method=method,
        reference=reference,
        allocations=allocations,
    )


class TestAllocation:
    def test_rejects_blank_allocation_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(allocation(), id=" ")

    def test_rejects_blank_payment_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(allocation(), payment_id=" ")

    def test_rejects_blank_invoice_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(allocation(), invoice_id=" ")

    def test_rejects_zero_amount(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(allocation(), amount=money("0"))

    def test_rejects_negative_amount(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(allocation(), amount=money("-1"))


class TestPayment:
    def test_rejects_blank_payment_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(payment(), id=" ")

    def test_rejects_blank_customer_id(self) -> None:
        with pytest.raises(SalesValidationError):
            replace(payment(), customer_id=" ")

    def test_rejects_non_positive_amount(self) -> None:
        with pytest.raises(SalesValidationError):
            payment(amount="0")

    @pytest.mark.parametrize(
        "method",
        [PaymentMethod.BANK_TRANSFER, PaymentMethod.MOBILE_MONEY],
    )
    def test_transfer_methods_require_reference(self, method: PaymentMethod) -> None:
        with pytest.raises(SalesValidationError):
            payment(method=method)

    def test_cash_does_not_require_reference(self) -> None:
        assert payment(method=PaymentMethod.CASH).reference is None

    def test_transfer_accepts_reference(self) -> None:
        result = payment(method=PaymentMethod.BANK_TRANSFER, reference="TX-123")
        assert result.reference == "TX-123"

    def test_allocation_must_belong_to_payment(self) -> None:
        with pytest.raises(SalesValidationError):
            payment(allocations=(allocation(payment_id="OTHER"),))

    def test_allocation_currency_must_match_payment(self) -> None:
        with pytest.raises(SalesValidationError):
            payment(
                allocations=(allocation(currency=Currency.CDF),)
            )

    def test_currency_property(self) -> None:
        assert payment().currency == Currency.USD

    def test_allocated_and_unallocated_amounts(self) -> None:
        result = payment(allocations=(allocation(amount="30"),))
        assert result.allocated_amount == money("30")
        assert result.unallocated_amount == money("70")

    def test_allocate_payment(self) -> None:
        result = payment().allocate(allocation(amount="40"), money("100"))
        assert len(result.allocations) == 1
        assert result.allocated_amount == money("40")
        assert result.unallocated_amount == money("60")

    def test_allocate_rejects_cancelled_payment(self) -> None:
        cancelled, _ = payment().cancel()
        with pytest.raises(SalesConflictError):
            cancelled.allocate(allocation(), money("100"))

    def test_allocate_rejects_allocation_for_another_payment(self) -> None:
        with pytest.raises(SalesValidationError):
            payment().allocate(allocation(payment_id="OTHER"), money("100"))

    def test_allocate_rejects_wrong_allocation_currency(self) -> None:
        with pytest.raises(SalesValidationError):
            payment().allocate(
                allocation(currency=Currency.CDF),
                money("100"),
            )

    def test_allocate_rejects_wrong_invoice_currency(self) -> None:
        with pytest.raises(SalesValidationError):
            payment().allocate(
                allocation(),
                money("100", Currency.CDF),
            )

    def test_allocate_rejects_amount_above_unallocated_balance(self) -> None:
        current = payment(allocations=(allocation(amount="80"),))
        with pytest.raises(SalesConflictError):
            current.allocate(
                replace(allocation(), id="ALLOC-2", amount=money("30")),
                money("100"),
            )

    def test_allocate_rejects_amount_above_invoice_balance(self) -> None:
        with pytest.raises(SalesConflictError):
            payment().allocate(allocation(amount="80"), money("50"))

    def test_cancel_releases_allocations(self) -> None:
        current = payment(allocations=(allocation(amount="30"),))
        cancelled, released = current.cancel()
        assert cancelled.status == PaymentStatus.CANCELLED
        assert cancelled.allocations == ()
        assert released == current.allocations
        assert current.status == PaymentStatus.RECORDED

    def test_cancel_rejects_already_cancelled_payment(self) -> None:
        cancelled, _ = payment().cancel()
        with pytest.raises(SalesConflictError):
            cancelled.cancel()


# ---------------------------------------------------------------------------
# REMINDER POLICY
# ---------------------------------------------------------------------------

class TestReminderLevel:
    def test_rejects_blank_code(self) -> None:
        with pytest.raises(SalesValidationError):
            ReminderLevel(" ", 5, "EMAIL", "Message")

    def test_rejects_delay_below_one_day(self) -> None:
        with pytest.raises(SalesValidationError):
            ReminderLevel("R1", 0, "EMAIL", "Message")

    def test_rejects_blank_channel(self) -> None:
        with pytest.raises(SalesValidationError):
            ReminderLevel("R1", 5, " ", "Message")

    def test_rejects_blank_template(self) -> None:
        with pytest.raises(SalesValidationError):
            ReminderLevel("R1", 5, "EMAIL", " ")


class TestReminderPolicy:
    def test_rejects_duplicate_level_codes(self) -> None:
        with pytest.raises(SalesValidationError):
            ReminderPolicy([
                ReminderLevel("R1", 5, "EMAIL", "A"),
                ReminderLevel("R1", 10, "SMS", "B"),
            ])

    def test_sorts_levels_by_delay(self) -> None:
        policy = ReminderPolicy(list(reversed(levels())))
        assert [level.days_after_due for level in policy.levels] == [5, 15, 30]

    def test_days_overdue_returns_zero_before_due_date(self) -> None:
        policy = ReminderPolicy(levels())
        assert policy.days_overdue(invoice(), date(2026, 11, 10)) == 0
        assert policy.days_overdue(invoice(), date(2026, 11, 9)) == 0

    def test_days_overdue_returns_elapsed_days(self) -> None:
        policy = ReminderPolicy(levels())
        assert policy.days_overdue(invoice(), date(2026, 11, 20)) == 10

    def test_invoice_is_not_eligible_before_due_date(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(invoice(), status=InvoiceStatus.VALIDATED)
        assert not policy.is_eligible(inv, date(2026, 11, 10))

    def test_invoice_is_not_eligible_when_draft(self) -> None:
        policy = ReminderPolicy(levels())
        assert not policy.is_eligible(invoice(), date(2026, 11, 20))

    def test_invoice_is_not_eligible_when_credit_note(self) -> None:
        policy = ReminderPolicy(levels())
        credit = invoice(
            invoice_type="avoir",
            original_invoice_id="INV-ORIGINAL",
            status=InvoiceStatus.VALIDATED,
        )
        assert not policy.is_eligible(credit, date(2026, 11, 20))

    @pytest.mark.parametrize("status", [InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED])
    def test_validated_or_certified_overdue_invoice_is_eligible(
        self,
        status: InvoiceStatus,
    ) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(invoice(), status=status)
        assert policy.is_eligible(inv, date(2026, 11, 20))

    @pytest.mark.parametrize("paid", ["232", "250"])
    def test_fully_paid_or_overpaid_invoice_is_not_eligible(self, paid: str) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(
            invoice(),
            status=InvoiceStatus.VALIDATED,
            paid_amount=money(paid),
        )
        assert not policy.is_eligible(inv, date(2026, 11, 20))

    def test_partially_paid_invoice_remains_eligible(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(
            invoice(),
            status=InvoiceStatus.VALIDATED,
            paid_amount=money("50"),
        )
        assert policy.is_eligible(inv, date(2026, 11, 20))

    def test_applicable_level_is_none_before_first_threshold(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(invoice(), status=InvoiceStatus.VALIDATED)
        assert policy.applicable_level(inv, date(2026, 11, 12)) is None


    def test_applicable_level_returns_none_for_paid_invoice(self) -> None:
        policy = ReminderPolicy(levels())
        paid_invoice = replace(
            invoice(),
            status=InvoiceStatus.VALIDATED,
            paid_amount=money("232"),
        )
        assert policy.applicable_level(
            paid_invoice,
            date(2026, 11, 20),
        ) is None

    def test_applicable_level_selects_highest_reached_threshold(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(invoice(), status=InvoiceStatus.VALIDATED)
        selected = policy.applicable_level(inv, date(2026, 12, 10))
        assert selected is not None
        assert selected.code == "R3"

    def test_render_message_uses_invoice_values(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(
            invoice(),
            status=InvoiceStatus.VALIDATED,
            number="FAC-2026-001",
            paid_amount=money("50"),
        )
        message = policy.render_message(
            levels()[0],
            inv,
            date(2026, 11, 20),
        )
        assert "FAC-2026-001" in message
        assert "10" in message

    def test_render_message_falls_back_to_invoice_id(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(invoice(), number=None)
        message = policy.render_message(levels()[0], inv, date(2026, 11, 20))
        assert "INV-001" in message

    def test_summarize_overdue_customers_groups_invoices(self) -> None:
        policy = ReminderPolicy(levels())
        first = replace(
            invoice(invoice_id="INV-1"),
            status=InvoiceStatus.VALIDATED,
        )
        second = replace(
            invoice(invoice_id="INV-2"),
            status=InvoiceStatus.CERTIFIED,
            due_date=date(2026, 11, 5),
        )
        summaries = policy.summarize_overdue_customers(
            [first, second],
            {"CUST-001": "Client Test"},
            date(2026, 11, 20),
        )
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.customer_name == "Client Test"
        assert summary.overdue_invoice_count == 2
        assert summary.total_overdue_amount == money("464.00")
        assert summary.max_days_overdue == 15

    def test_summary_uses_customer_id_when_name_is_missing(self) -> None:
        policy = ReminderPolicy(levels())
        inv = replace(invoice(), status=InvoiceStatus.VALIDATED)
        summaries = policy.summarize_overdue_customers(
            [inv],
            {},
            date(2026, 11, 20),
        )
        assert summaries[0].customer_name == "CUST-001"

    def test_summary_separates_currencies(self) -> None:
        policy = ReminderPolicy(levels())
        usd = replace(invoice(), status=InvoiceStatus.VALIDATED)
        cdf = replace(
            invoice(
                invoice_id="INV-CDF",
                currency=Currency.CDF,
            ),
            status=InvoiceStatus.VALIDATED,
        )
        summaries = policy.summarize_overdue_customers(
            [usd, cdf],
            {},
            date(2026, 11, 20),
        )
        assert len(summaries) == 2
        assert {summary.currency for summary in summaries} == {
            Currency.USD,
            Currency.CDF,
        }

    def test_summary_excludes_ineligible_invoices(self) -> None:
        policy = ReminderPolicy(levels())
        draft = invoice()
        paid = replace(
            invoice(invoice_id="INV-PAID"),
            status=InvoiceStatus.VALIDATED,
            paid_amount=money("232"),
        )
        summaries = policy.summarize_overdue_customers(
            [draft, paid],
            {},
            date(2026, 11, 20),
        )
        assert summaries == []

    def test_empty_summary_returns_empty_list(self) -> None:
        policy = ReminderPolicy(levels())
        assert policy.summarize_overdue_customers([], {}, date(2026, 11, 20)) == []
