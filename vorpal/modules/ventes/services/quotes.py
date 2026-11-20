"""Service applicatif de gestion des devis clients."""

from __future__ import annotations

from datetime import date

from vorpal.core import Currency, User
from vorpal.modules.ventes.domain.errors import (
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.quotes import Quote, QuoteLine
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.services.common import (
    ApplicationService,
    LineInput,
    PostCommitBatch,
    ServiceDependencies,
)
from vorpal.modules.ventes.services.pricing import PricingService


def _resolve_quote_lines(
    uow: SalesUnitOfWork,
    pricing: PricingService,
    line_inputs: list[LineInput],
    currency: Currency,
    on_date: date,
    deps: ServiceDependencies,
) -> tuple[QuoteLine, ...]:
    if not line_inputs:
        raise SalesValidationError("Un devis doit contenir au moins une ligne.")
    resolved: list[QuoteLine] = []
    for raw in line_inputs:
        if raw.item_code is not None and raw.item_code.strip():
            code = raw.item_code.strip()
            item = uow.catalog.get_item(code)
            if item is None:
                raise SalesNotFoundError(f"Article introuvable : '{code}'.")
            if not item.is_active:
                raise SalesConflictError(f"Article inactif : '{code}'.")
            designation = raw.designation.strip() if (raw.designation and raw.designation.strip()) else item.designation
            unit_price = (
                raw.unit_price_ht
                if raw.unit_price_ht is not None
                else pricing.get_unit_price(code, currency, on_date).unit_price
            )
            if unit_price.currency != currency:
                raise SalesValidationError("La devise du prix unitaire doit correspondre a la devise du devis.")
            resolved.append(
                QuoteLine(
                    item_code=code,
                    designation=designation,
                    quantity=raw.quantity,
                    unit_price_ht=unit_price,
                    discount_percent=raw.discount_percent,
                    tax_group=item.tax_group,
                    vat_rate=item.vat_rate,
                    is_out_of_scope=item.is_out_of_scope,
                )
            )
        else:
            if not raw.designation or not raw.designation.strip():
                raise SalesValidationError("La designation est obligatoire sans code article.")
            if raw.unit_price_ht is None:
                raise SalesValidationError("Le prix unitaire HT est obligatoire sans code article.")
            if raw.unit_price_ht.currency != currency:
                raise SalesValidationError("La devise du prix unitaire doit correspondre a la devise du devis.")
            if not raw.tax_group or not raw.tax_group.strip():
                raise SalesValidationError("Le groupe de taxe (tax_group) est obligatoire sans code article.")
            group_cfg = deps.settings.get_tax_group(raw.tax_group.strip())
            if group_cfg is None:
                raise SalesValidationError(f"Groupe de taxe inconnu : '{raw.tax_group}'.")
            if group_cfg.is_out_of_scope:
                raise SalesValidationError("Une ligne hors champ exige un code article (item_code).")
            resolved.append(
                QuoteLine(
                    item_code=None,
                    designation=raw.designation.strip(),
                    quantity=raw.quantity,
                    unit_price_ht=raw.unit_price_ht,
                    discount_percent=raw.discount_percent,
                    tax_group=group_cfg.code,
                    vat_rate=group_cfg.vat_rate,
                    is_out_of_scope=False,
                )
            )
    return tuple(resolved)


class QuoteService(ApplicationService):
    """Cas d'usage lies aux devis."""

    def __init__(self, deps: ServiceDependencies, pricing: PricingService) -> None:
        super().__init__(deps)
        self._pricing = pricing

    def create_quote(
        self,
        actor: User,
        *,
        customer_id: str,
        valid_until: date,
        lines: list[LineInput],
        currency: Currency | None = None,
        quote_date: date | None = None,
        quote_id: str | None = None,
    ) -> Quote:
        """Cree un devis avec date de validite explicite et numerotation sequentielle."""
        self._require(actor, "ventes.create")
        q_date = quote_date or self._today()
        qid = quote_id or self._deps.id_factory()
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            customer = uow.customers.get(customer_id)
            if customer is None:
                raise SalesNotFoundError(f"Client introuvable : '{customer_id}'.")
            if not customer.is_active:
                raise SalesConflictError(f"Le client '{customer_id}' est inactif.")
            chosen_currency = currency or customer.preferred_currency
            resolved_lines = _resolve_quote_lines(uow, self._pricing, lines, chosen_currency, q_date, self._deps)
            number = uow.numbers.next_number("quote", q_date.year)
            quote = Quote(
                id=qid,
                number=number,
                customer_id=customer_id,
                quote_date=q_date,
                valid_until=valid_until,
                currency=chosen_currency,
                lines=resolved_lines,
            )
            uow.quotes.save(quote)
            totals = quote.compute_totals(decimals=self._decimals(chosen_currency))
            batch.add_audit(
                user=actor.username,
                action="CREATE_QUOTE",
                entity="quote",
                entity_id=quote.id,
                before=None,
                after={
                    "number": quote.number,
                    "customer_id": quote.customer_id,
                    "status": quote.status.value,
                    "total_ttc": str(totals.total_ttc.amount),
                    "currency": quote.currency.value,
                },
            )
        batch.emit(self._deps)
        return quote

    def send_quote(self, actor: User, quote_id: str) -> Quote:
        """Envoie un devis brouillon au client."""
        self._require(actor, "ventes.create")
        today = self._today()
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            quote = uow.quotes.get(quote_id)
            if quote is None:
                raise SalesNotFoundError(f"Devis introuvable : '{quote_id}'.")
            updated = quote.send(today)
            uow.quotes.save(updated)
            batch.add_audit(
                user=actor.username,
                action="SEND_QUOTE",
                entity="quote",
                entity_id=updated.id,
                before={"status": quote.status.value},
                after={"status": updated.status.value},
            )
        batch.emit(self._deps)
        return updated

    def accept_quote(self, actor: User, quote_id: str) -> Quote:
        """Accepte un devis envoye."""
        self._require(actor, "ventes.create")
        today = self._today()
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            quote = uow.quotes.get(quote_id)
            if quote is None:
                raise SalesNotFoundError(f"Devis introuvable : '{quote_id}'.")
            updated = quote.accept(today)
            uow.quotes.save(updated)
            batch.add_audit(
                user=actor.username,
                action="ACCEPT_QUOTE",
                entity="quote",
                entity_id=updated.id,
                before={"status": quote.status.value},
                after={"status": updated.status.value},
            )
        batch.emit(self._deps)
        return updated

    def reject_quote(self, actor: User, quote_id: str) -> Quote:
        """Refuse un devis envoye."""
        self._require(actor, "ventes.create")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            quote = uow.quotes.get(quote_id)
            if quote is None:
                raise SalesNotFoundError(f"Devis introuvable : '{quote_id}'.")
            updated = quote.reject()
            uow.quotes.save(updated)
            batch.add_audit(
                user=actor.username,
                action="REJECT_QUOTE",
                entity="quote",
                entity_id=updated.id,
                before={"status": quote.status.value},
                after={"status": updated.status.value},
            )
        batch.emit(self._deps)
        return updated

    def expire_due_quotes(self, actor: User, as_of: date | None = None) -> list[Quote]:
        """Marque comme expires les devis dont la date de validite est depassee."""
        self._require(actor, "ventes.create")
        ref_date = as_of or self._today()
        expired_list: list[Quote] = []
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            for quote in sorted(uow.quotes.list_all(), key=lambda q: q.id):
                if quote.status.value in ("DRAFT", "SENT") and ref_date > quote.valid_until:
                    updated = quote.mark_expired(ref_date)
                    uow.quotes.save(updated)
                    expired_list.append(updated)
                    batch.add_audit(
                        user=actor.username,
                        action="EXPIRE_QUOTE",
                        entity="quote",
                        entity_id=updated.id,
                        before={"status": quote.status.value},
                        after={"status": updated.status.value},
                    )
        batch.emit(self._deps)
        return expired_list

    def get_quote(self, actor: User, quote_id: str) -> Quote:
        """Recupere un devis par identifiant."""
        self._require(actor, "ventes.view")
        with self._deps.uow_factory() as uow:
            quote = uow.quotes.get(quote_id)
            if quote is None:
                raise SalesNotFoundError(f"Devis introuvable : '{quote_id}'.")
            return quote

    def list_quotes(self, actor: User) -> list[Quote]:
        """Liste tous les devis tries par identifiant."""
        self._require(actor, "ventes.view")
        with self._deps.uow_factory() as uow:
            return sorted(uow.quotes.list_all(), key=lambda q: q.id)
