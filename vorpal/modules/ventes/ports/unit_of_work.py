"""Port d'unite de travail (Unit of Work) du module Ventes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType

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


class SalesUnitOfWork(ABC):
    """Unite de travail transactionnelle regroupant les repositories et la sequence."""

    customers: CustomerRepository
    catalog: CatalogRepository
    quotes: QuoteRepository
    orders: OrderRepository
    invoices: InvoiceRepository
    payments: PaymentRepository
    reminders: ReminderRepository
    numbers: NumberSequence

    @abstractmethod
    def __enter__(self) -> SalesUnitOfWork:
        """Ouvre une transaction."""
        ...

    @abstractmethod
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Valide si aucune exception n'est levee, sinon annule."""
        ...

    @abstractmethod
    def commit(self) -> None:
        """Valide explicitement la transaction courante."""
        ...

    @abstractmethod
    def rollback(self) -> None:
        """Annule explicitement la transaction courante."""
        ...
