"""Service applicatif de gestion des clients."""

from __future__ import annotations

from dataclasses import replace

from vorpal.core import Currency, Partner, User
from vorpal.modules.ventes.domain.customers import ContactPerson, Customer, PaymentTerms
from vorpal.modules.ventes.domain.errors import (
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.services.common import (
    ApplicationService,
    PostCommitBatch,
    ServiceDependencies,
)


class CustomerService(ApplicationService):
    """Cas d'usage lies aux clients."""

    def __init__(self, deps: ServiceDependencies) -> None:
        super().__init__(deps)

    def create_customer(
        self,
        actor: User,
        *,
        name: str,
        nif: str | None = None,
        rccm: str | None = None,
        address: str | None = None,
        contacts: tuple[ContactPerson, ...] | list[ContactPerson] = (),
        payment_terms_days: int | None = None,
        payment_terms_label: str | None = None,
        preferred_currency: Currency | None = None,
        customer_id: str | None = None,
    ) -> Customer:
        """Cree un nouveau client avec delai et devise par defaut issus de la configuration."""
        self._require(actor, "ventes.create")
        cid = customer_id or self._deps.id_factory()
        days = self._deps.settings.default_payment_terms_days if payment_terms_days is None else payment_terms_days
        label = payment_terms_label or f"Net {days} jours"
        currency = preferred_currency or self._deps.settings.default_currency

        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            if uow.customers.get(cid) is not None:
                raise SalesConflictError(f"Un client avec l'identifiant '{cid}' existe deja.")
            partner = Partner(
                id=cid,
                name=name,
                partner_type="client",
                nif=nif,
                rccm=rccm,
                address=address,
            )
            customer = Customer(
                partner=partner,
                contacts=tuple(contacts),
                payment_terms=PaymentTerms(days=days, label=label),
                preferred_currency=currency,
                is_active=True,
            )
            uow.customers.save(customer)
            batch.add_audit(
                user=actor.username,
                action="CREATE_CUSTOMER",
                entity="customer",
                entity_id=customer.id,
                before=None,
                after={
                    "name": customer.name,
                    "nif": customer.nif or "",
                    "preferred_currency": customer.preferred_currency.value,
                    "payment_terms_days": str(customer.payment_terms.days),
                    "is_active": str(customer.is_active),
                },
            )
        batch.emit(self._deps)
        return customer

    def update_customer(
        self,
        actor: User,
        customer_id: str,
        *,
        name: str | None = None,
        nif: str | None = None,
        rccm: str | None = None,
        address: str | None = None,
        contacts: tuple[ContactPerson, ...] | list[ContactPerson] | None = None,
        payment_terms_days: int | None = None,
        payment_terms_label: str | None = None,
        preferred_currency: Currency | None = None,
    ) -> Customer:
        """Met a jour les informations d'un client existant."""
        self._require(actor, "ventes.create")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            existing = uow.customers.get(customer_id)
            if existing is None:
                raise SalesNotFoundError(f"Client introuvable : '{customer_id}'.")
            if name is not None and (not name or not name.strip()):
                raise SalesValidationError("Le nom du client ne peut pas etre vide.")

            updated_partner = Partner(
                id=existing.id,
                name=name if name is not None else existing.name,
                partner_type="client",
                nif=nif if nif is not None else existing.nif,
                rccm=rccm if rccm is not None else existing.rccm,
                address=address if address is not None else existing.address,
            )
            days = payment_terms_days if payment_terms_days is not None else existing.payment_terms.days
            label = (
                payment_terms_label
                if payment_terms_label is not None
                else (f"Net {days} jours" if payment_terms_days is not None else existing.payment_terms.label)
            )
            updated = replace(
                existing,
                partner=updated_partner,
                contacts=tuple(contacts) if contacts is not None else existing.contacts,
                payment_terms=PaymentTerms(days=days, label=label),
                preferred_currency=preferred_currency if preferred_currency is not None else existing.preferred_currency,
            )
            uow.customers.save(updated)
            batch.add_audit(
                user=actor.username,
                action="UPDATE_CUSTOMER",
                entity="customer",
                entity_id=updated.id,
                before={"name": existing.name, "nif": existing.nif or ""},
                after={"name": updated.name, "nif": updated.nif or ""},
            )
        batch.emit(self._deps)
        return updated

    def deactivate_customer(self, actor: User, customer_id: str) -> Customer:
        """Desactive un client sans supprimer ses donnees."""
        self._require(actor, "ventes.admin")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            existing = uow.customers.get(customer_id)
            if existing is None:
                raise SalesNotFoundError(f"Client introuvable : '{customer_id}'.")
            updated = existing.deactivate()
            uow.customers.save(updated)
            batch.add_audit(
                user=actor.username,
                action="DEACTIVATE_CUSTOMER",
                entity="customer",
                entity_id=updated.id,
                before={"is_active": str(existing.is_active)},
                after={"is_active": str(updated.is_active)},
            )
        batch.emit(self._deps)
        return updated

    def reactivate_customer(self, actor: User, customer_id: str) -> Customer:
        """Reactive un client."""
        self._require(actor, "ventes.admin")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            existing = uow.customers.get(customer_id)
            if existing is None:
                raise SalesNotFoundError(f"Client introuvable : '{customer_id}'.")
            updated = existing.activate()
            uow.customers.save(updated)
            batch.add_audit(
                user=actor.username,
                action="REACTIVATE_CUSTOMER",
                entity="customer",
                entity_id=updated.id,
                before={"is_active": str(existing.is_active)},
                after={"is_active": str(updated.is_active)},
            )
        batch.emit(self._deps)
        return updated

    def get_customer(self, actor: User, customer_id: str) -> Customer:
        """Recupere un client par identifiant."""
        self._require(actor, "ventes.view")
        with self._deps.uow_factory() as uow:
            customer = uow.customers.get(customer_id)
            if customer is None:
                raise SalesNotFoundError(f"Client introuvable : '{customer_id}'.")
            return customer

    def list_customers(self, actor: User, active_only: bool = False) -> list[Customer]:
        """Liste les clients dans un ordre deterministe."""
        self._require(actor, "ventes.view")
        with self._deps.uow_factory() as uow:
            items = uow.customers.list_all(active_only=active_only)
            return sorted(items, key=lambda c: c.id)
