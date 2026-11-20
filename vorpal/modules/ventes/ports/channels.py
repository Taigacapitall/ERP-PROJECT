"""Interfaces de canaux d'envoi et points d'extension futurs (V1/V2/V3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from vorpal.core import Money
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import SalesInvoice
from vorpal.modules.ventes.domain.orders import SalesOrder
from vorpal.modules.ventes.domain.reminders import Reminder


class ReminderChannel(ABC):
    """Canal d'expedition d'une relance (e-mail, SMS, WhatsApp)."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Identifiant du canal supporte (ex: 'email', 'sms', 'whatsapp')."""
        ...

    @abstractmethod
    def send(self, reminder: Reminder, customer: Customer) -> None:
        """Expedie la relance au client."""
        ...


@runtime_checkable
class CreditLimitPolicyPort(Protocol):
    """Point d'extension prevu (V1 - non implemente) : verification du plafond de credit client."""

    def check_credit_available(self, customer: Customer, additional_amount: Money) -> bool:
        """Retourne True si l'encours autorise couvre le nouveau montant."""
        ...


@runtime_checkable
class MarginCalculatorPort(Protocol):
    """Point d'extension prevu (V1 - non implemente) : calcul de marge par ligne."""

    def compute_line_margin(self, item_code: str, net_unit_price_ht: Money) -> Money | None:
        """Calcule la marge unitaire si le cout d'achat est disponible."""
        ...


@runtime_checkable
class StockReservationPort(Protocol):
    """Point d'extension prevu (module Stocks absent) : reservation de stock a la commande."""

    def reserve_for_order(self, order: SalesOrder) -> None:
        """Reserve les quantites commandees si le module Stocks est present."""
        ...

    def release_for_order(self, order: SalesOrder) -> None:
        """Libere les reservations lors d'une annulation."""
        ...


@runtime_checkable
class OnlinePaymentGatewayPort(Protocol):
    """Point d'extension prevu (V3 - non implemente) : paiement en ligne depuis portail client."""

    def create_payment_link(self, invoice: SalesInvoice) -> str:
        """Genere un lien de paiement en ligne pour une facture."""
        ...
