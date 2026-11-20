from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from vorpal.core.money import Currency


class PartyInfoDTO(BaseModel):
    name: str = Field(min_length=1)
    nif: str | None = None
    rccm: str | None = None
    address: str | None = None


class InvoiceLineDTO(BaseModel):
    designation: str = Field(min_length=1)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit_price_ht: Decimal = Field(ge=Decimal("0"))
    tax_group: str = Field(default="A", min_length=1)
    vat_rate: Decimal = Field(ge=Decimal("0"))


class InvoiceDTO(BaseModel):
    number: str = Field(min_length=1)
    invoice_date: date
    seller: PartyInfoDTO
    customer: PartyInfoDTO
    lines: list[InvoiceLineDTO] = Field(min_length=1)
    currency: Currency
    total_ht: Decimal = Field(ge=Decimal("0"))
    total_vat: Decimal = Field(ge=Decimal("0"))
    total_ttc: Decimal = Field(ge=Decimal("0"))
    invoice_type: Literal["vente", "avoir"] = "vente"
    original_invoice_ref: str | None = None

    @model_validator(mode="after")
    def validate_credit_note_ref(self) -> InvoiceDTO:
        if self.invoice_type == "avoir" and not self.original_invoice_ref:
            raise ValueError("Un avoir (invoice_type='avoir') exige original_invoice_ref.")
        return self


class CertifiedInvoiceDTO(InvoiceDTO):
    device_id: str = Field(min_length=1)
    auth_code: str = Field(min_length=1)
    qr_code: str = Field(min_length=1)
    certified_at: datetime
    counters: dict[str, str | int | Decimal] = Field(default_factory=dict)


class JournalLineDTO(BaseModel):
    account: str = Field(min_length=1)
    debit: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    credit: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    currency: Currency

    @model_validator(mode="after")
    def validate_line_side(self) -> JournalLineDTO:
        if self.debit > Decimal("0") and self.credit > Decimal("0"):
            raise ValueError("Une ligne d'écriture ne peut avoir à la fois un débit et un crédit > 0.")
        if self.debit == Decimal("0") and self.credit == Decimal("0"):
            raise ValueError("Une ligne d'écriture doit avoir un débit ou un crédit > 0.")
        return self


class JournalEntryDTO(BaseModel):
    entry_date: date
    journal: str = Field(min_length=1)
    label: str = Field(min_length=1)
    lines: list[JournalLineDTO] = Field(min_length=2)
    source_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_balanced(self) -> JournalEntryDTO:
        total_debit = sum((line.debit for line in self.lines), Decimal("0"))
        total_credit = sum((line.credit for line in self.lines), Decimal("0"))
        if total_debit != total_credit:
            raise ValueError(f"Écriture déséquilibrée: débit={total_debit} != crédit={total_credit}.")
        return self


class PayrollResultDTO(BaseModel):
    period: str = Field(min_length=1)
    employee_id: str = Field(min_length=1)
    gross_pay: Decimal = Field(ge=Decimal("0"))
    deductions: Decimal = Field(ge=Decimal("0"))
    net_pay: Decimal = Field(ge=Decimal("0"))
    employer_charges: Decimal = Field(ge=Decimal("0"))
    breakdown: dict[str, Decimal] = Field(default_factory=dict)
