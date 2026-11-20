"""Service applicatif de gestion des commandes clients et acomptes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from vorpal.core import Currency, Money, User
from vorpal.modules.ventes.domain.errors import (
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.orders import OrderLine, SalesOrder
from vorpal.modules.ventes.domain.totals import ensure_strict_decimal, round_decimal
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.services.common import (
    ApplicationService,
    LineInput,
    PostCommitBatch,
    ServiceDependencies,
)
from vorpal.modules.ventes.services.pricing import PricingService


def _resolve_order_lines(
    uow: SalesUnitOfWork,
    pricing: PricingService,
    line_inputs: list[LineInput],
    currency: Currency,
    on_date: date,
    deps: ServiceDependencies,
) -> tuple[OrderLine, ...]:
    if not line_inputs:
        raise SalesValidationError("Une commande doit contenir au moins une ligne.")
    resolved: list[OrderLine] = []
    for raw in line_inputs:
        line_id = deps.id_factory()
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
                raise SalesValidationError("La devise du prix unitaire doit correspondre a la devise de la commande.")
            resolved.append(
                OrderLine(
                    line_id=line_id,
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
                raise SalesValidationError("La devise du prix unitaire doit correspondre a la devise de la commande.")
            if not raw.tax_group or not raw.tax_group.strip():
                raise SalesValidationError("Le groupe de taxe (tax_group) est obligatoire sans code article.")
            group_cfg = deps.settings.get_tax_group(raw.tax_group.strip())
            if group_cfg is None:
                raise SalesValidationError(f"Groupe de taxe inconnu : '{raw.tax_group}'.")
            if group_cfg.is_out_of_scope:
                raise SalesValidationError("Une ligne hors champ exige un code article (item_code).")
            resolved.append(
                OrderLine(
                    line_id=line_id,
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


class OrderService(ApplicationService):
    """Cas d'usage lies aux commandes clients."""

    def __init__(self, deps: ServiceDependencies, pricing: PricingService) -> None:
        super().__init__(deps)
        self._pricing = pricing

    def create_order(
        self,
        actor: User,
        *,
        customer_id: str,
        lines: list[LineInput],
        currency: Currency | None = None,
        order_date: date | None = None,
        order_id: str | None = None,
    ) -> SalesOrder:
        """Cree une commande en brouillon avec numerotation sequentielle."""
        self._require(actor, "ventes.create")
        o_date = order_date or self._today()
        oid = order_id or self._deps.id_factory()
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            customer = uow.customers.get(customer_id)
            if customer is None:
                raise SalesNotFoundError(f"Client introuvable : '{customer_id}'.")
            if not customer.is_active:
                raise SalesConflictError(f"Le client '{customer_id}' est inactif.")
            chosen_currency = currency or customer.preferred_currency
            resolved_lines = _resolve_order_lines(uow, self._pricing, lines, chosen_currency, o_date, self._deps)
            number = uow.numbers.next_number("order", o_date.year)
            order = SalesOrder(
                id=oid,
                number=number,
                customer_id=customer_id,
                order_date=o_date,
                currency=chosen_currency,
                lines=resolved_lines,
            )
            uow.orders.save(order)
            totals = order.compute_totals(decimals=self._decimals(chosen_currency))
            batch.add_audit(
                user=actor.username,
                action="CREATE_ORDER",
                entity="order",
                entity_id=order.id,
                before=None,
                after={
                    "number": order.number,
                    "customer_id": order.customer_id,
                    "status": order.status.value,
                    "total_ttc": str(totals.total_ttc.amount),
                    "currency": order.currency.value,
                },
            )
        batch.emit(self._deps)
        return order

    def create_from_quote(
        self,
        actor: User,
        quote_id: str,
        *,
        order_date: date | None = None,
        order_id: str | None = None,
    ) -> SalesOrder:
        """Convertit un devis accepte en commande (une seule fois, atomique)."""
        self._require(actor, "ventes.create")
        o_date = order_date or self._today()
        oid = order_id or self._deps.id_factory()
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            quote = uow.quotes.get(quote_id)
            if quote is None:
                raise SalesNotFoundError(f"Devis introuvable : '{quote_id}'.")
            if quote.status.value == "CONVERTED" or quote.converted_order_id is not None:
                raise SalesConflictError("Ce devis a deja ete converti en commande.")
            if quote.status.value != "ACCEPTED":
                raise SalesConflictError(
                    f"Seul un devis accepte peut etre converti en commande (etat actuel : {quote.status.value})."
                )
            number = uow.numbers.next_number("order", o_date.year)
            order_lines = tuple(
                OrderLine(
                    line_id=self._deps.id_factory(),
                    item_code=ql.item_code,
                    designation=ql.designation,
                    quantity=ql.quantity,
                    unit_price_ht=ql.unit_price_ht,
                    discount_percent=ql.discount_percent,
                    tax_group=ql.tax_group,
                    vat_rate=ql.vat_rate,
                    is_out_of_scope=ql.is_out_of_scope,
                )
                for ql in quote.lines
            )
            order = SalesOrder(
                id=oid,
                number=number,
                customer_id=quote.customer_id,
                order_date=o_date,
                currency=quote.currency,
                lines=order_lines,
                quote_id=quote.id,
            )
            converted_quote = quote.mark_converted(order.id)
            uow.orders.save(order)
            uow.quotes.save(converted_quote)
            batch.add_audit(
                user=actor.username,
                action="CONVERT_QUOTE_TO_ORDER",
                entity="order",
                entity_id=order.id,
                before={"quote_id": quote.id, "quote_status": quote.status.value},
                after={"order_number": order.number, "quote_status": converted_quote.status.value},
            )
        batch.emit(self._deps)
        return order

    def update_order_lines(self, actor: User, order_id: str, lines: list[LineInput]) -> SalesOrder:
        """Met a jour les lignes d'une commande en brouillon."""
        self._require(actor, "ventes.create")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                raise SalesNotFoundError(f"Commande introuvable : '{order_id}'.")
            if order.status.value != "DRAFT":
                raise SalesConflictError(
                    f"Aucune modification de lignes n'est autorisee en etat {order.status.value}."
                )
            resolved_lines = _resolve_order_lines(uow, self._pricing, lines, order.currency, order.order_date, self._deps)
            updated = order.update_lines(resolved_lines)
            uow.orders.save(updated)
            batch.add_audit(
                user=actor.username,
                action="UPDATE_ORDER_LINES",
                entity="order",
                entity_id=updated.id,
                before={"line_count": str(len(order.lines))},
                after={"line_count": str(len(updated.lines))},
            )
        batch.emit(self._deps)
        return updated

    def set_deposit(
        self,
        actor: User,
        order_id: str,
        *,
        percent: Decimal | None = None,
        amount: Money | None = None,
    ) -> SalesOrder:
        """Definit l'acompte requis sur une commande DRAFT ou CONFIRMED (percent OU amount)."""
        self._require(actor, "ventes.create")
        if (percent is None and amount is None) or (percent is not None and amount is not None):
            raise SalesValidationError("Exactement un des parametres 'percent' ou 'amount' doit etre fourni.")

        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                raise SalesNotFoundError(f"Commande introuvable : '{order_id}'.")
            if order.status.value not in ("DRAFT", "CONFIRMED"):
                raise SalesConflictError(
                    f"L'acompte ne peut etre defini qu'en etat DRAFT ou CONFIRMED ({order.status.value})."
                )
            decimals = self._decimals(order.currency)
            if percent is not None:
                pct = ensure_strict_decimal(percent, "percent")
                if pct < Decimal("0") or pct > Decimal("100"):
                    raise SalesValidationError("Le pourcentage d'acompte doit etre compris entre 0 et 100.")
                # TODO(A_VALIDER): Confirmer si le pourcentage d'acompte s'applique sur le TTC ou le HT.
                totals = order.compute_totals(decimals=decimals)
                raw_deposit = (totals.total_ttc.amount * pct) / Decimal("100")
                deposit_money = Money(round_decimal(raw_deposit, decimals), order.currency)
            else:
                assert amount is not None
                if amount.currency != order.currency:
                    raise SalesValidationError("La devise de l'acompte doit correspondre a celle de la commande.")
                if amount.amount < Decimal("0"):
                    raise SalesValidationError("Le montant d'acompte ne peut pas etre negatif.")
                deposit_money = Money(round_decimal(amount.amount, decimals), order.currency)

            from dataclasses import replace

            updated = replace(order, deposit_required=deposit_money)
            uow.orders.save(updated)
            batch.add_audit(
                user=actor.username,
                action="SET_ORDER_DEPOSIT",
                entity="order",
                entity_id=updated.id,
                before={"deposit_required": str(order.deposit_required.amount) if order.deposit_required else ""},
                after={"deposit_required": str(deposit_money.amount), "currency": deposit_money.currency.value},
            )
        batch.emit(self._deps)
        return updated

    def confirm_order(self, actor: User, order_id: str) -> SalesOrder:
        """Confirme une commande en brouillon."""
        self._require(actor, "ventes.confirm")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                raise SalesNotFoundError(f"Commande introuvable : '{order_id}'.")
            updated = order.confirm()
            uow.orders.save(updated)
            batch.add_audit(
                user=actor.username,
                action="CONFIRM_ORDER",
                entity="order",
                entity_id=updated.id,
                before={"status": order.status.value},
                after={"status": updated.status.value},
            )
        batch.emit(self._deps)
        return updated

    def cancel_order(self, actor: User, order_id: str) -> SalesOrder:
        """Annule une commande avant toute facturation."""
        self._require(actor, "ventes.confirm")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                raise SalesNotFoundError(f"Commande introuvable : '{order_id}'.")
            updated = order.cancel()
            uow.orders.save(updated)
            batch.add_audit(
                user=actor.username,
                action="CANCEL_ORDER",
                entity="order",
                entity_id=updated.id,
                before={"status": order.status.value},
                after={"status": updated.status.value},
            )
        batch.emit(self._deps)
        return updated

    def get_order(self, actor: User, order_id: str) -> SalesOrder:
        """Recupere une commande par identifiant."""
        self._require(actor, "ventes.view")
        with self._deps.uow_factory() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                raise SalesNotFoundError(f"Commande introuvable : '{order_id}'.")
            return order

    def list_orders(self, actor: User) -> list[SalesOrder]:
        """Liste toutes les commandes tries par identifiant."""
        self._require(actor, "ventes.view")
        with self._deps.uow_factory() as uow:
            return sorted(uow.orders.list_all(), key=lambda o: o.id)
