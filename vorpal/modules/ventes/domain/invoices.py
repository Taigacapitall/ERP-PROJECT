"""Entites, machine a etats et regles fiscales des factures et avoirs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from vorpal.core import Currency, Money
from vorpal.core.contracts import CertifiedInvoiceDTO, InvoiceDTO, InvoiceLineDTO, PartyInfoDTO
from vorpal.modules.ventes.domain.customers import Customer, TaxIdValidator
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.totals import (
    LineCalculationInput,
    compute_document_totals,
    compute_line_amounts,
    ensure_strict_decimal,
)
from vorpal.modules.ventes.settings import SellerSettings


class InvoiceStatus(str, Enum):
    """Cycle de vie fiscal d'une facture ou d'un avoir."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    CERTIFIED = "CERTIFIED"
    REJECTED = "REJECTED"


class InvoicePaymentStatus(str, Enum):
    """Statut de reglement derive des encaissements lettres."""

    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERPAID = "OVERPAID"


@dataclass(frozen=True)
class FiscalStamp:
    """Cachet fiscal retourne par le module DGI lors de la certification."""

    device_id: str
    auth_code: str
    qr_code: str
    certified_at: datetime
    # TODO(A_VALIDER): contenu exact des compteurs DGI (le Core impose un dictionnaire).
    counters: Mapping[str, str | int | Decimal] = field(default_factory=dict, hash=False)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def stamps_match(a: FiscalStamp, b: FiscalStamp) -> bool:
    """Egalite de deux cachets (dates comparees en UTC ; SQLite perd le fuseau)."""
    return (
        a.device_id == b.device_id
        and a.auth_code == b.auth_code
        and a.qr_code == b.qr_code
        and _as_utc(a.certified_at) == _as_utc(b.certified_at)
        and dict(a.counters) == dict(b.counters)
    )


@dataclass(frozen=True)
class InvoiceLine:
    """Ligne d'une facture ou d'un avoir."""

    item_code: str | None
    designation: str
    quantity: Decimal
    unit_price_ht: Money
    discount_percent: Decimal = Decimal("0")
    tax_group: str = "A"
    vat_rate: Decimal = Decimal("0.16")
    is_out_of_scope: bool = False
    order_line_id: str | None = None

    def __post_init__(self) -> None:
        if not self.designation or not self.designation.strip():
            raise SalesValidationError("La designation de la ligne de facture est obligatoire.")
        qty = ensure_strict_decimal(self.quantity, "quantity")
        discount = ensure_strict_decimal(self.discount_percent, "discount_percent")
        rate = ensure_strict_decimal(self.vat_rate, "vat_rate")
        if qty <= Decimal("0"):
            raise SalesValidationError("La quantite doit etre strictement positive.")
        if self.unit_price_ht.amount < Decimal("0"):
            raise SalesValidationError("Le prix unitaire HT ne peut pas etre negatif.")
        if discount < Decimal("0") or discount > Decimal("100"):
            raise SalesValidationError("La remise doit etre comprise entre 0 % et 100 %.")
        if rate < Decimal("0"):
            raise SalesValidationError("Le taux de TVA ne peut pas etre negatif.")

    def check_fiscal_rules(self) -> None:
        """Verifie les regles fiscales de ligne (ex: hors champ => item_code obligatoire)."""
        if self.is_out_of_scope and (not self.item_code or not self.item_code.strip()):
            raise SalesComplianceError(
                f"La ligne hors champ '{self.designation}' doit obligatoirement porter un item_code DGI."
            )

    def to_dto(self, decimals: int = 2) -> InvoiceLineDTO:
        """Convertit la ligne en InvoiceLineDTO conforme au Core."""
        self.check_fiscal_rules()
        amounts = compute_line_amounts(
            LineCalculationInput(
                quantity=self.quantity,
                unit_price_ht=self.unit_price_ht.amount,
                discount_percent=self.discount_percent,
                vat_rate=self.vat_rate,
            ),
            currency=self.unit_price_ht.currency,
            decimals=decimals,
        )
        # TODO(A_VALIDER): Representation exacte des remises dans InvoiceLineDTO pour la DGI
        # (actuellement reflétée dans le prix unitaire net HT).
        return InvoiceLineDTO(
            designation=self.designation,
            quantity=self.quantity,
            unit_price_ht=amounts.net_unit_price_ht.amount,
            tax_group=self.tax_group,
            vat_rate=self.vat_rate,
            item_code=self.item_code,
        )


