"""Entite et regles metier des fournisseurs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from vorpal.core.money import Currency
from vorpal.core.partners import Partner
from vorpal.modules.achats.domain.errors import PurchaseValidationError


@runtime_checkable
class SupplierTaxIdValidator(Protocol):
    """Validateur injectable du numero d'impot / NIF fournisseur."""

    def validate(self, tax_id: str | None) -> bool:
        """Retourne True si le NIF/identifiant fiscal est acceptable."""
        ...


class DefaultSupplierTaxIdValidator:
    """Validateur par defaut (TODO(A_VALIDER): format officiel NIF/ID national RDC)."""

    def validate(self, tax_id: str | None) -> bool:
        return tax_id is not None and tax_id.strip() != ""


@dataclass(frozen=True)
class SupplierContact:
    """Contact rattache a un fournisseur."""

    name: str
    role: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class Supplier:
    """Fournisseur du module Achats, appuye sur vorpal.core.Partner."""

    id: str
    partner: Partner
    payment_terms_days: int = 30
    preferred_currency: Currency = Currency.USD
    contacts: list[SupplierContact] = field(default_factory=list)
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise PurchaseValidationError("L'identifiant du fournisseur est obligatoire.")
        if self.partner.partner_type != "fournisseur":
            raise PurchaseValidationError(
                "Le partenaire rattache doit avoir le type 'fournisseur'."
            )
        if not self.partner.name.strip():
            raise PurchaseValidationError("Le nom du fournisseur est obligatoire.")
        if self.payment_terms_days < 0:
            raise PurchaseValidationError("Le delai de paiement ne peut pas etre negatif.")

    @property
    def name(self) -> str:
        return self.partner.name

    @property
    def nif(self) -> str | None:
        return self.partner.nif

    def ensure_can_order(self, tax_id_validator: SupplierTaxIdValidator | None = None) -> None:
        """Verifie que le fournisseur est actif et dispose d'un identifiant fiscal valide."""
        if not self.is_active:
            raise PurchaseValidationError(f"Le fournisseur '{self.id}' est inactif.")
        validator = (
            tax_id_validator if tax_id_validator is not None else DefaultSupplierTaxIdValidator()
        )
        if not validator.validate(self.partner.nif):
            raise PurchaseValidationError(
                f"Le fournisseur '{self.id}' doit avoir un NIF/identifiant fiscal renseigne."
            )

    def deactivate(self) -> None:
        """Desactive le fournisseur sans supprimer ses donnees."""
        self.is_active = False

    def activate(self) -> None:
        """Reactive le fournisseur."""
        self.is_active = True
