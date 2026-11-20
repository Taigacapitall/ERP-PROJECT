"""Types communs et base applicative des services du module Ventes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from vorpal.core import AuditLog, AuthorizationService, Currency, Money, User
from vorpal.core.contracts import InvoiceDTO
from vorpal.modules.ventes.domain.errors import SalesComplianceError, SalesValidationError
from vorpal.modules.ventes.domain.payments import Payment
from vorpal.modules.ventes.domain.totals import ensure_strict_decimal
from vorpal.modules.ventes.ports.publisher import SalesEventPublisher
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.settings import SalesSettings


@dataclass(frozen=True)
class ServiceDependencies:
    """Dependances injectees aux services applicatifs de Ventes."""

    uow_factory: Callable[[], SalesUnitOfWork]
    publisher: SalesEventPublisher
    audit: AuditLog
    authz: AuthorizationService
    clock: Callable[[], datetime]
    logger: logging.Logger
    settings: SalesSettings
    id_factory: Callable[[], str]


@dataclass(frozen=True)
class LineInput:
    """Entree de ligne de document (devis, commande, facture) avant resolution metier."""

    item_code: str | None = None
    designation: str | None = None
    quantity: Decimal = Decimal("1")
    unit_price_ht: Money | None = None
    discount_percent: Decimal = Decimal("0")
    tax_group: str | None = None

    def __post_init__(self) -> None:
        qty = ensure_strict_decimal(self.quantity, "quantity")
        disc = ensure_strict_decimal(self.discount_percent, "discount_percent")
        if qty <= Decimal("0"):
            raise SalesValidationError("La quantite doit etre strictement positive.")
        if disc < Decimal("0") or disc > Decimal("100"):
            raise SalesValidationError("La remise doit etre comprise entre 0 % et 100 %.")
        if self.unit_price_ht is not None and self.unit_price_ht.amount < Decimal("0"):
            raise SalesValidationError("Le prix unitaire HT ne peut pas etre negatif.")


@dataclass(frozen=True)
class PendingAuditRecord:
    """Enregistrement d'audit prepare pendant la transaction pour emission post-commit."""

    user: str
    action: str
    entity: str
    entity_id: str
    before: dict[str, object] | None
    after: dict[str, object] | None


@dataclass
class PostCommitBatch:
    """Accumule les audits et evenements a emettre uniquement apres commit reussi."""

    audits: list[PendingAuditRecord] = field(default_factory=list)
    ready_invoices: list[InvoiceDTO] = field(default_factory=list)
    received_payments: list[Payment] = field(default_factory=list)

    def add_audit(
        self,
        *,
        user: str,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        """Ajoute une entree d'audit a publier apres validation de la transaction."""
        self.audits.append(
            PendingAuditRecord(
                user=user,
                action=action,
                entity=entity,
                entity_id=entity_id,
                before=before,
                after=after,
            )
        )

    def emit(self, deps: ServiceDependencies) -> None:
        """Execute audits puis publications d'evenements hors transaction ouverte."""
        for rec in self.audits:
            deps.audit.record(
                user=rec.user,
                action=rec.action,
                entity=rec.entity,
                entity_id=rec.entity_id,
                before=rec.before,
                after=rec.after,
                timestamp=deps.clock(),
            )
        for inv_dto in self.ready_invoices:
            deps.publisher.invoice_ready_for_certification(inv_dto)
        for payment in self.received_payments:
            deps.publisher.payment_received(payment)


class ApplicationService:
    """Classe de base des cas d'usage du module Ventes."""

    def __init__(self, deps: ServiceDependencies) -> None:
        self._deps = deps

    def _require(self, actor: User, permission: str) -> None:
        """Verifie la permission de l'acteur ou leve SalesComplianceError."""
        if not self._deps.authz.check(actor, permission):
            raise SalesComplianceError(
                f"Acces refuse pour l'utilisateur '{actor.username}' : permission '{permission}' requise."
            )

    def _today(self) -> date:
        """Retourne la date courante selon l'horloge injectee."""
        return self._deps.clock().date()

    def _decimals(self, currency: Currency) -> int:
        """Retourne le nombre de decimales configure pour une devise."""
        return self._deps.settings.rounding.decimals_for(currency)