@dataclass(frozen=True)
class SalesInvoice:
    """Facture de vente ou avoir (invoice_type='vente'|'avoir')."""

    id: str
    customer_id: str
    invoice_date: date
    due_date: date
    currency: Currency
    lines: tuple[InvoiceLine, ...]
    invoice_type: Literal["vente", "avoir"] = "vente"
    number: str | None = None
    status: InvoiceStatus = InvoiceStatus.DRAFT
    order_id: str | None = None
    original_invoice_id: str | None = None
    original_invoice_number: str | None = None
    total_ht: Money | None = None
    total_vat: Money | None = None
    total_ttc: Money | None = None
    paid_amount: Money | None = None
    fiscal_stamp: FiscalStamp | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise SalesValidationError("L'identifiant de facture est obligatoire.")
        if not self.customer_id or not self.customer_id.strip():
            raise SalesValidationError("Le client de la facture est obligatoire.")
        if self.due_date < self.invoice_date:
            raise SalesValidationError("La date d'echeance ne peut pas etre anterieure a la date de facture.")
        if not self.lines:
            raise SalesValidationError("Une facture doit contenir au moins une ligne.")
        for line in self.lines:
            if line.unit_price_ht.currency != self.currency:
                raise SalesValidationError("Toutes les lignes d'une facture doivent avoir la meme devise.")
        if self.invoice_type == "avoir" and not self.original_invoice_id:
            raise SalesValidationError("Un avoir doit referencer une facture d'origine.")

    def _compute_lines_totals(self, decimals: int = 2) -> tuple[Money, Money, Money]:
        inputs = [
            LineCalculationInput(
                quantity=line.quantity,
                unit_price_ht=line.unit_price_ht.amount,
                discount_percent=line.discount_percent,
                vat_rate=line.vat_rate,
            )
            for line in self.lines
        ]
        totals = compute_document_totals(inputs, currency=self.currency, decimals=decimals)
        return totals.total_ht, totals.total_vat, totals.total_ttc

    @property
    def effective_total_ttc(self) -> Money:
        """Retourne le total TTC fige (si validee) ou calcule a la volee (si brouillon)."""
        if self.total_ttc is not None:
            return self.total_ttc
        _, _, ttc = self._compute_lines_totals()
        return ttc

    @property
    def effective_paid_amount(self) -> Money:
        """Retourne le montant regle (0 par defaut)."""
        if self.paid_amount is not None:
            return self.paid_amount
        return Money(Decimal("0.00"), self.currency)

    @property
    def balance_due(self) -> Money:
        """Solde restant a payer sur la facture."""
        return self.effective_total_ttc - self.effective_paid_amount

    @property
    def payment_status(self) -> InvoicePaymentStatus:
        """Statut de paiement derive du solde TTC."""
        paid = self.effective_paid_amount.amount
        ttc = self.effective_total_ttc.amount
        if paid <= Decimal("0"):
            return InvoicePaymentStatus.UNPAID
        if paid < ttc:
            return InvoicePaymentStatus.PARTIALLY_PAID
        if paid == ttc:
            return InvoicePaymentStatus.PAID
        return InvoicePaymentStatus.OVERPAID

    def update_lines(self, new_lines: tuple[InvoiceLine, ...]) -> SalesInvoice:
        """Modifie les lignes uniquement si la facture est en brouillon (DRAFT)."""
        if self.status in (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED):
            raise SalesComplianceError(
                f"Modification interdite : la facture est en etat {self.status.value}. Utilisez un avoir."
            )
        if self.status != InvoiceStatus.DRAFT:
            raise SalesConflictError(
                f"La facture est en etat {self.status.value} : rouvrez-la explicitement avant modification."
            )
        return replace(self, lines=new_lines)

    def validate(
        self,
        *,
        customer: Customer,
        assigned_number: str,
        decimals: int = 2,
        tax_id_validator: TaxIdValidator | None = None,
        original_invoice: SalesInvoice | None = None,
        already_credited_ttc: Money | None = None,
    ) -> SalesInvoice:
        """Valide la facture, fige les lignes et totaux, et attribue le numero officiel."""
        if self.status != InvoiceStatus.DRAFT:
            raise SalesConflictError(f"Seule une facture DRAFT peut etre validee (etat actuel : {self.status.value}).")
        if customer.id != self.customer_id:
            raise SalesValidationError("Le client fourni ne correspond pas au client de la facture.")
        if not assigned_number or not assigned_number.strip():
            raise SalesValidationError("Un numero de facture est obligatoire a la validation.")

        customer.ensure_eligible_for_invoicing(tax_id_validator)
        for line in self.lines:
            line.check_fiscal_rules()

        total_ht, total_vat, total_ttc = self._compute_lines_totals(decimals=decimals)

        if self.invoice_type == "avoir":
            if original_invoice is None:
                raise SalesValidationError("La facture d'origine est requise pour valider un avoir.")
            if original_invoice.invoice_type != "vente":
                raise SalesComplianceError("Un avoir ne peut porter que sur une facture de vente.")
            if original_invoice.status != InvoiceStatus.CERTIFIED:
                raise SalesComplianceError("Un avoir ne peut etre emis que sur une facture d'origine CERTIFIED.")
            if original_invoice.currency != self.currency:
                raise SalesValidationError("La devise de l'avoir doit correspondre a celle de la facture d'origine.")
            if not original_invoice.number:
                raise SalesComplianceError("La facture d'origine doit porter un numero officiel.")
            prior = already_credited_ttc or Money(Decimal("0.00"), self.currency)
            if (prior + total_ttc).amount > original_invoice.effective_total_ttc.amount:
                raise SalesComplianceError(
                    "Le montant cumule des avoirs depasse le total TTC de la facture d'origine."
                )

        final_number = self.number if (self.number and self.number.strip()) else assigned_number
        orig_num = original_invoice.number if original_invoice is not None else self.original_invoice_number
        paid = self.paid_amount or Money(Decimal("0.00"), self.currency)

        return replace(
            self,
            number=final_number,
            status=InvoiceStatus.VALIDATED,
            original_invoice_number=orig_num,
            total_ht=total_ht,
            total_vat=total_vat,
            total_ttc=total_ttc,
            paid_amount=paid,
            rejection_reason=None,
        )

    def to_invoice_dto(
        self,
        customer: Customer,
        seller: SellerSettings,
        decimals: int = 2,
    ) -> InvoiceDTO:
        """Construit le contrat InvoiceDTO partage avec le module DGI."""
        if self.status not in (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED):
            raise SalesConflictError("La facture doit etre validee avant de generer un InvoiceDTO.")
        if not self.number:
            raise SalesValidationError("Numero de facture manquant pour le DTO.")
        if self.total_ht is None or self.total_vat is None or self.total_ttc is None:
            raise SalesValidationError("Totaux de facture manquants pour le DTO.")

        return InvoiceDTO(
            number=self.number,
            invoice_date=self.invoice_date,
            seller=PartyInfoDTO(
                name=seller.name,
                nif=seller.nif,
                rccm=seller.rccm,
                address=seller.address,
            ),
            customer=PartyInfoDTO(
                name=customer.name,
                nif=customer.nif,
                rccm=customer.rccm,
                address=customer.address,
            ),
            lines=[line.to_dto(decimals=decimals) for line in self.lines],
            currency=self.currency,
            total_ht=self.total_ht.amount,
            total_vat=self.total_vat.amount,
            total_ttc=self.total_ttc.amount,
            invoice_type=self.invoice_type,
            original_invoice_ref=self.original_invoice_number,
        )

    def mark_certified(self, certified_dto: CertifiedInvoiceDTO) -> SalesInvoice:
        """Applique la certification DGI de maniere idempotente."""
        stamp = FiscalStamp(
            device_id=certified_dto.device_id,
            auth_code=certified_dto.auth_code,
            qr_code=certified_dto.qr_code,
            certified_at=certified_dto.certified_at,
            counters=dict(certified_dto.counters),
        )
        if self.status == InvoiceStatus.CERTIFIED:
            if self.fiscal_stamp is not None and stamps_match(self.fiscal_stamp, stamp):
                return self
            raise SalesConflictError(
                "Facture deja certifiee avec un cachet fiscal different : "
                "le cachet enregistre ne peut pas etre ecrase."
            )
        if self.status != InvoiceStatus.VALIDATED:
            raise SalesConflictError(
                f"Impossible de certifier une facture en etat {self.status.value}."
            )
        return replace(self, status=InvoiceStatus.CERTIFIED, fiscal_stamp=stamp, rejection_reason=None)

    def mark_rejected(self, reason: str) -> SalesInvoice:
        """Passe la facture en REJECTED suite au retour du module DGI."""
        if self.status == InvoiceStatus.REJECTED:
            return self
        if self.status != InvoiceStatus.VALIDATED:
            raise SalesConflictError(
                f"Seule une facture en attente (VALIDATED) peut etre rejetee ({self.status.value})."
            )
        clean_reason = reason.strip() if reason and reason.strip() else "Rejet DGI sans motif detaille"
        return replace(self, status=InvoiceStatus.REJECTED, rejection_reason=clean_reason)

    def reopen_after_rejection(self, preserve_number: bool = True) -> SalesInvoice:
        """Action explicite pour repasser une facture REJECTED en DRAFT modifiable."""
        if self.status != InvoiceStatus.REJECTED:
            raise SalesConflictError(
                f"Seule une facture REJECTED peut etre rouverte (etat actuel : {self.status.value})."
            )
        # TODO(A_VALIDER): Confirmer aupres de la DGI si le numero attribue avant rejet doit etre conserve ou re-attribue.
        kept_number = self.number if preserve_number else None
        return replace(
            self,
            status=InvoiceStatus.DRAFT,
            number=kept_number,
            total_ht=None,
            total_vat=None,
            total_ttc=None,
        )

    def apply_paid_amount(self, new_paid_amount: Money) -> SalesInvoice:
        """Met a jour le montant encaisse lettre sur cette facture."""
        if new_paid_amount.currency != self.currency:
            raise SalesValidationError("La devise du montant regle ne correspond pas a la facture.")
        if new_paid_amount.amount < Decimal("0"):
            raise SalesValidationError("Le montant regle ne peut pas etre negatif.")
        return replace(self, paid_amount=new_paid_amount)


CreditNote = SalesInvoice
