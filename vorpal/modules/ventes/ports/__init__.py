"""Interfaces (ports) de persistance, numerotation, publication et canaux du module Ventes."""

from vorpal.modules.ventes.ports.channels import (
    CreditLimitPolicyPort,
    MarginCalculatorPort,
    OnlinePaymentGatewayPort,
    ReminderChannel,
    StockReservationPort,
)
from vorpal.modules.ventes.ports.publisher import SalesEventPublisher
from vorpal.modules.ventes.ports.repositories import (
    CatalogRepository,
    CustomerRepository,
    InvoiceRepository,
    NumberSequence,
    OrderRepository,
    PaymentRepository,
    QuoteRepository,
    ReminderRepository,
)
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork

__all__ = [
    "CatalogRepository",
    "CreditLimitPolicyPort",
    "CustomerRepository",
    "InvoiceRepository",
    "MarginCalculatorPort",
    "NumberSequence",
    "OnlinePaymentGatewayPort",
    "OrderRepository",
    "PaymentRepository",
    "QuoteRepository",
    "ReminderChannel",
    "ReminderRepository",
    "SalesEventPublisher",
    "SalesUnitOfWork",
    "StockReservationPort",
]
