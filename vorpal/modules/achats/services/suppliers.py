"""Service applicatif de gestion des fournisseurs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from vorpal.core.audit import AuditLog
from vorpal.core.money import Currency
from vorpal.core.partners import Partner
from vorpal.core.security import AuthorizationService, User
from vorpal.modules.achats.domain.errors import PurchaseConflictError, PurchaseNotFoundError
from vorpal.modules.achats.domain.suppliers import (
    Supplier,
    SupplierContact,
    SupplierTaxIdValidator,
)
from vorpal.modules.achats.ports.unit_of_work import PurchaseUnitOfWork
from vorpal.modules.achats.services.common import record_audit, require_permission


class SupplierService:
    """Orchestre la creation, la mise a jour et la gestion d'etat des fournisseurs."""

    def __init__(
        self,
        uow_factory: Callable[[], PurchaseUnitOfWork],
        audit: AuditLog,
        authz: AuthorizationService,
        clock: Callable[[], datetime] | None = None,
        tax_id_validator: SupplierTaxIdValidator | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._authz = authz
        self._clock = clock
        self._tax_id_validator = tax_id_validator

    def create_supplier(
        self,
        actor: User,
        supplier_id: str,
        name: str,
        nif: str | None = None,
        rccm: str | None = None,
        address: str | None = None,
        payment_terms_days: int = 30,
        preferred_currency: Currency = Currency.USD,
        contacts: list[SupplierContact] | None = None,
    ) -> Supplier:
        require_permission(self._authz, actor, "achats.create")

        with self._uow_factory() as uow:
            if uow.suppliers.get(supplier_id) is not None:
                raise PurchaseConflictError(
                    f"Un fournisseur avec l'identifiant '{supplier_id}' existe deja."
                )

            partner = Partner(
                id=supplier_id,
                name=name,
                partner_type="fournisseur",
                nif=nif,
                rccm=rccm,
                address=address,
            )
            supplier = Supplier(
                id=supplier_id,
                partner=partner,
                payment_terms_days=payment_terms_days,
                preferred_currency=preferred_currency,
                contacts=list(contacts) if contacts else [],
                is_active=True,
            )
            uow.suppliers.add(supplier)

        record_audit(
            self._audit,
            actor=actor,
            action="CREATE",
            entity="supplier",
            entity_id=supplier.id,
            before=None,
            after={
                "name": supplier.name,
                "nif": supplier.nif,
                "payment_terms_days": supplier.payment_terms_days,
                "preferred_currency": supplier.preferred_currency.value,
                "is_active": supplier.is_active,
            },
            clock=self._clock,
        )
        return supplier

    def get_supplier(self, actor: User, supplier_id: str) -> Supplier:
        require_permission(self._authz, actor, "achats.view")
        with self._uow_factory() as uow:
            supplier = uow.suppliers.get(supplier_id)
            if supplier is None:
                raise PurchaseNotFoundError(f"Fournisseur '{supplier_id}' introuvable.")
            return supplier

    def list_suppliers(self, actor: User) -> list[Supplier]:
        require_permission(self._authz, actor, "achats.view")
        with self._uow_factory() as uow:
            return uow.suppliers.list_all()

    def update_supplier(
        self,
        actor: User,
        supplier_id: str,
        name: str | None = None,
        nif: str | None = None,
        rccm: str | None = None,
        address: str | None = None,
        payment_terms_days: int | None = None,
        preferred_currency: Currency | None = None,
    ) -> Supplier:
        require_permission(self._authz, actor, "achats.create")

        with self._uow_factory() as uow:
            existing = uow.suppliers.get(supplier_id)
            if existing is None:
                raise PurchaseNotFoundError(f"Fournisseur '{supplier_id}' introuvable.")

            before_state = {
                "name": existing.name,
                "nif": existing.nif,
                "rccm": existing.partner.rccm,
                "address": existing.partner.address,
                "payment_terms_days": existing.payment_terms_days,
                "preferred_currency": existing.preferred_currency.value,
            }

            updated_partner = Partner(
                id=existing.partner.id,
                name=name if name is not None else existing.partner.name,
                partner_type="fournisseur",
                nif=nif if nif is not None else existing.partner.nif,
                rccm=rccm if rccm is not None else existing.partner.rccm,
                address=address if address is not None else existing.partner.address,
            )
            updated = Supplier(
                id=existing.id,
                partner=updated_partner,
                payment_terms_days=payment_terms_days
                if payment_terms_days is not None
                else existing.payment_terms_days,
                preferred_currency=preferred_currency
                if preferred_currency is not None
                else existing.preferred_currency,
                contacts=list(existing.contacts),
                is_active=existing.is_active,
            )
            uow.suppliers.add(updated)

        record_audit(
            self._audit,
            actor=actor,
            action="UPDATE",
            entity="supplier",
            entity_id=updated.id,
            before=before_state,
            after={
                "name": updated.name,
                "nif": updated.nif,
                "rccm": updated.partner.rccm,
                "address": updated.partner.address,
                "payment_terms_days": updated.payment_terms_days,
                "preferred_currency": updated.preferred_currency.value,
            },
            clock=self._clock,
        )
        return updated

    def set_active_status(self, actor: User, supplier_id: str, is_active: bool) -> Supplier:
        require_permission(self._authz, actor, "achats.admin")

        with self._uow_factory() as uow:
            supplier = uow.suppliers.get(supplier_id)
            if supplier is None:
                raise PurchaseNotFoundError(f"Fournisseur '{supplier_id}' introuvable.")
            before_active = supplier.is_active
            if is_active:
                supplier.activate()
            else:
                supplier.deactivate()
            uow.suppliers.add(supplier)

        record_audit(
            self._audit,
            actor=actor,
            action="ACTIVATE" if is_active else "DEACTIVATE",
            entity="supplier",
            entity_id=supplier.id,
            before={"is_active": before_active},
            after={"is_active": supplier.is_active},
            clock=self._clock,
        )
        return supplier
