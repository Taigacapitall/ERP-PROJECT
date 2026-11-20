"""Entites et regles metier des clients."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

from vorpal.core import Currency, Partner
from vorpal.modules.ventes.domain.errors import SalesComplianceError, SalesValidationError


@runtime_checkable
class TaxIdValidator(Protocol):
    """Point d'extension injectable pour valider le format du NIF."""

    def validate(self, nif: str | None) -> bool:
        """Retourne True si le NIF est valide."""
        ...


class NonEmptyTaxIdValidator:
    """Validateur par defaut : exige un NIF non vide sans regex inventee."""

    # TODO(A_VALIDER): Format officiel du NIF de la DGI RDC (longueur, structure alphanumerique, cle).
    def validate(self, nif: str | None) -> bool:
        return bool(nif and nif.strip())


@dataclass(frozen=True)
class ContactPerson:
    """Contact rattache a un client."""

    name: str
    email: str | None = None
    phone: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise SalesValidationError("Le nom du contact est obligatoire.")


@dataclass(frozen=True)
class PaymentTerms:
    """Conditions de paiement declaratives d'un client."""

    days: int = 30
    label: str = "Net 30 jours"

    def __post_init__(self) -> None:
        if self.days < 0:
            raise SalesValidationError("Le delai de paiement ne peut pas etre negatif.")


@dataclass(frozen=True)
class Customer:
    """Client commercial (composition avec vorpal.core.Partner)."""

    partner: Partner
    contacts: tuple[ContactPerson, ...] = ()
    payment_terms: PaymentTerms = field(default_factory=PaymentTerms)
    preferred_currency: Currency = Currency.USD
    is_active: bool = True

    def __post_init__(self) -> None:
        if self.partner.partner_type != "client":
            raise SalesValidationError("Un client doit etre adosse a un Partner de type 'client'.")
        if not self.partner.name or not self.partner.name.strip():
            raise SalesValidationError("Le nom du client est obligatoire.")

    @property
    def id(self) -> str:
        """Identifiant unique du client."""
        return self.partner.id

    @property
    def name(self) -> str:
        """Raison sociale ou nom du client."""
        return self.partner.name

    @property
    def nif(self) -> str | None:
        """Numero d'Impot (NIF) du client."""
        return self.partner.nif

    @property
    def rccm(self) -> str | None:
        """Registre du Commerce (RCCM) du client."""
        return self.partner.rccm

    @property
    def address(self) -> str | None:
        """Adresse postale du client."""
        return self.partner.address

    def deactivate(self) -> Customer:
        """Desactive le client sans supprimer ses donnees."""
        return replace(self, is_active=False)

    def activate(self) -> Customer:
        """Reactive le client."""
        return replace(self, is_active=True)

    def ensure_eligible_for_invoicing(self, validator: TaxIdValidator | None = None) -> None:
        """Verifie qu'un client est actif et dispose d'un NIF valide pour la facturation DGI."""
        if not self.is_active:
            raise SalesComplianceError(f"Le client '{self.id}' est inactif et ne peut pas etre facture.")
        active_validator = validator or NonEmptyTaxIdValidator()
        if not active_validator.validate(self.nif):
            raise SalesComplianceError(
                f"Le client '{self.id}' ne possede pas de NIF valide (exige pour emettre une facture)."
            )
