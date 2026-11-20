from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from vorpal.core.money import Currency


def _reject_float_and_bool(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError("float et bool interdits pour les montants; utiliser Decimal, int ou str.")  # noqa: TRY004  (pydantic exige ValueError)
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float_and_bool)]

_CENT = Decimal("0.01")


class PartyInfoDTO(BaseModel):
    name: str = Field(min_length=1)
    nif: str | None = None
    rccm: str | None = None
    address: str | None = None


class InvoiceLineDTO(BaseModel):
    designation: str = Field(min_length=1)
    quantity: StrictDecimal = Field(gt=Decimal(0))
    unit_price_ht: StrictDecimal = Field(ge=Decimal(0))
    tax_group: str = Field(default="A", min_length=1)
    vat_rate: StrictDecimal = Field(ge=Decimal(0))
    # Code article : exige par la DGI pour les lignes "hors champ" (controle dans le module dgi).
    item_code: str | None = Field(default=None, min_length=1)


class InvoiceDTO(BaseModel):
    number: str = Field(min_length=1)
    invoice_date: date
    seller: PartyInfoDTO
    customer: PartyInfoDTO
    lines: list[InvoiceLineDTO] = Field(min_length=1)
    currency: Currency
    total_ht: StrictDecimal = Field(ge=Decimal(0))
    total_vat: StrictDecimal = Field(ge=Decimal(0))
    total_ttc: StrictDecimal = Field(ge=Decimal(0))
    invoice_type: Literal["vente", "avoir"] = "vente"
    original_invoice_ref: str | None = None
    # Mentions obligatoires DGI portees par le contrat (simples porteurs, defaut 0).
    # TODO(A_VALIDER): lien exact avec total_ht / total_ttc (spec DGI 02, regle fiscale).
    non_taxable_amount: StrictDecimal = Field(default=Decimal(0), ge=Decimal(0))
    other_taxes: StrictDecimal = Field(default=Decimal(0), ge=Decimal(0))

    @model_validator(mode="after")
    def validate_totals(self) -> InvoiceDTO:
        if self.total_ht + self.total_vat != self.total_ttc:
            raise ValueError("Totaux incohérents: total_ttc doit être égal à total_ht + total_vat.")
        # TODO(A_VALIDER): recouper total_vat avec la somme des (HT ligne x vat_rate),
        # selon la regle d'arrondi de la DGI (module dgi).
        lines_ht = sum((ln.quantity * ln.unit_price_ht for ln in self.lines), Decimal(0))
        if abs(lines_ht - self.total_ht) > _CENT * len(self.lines):
            raise ValueError("Totaux incohérents: total_ht diffère de la somme des lignes.")
        return self

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
    debit: StrictDecimal = Field(default=Decimal(0), ge=Decimal(0))
    credit: StrictDecimal = Field(default=Decimal(0), ge=Decimal(0))
    currency: Currency
    # TODO(A_VALIDER) etape 3 (spec 03) : contre-valeur en devise societe / taux de change
    # a porter par la ligne si Odoo l'exige (critere "facture CDF dans societe USD").

    @model_validator(mode="after")
    def validate_line_side(self) -> JournalLineDTO:
        if self.debit > Decimal(0) and self.credit > Decimal(0):
            raise ValueError("Une ligne d'écriture ne peut avoir à la fois un débit et un crédit > 0.")
        if self.debit == Decimal(0) and self.credit == Decimal(0):
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
        balance: dict[Currency, Decimal] = {}
        for line in self.lines:
            balance[line.currency] = balance.get(line.currency, Decimal(0)) + line.debit - line.credit
        unbalanced = {cur.value: str(bal) for cur, bal in balance.items() if bal != 0}
        if unbalanced:
            raise ValueError(f"Écriture déséquilibrée par devise (débit - crédit): {unbalanced}.")
        return self


class PayrollResultDTO(BaseModel):
    period: str = Field(min_length=1)
    employee_id: str = Field(min_length=1)
    gross_pay: StrictDecimal = Field(ge=Decimal(0))
    deductions: StrictDecimal = Field(ge=Decimal(0))
    net_pay: StrictDecimal = Field(ge=Decimal(0))
    employer_charges: StrictDecimal = Field(ge=Decimal(0))
    breakdown: dict[str, StrictDecimal] = Field(default_factory=dict)
