"""Service applicatif de tarification et conversion de devises USD/CDF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from vorpal.core import Currency, Money, ServiceRegistry, User
from vorpal.core.contracts import ExchangeRateProviderProtocol
from vorpal.modules.ventes.domain.catalog import PriceList, SalesItem, resolve_catalog_price
from vorpal.modules.ventes.domain.errors import (
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.totals import ensure_strict_decimal, round_decimal
from vorpal.modules.ventes.services.common import (
    ApplicationService,
    PostCommitBatch,
    ServiceDependencies,
)


@dataclass(frozen=True)
class PriceResult:
    """Resultat de resolution d'un prix unitaire HT dans la devise demandee."""

    unit_price: Money
    converted: bool
    rate: Decimal


class PricingService(ApplicationService):
    """Resout les prix catalogue et gere les listes de prix multi-devises."""

    def __init__(self, deps: ServiceDependencies, services: ServiceRegistry) -> None:
        super().__init__(deps)
        self._services = services

    def get_unit_price(
        self,
        item_code: str,
        currency: Currency,
        on_date: date | None = None,
    ) -> PriceResult:
        """Retourne le prix unitaire HT dans la devise cible (direct ou converti via fournisseur)."""
        target_date = on_date or self._today()
        with self._deps.uow_factory() as uow:
            item = uow.catalog.get_item(item_code)
            if item is None:
                raise SalesNotFoundError(f"Article introuvable dans le catalogue : '{item_code}'.")
            if not item.is_active:
                raise SalesConflictError(f"L'article '{item_code}' est inactif.")

            active_lists = sorted(
                uow.catalog.list_price_lists(active_only=True),
                key=lambda pl: pl.id,
            )

        try:
            direct = resolve_catalog_price(item_code, currency, active_lists)
            decimals = self._decimals(currency)
            rounded = Money(round_decimal(direct.amount, decimals), currency)
            return PriceResult(unit_price=rounded, converted=False, rate=Decimal("1"))
        except SalesNotFoundError:
            pass

        other_currency = Currency.CDF if currency == Currency.USD else Currency.USD
        try:
            source_price = resolve_catalog_price(item_code, other_currency, active_lists)
        except SalesNotFoundError as exc:
            raise SalesNotFoundError(
                f"Aucun prix trouve pour l'article '{item_code}' en {currency.value} ni en {other_currency.value}."
            ) from exc

        rate_provider = cast(
            ExchangeRateProviderProtocol | None,
            self._services.get_optional(cast(Any, ExchangeRateProviderProtocol)),
        )
        if rate_provider is None:
            raise RuntimeError(
                f"Fournisseur de taux de change indisponible pour convertir '{item_code}' de {other_currency.value} vers {currency.value}."
            )

        try:
            rate = rate_provider.get_rate(other_currency, currency, target_date)
        except Exception as exc:
            raise RuntimeError(
                f"Taux de change indisponible pour {other_currency.value}->{currency.value} le {target_date.isoformat()}."
            ) from exc

        dec_rate = ensure_strict_decimal(rate, "exchange_rate")
        if dec_rate <= Decimal("0"):
            raise SalesValidationError("Le taux de change doit etre strictement positif.")

        raw_converted = source_price.amount * dec_rate
        decimals = self._decimals(currency)
        rounded_money = Money(round_decimal(raw_converted, decimals), currency)
        return PriceResult(unit_price=rounded_money, converted=True, rate=dec_rate)

    def save_item(
        self,
        actor: User,
        *,
        code: str,
        designation: str,
        tax_group: str | None = None,
        vat_rate: Decimal | None = None,
        is_out_of_scope: bool | None = None,
        is_active: bool = True,
    ) -> SalesItem:
        """Cree ou met a jour un article du catalogue."""
        self._require(actor, "ventes.admin")
        if not tax_group or not tax_group.strip():
            raise SalesValidationError("Le groupe de taxe est obligatoire pour un article.")
        group_cfg = self._deps.settings.get_tax_group(tax_group)
        if group_cfg is None:
            raise SalesValidationError(f"Groupe de taxe inconnu dans la configuration : '{tax_group}'.")

        out_of_scope = group_cfg.is_out_of_scope if is_out_of_scope is None else is_out_of_scope
        resolved_rate = group_cfg.vat_rate if vat_rate is None else ensure_strict_decimal(vat_rate, "vat_rate")
        if out_of_scope:
            resolved_rate = Decimal("0")

        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            before_item = uow.catalog.get_item(code)
            item = SalesItem(
                code=code,
                designation=designation,
                tax_group=tax_group,
                vat_rate=resolved_rate,
                is_out_of_scope=out_of_scope,
                is_active=is_active,
            )
            uow.catalog.save_item(item)
            batch.add_audit(
                user=actor.username,
                action="SAVE_CATALOG_ITEM",
                entity="catalog_item",
                entity_id=item.code,
                before={"is_active": str(before_item.is_active)} if before_item else None,
                after={
                    "tax_group": item.tax_group,
                    "vat_rate": str(item.vat_rate),
                    "is_out_of_scope": str(item.is_out_of_scope),
                    "is_active": str(item.is_active),
                },
            )
        batch.emit(self._deps)
        return item

    def create_price_list(
        self,
        actor: User,
        *,
        name: str,
        currency: Currency,
        prices: dict[str, Money] | None = None,
        is_active: bool = True,
        price_list_id: str | None = None,
    ) -> PriceList:
        """Cree une liste de prix dans une devise unique."""
        self._require(actor, "ventes.admin")
        pl_id = price_list_id or self._deps.id_factory()
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            if uow.catalog.get_price_list(pl_id) is not None:
                raise SalesConflictError(f"Une liste de prix '{pl_id}' existe deja.")
            plist = PriceList(
                id=pl_id,
                name=name,
                currency=currency,
                prices=dict(prices or {}),
                is_active=is_active,
            )
            uow.catalog.save_price_list(plist)
            batch.add_audit(
                user=actor.username,
                action="CREATE_PRICE_LIST",
                entity="price_list",
                entity_id=plist.id,
                before=None,
                after={"name": plist.name, "currency": plist.currency.value, "is_active": str(plist.is_active)},
            )
        batch.emit(self._deps)
        return plist

    def set_unit_price(
        self,
        actor: User,
        price_list_id: str,
        item_code: str,
        unit_price: Money,
    ) -> PriceList:
        """Definit ou met a jour le prix unitaire HT d'un article dans une liste de prix."""
        self._require(actor, "ventes.admin")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            plist = uow.catalog.get_price_list(price_list_id)
            if plist is None:
                raise SalesNotFoundError(f"Liste de prix introuvable : '{price_list_id}'.")
            if uow.catalog.get_item(item_code) is None:
                raise SalesNotFoundError(f"Article introuvable dans le catalogue : '{item_code}'.")
            before_price = plist.get_unit_price(item_code)
            updated = plist.with_price(item_code, unit_price)
            uow.catalog.save_price_list(updated)
            batch.add_audit(
                user=actor.username,
                action="SET_UNIT_PRICE",
                entity="price_list",
                entity_id=updated.id,
                before={"item_code": item_code, "amount": str(before_price.amount)} if before_price else None,
                after={"item_code": item_code, "amount": str(unit_price.amount), "currency": unit_price.currency.value},
            )
        batch.emit(self._deps)
        return updated
