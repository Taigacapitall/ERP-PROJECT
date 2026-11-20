"""Implementations SQLAlchemy et memoire de SalesUnitOfWork."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from vorpal.core import UnitOfWork
from vorpal.modules.ventes.adapters.persistence.memory_repositories import (
    InMemoryCatalogRepository,
    InMemoryCustomerRepository,
    InMemoryInvoiceRepository,
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
    InMemoryQuoteRepository,
    InMemoryReminderRepository,
    InMemorySalesStore,
    SalesStoreSnapshot,
)
from vorpal.modules.ventes.adapters.persistence.numbering import (
    InMemoryNumberSequence,
    SqlNumberSequence,
)
from vorpal.modules.ventes.adapters.persistence.sql_repositories import (
    SqlCatalogRepository,
    SqlCustomerRepository,
    SqlInvoiceRepository,
    SqlOrderRepository,
    SqlPaymentRepository,
    SqlQuoteRepository,
    SqlReminderRepository,
)
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.settings import SalesSettings


class SqlSalesUnitOfWork(SalesUnitOfWork):
    """Unite de travail SQLAlchemy composant le UnitOfWork du Core."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: SalesSettings,
    ) -> None:
        self._core_uow = UnitOfWork(session_factory)
        self._settings = settings

    def _bind_repositories(self, session: Session) -> None:
        self.customers = SqlCustomerRepository(session)
        self.catalog = SqlCatalogRepository(session)
        self.quotes = SqlQuoteRepository(session)
        self.orders = SqlOrderRepository(session)
        self.invoices = SqlInvoiceRepository(session)
        self.payments = SqlPaymentRepository(session)
        self.reminders = SqlReminderRepository(session)
        self.numbers = SqlNumberSequence(session, self._settings.numbering)

    def __enter__(self) -> SalesUnitOfWork:
        self._core_uow.__enter__()
        session = self._core_uow.session
        assert session is not None
        self._bind_repositories(session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._core_uow.__exit__(exc_type, exc_val, exc_tb)

    def commit(self) -> None:
        session = self._core_uow.session
        if session is None:
            raise RuntimeError("La transaction n'est pas ouverte.")
        session.commit()

    def rollback(self) -> None:
        session = self._core_uow.session
        if session is None:
            raise RuntimeError("La transaction n'est pas ouverte.")
        session.rollback()


class InMemorySalesUnitOfWork(SalesUnitOfWork):
    """Unite de travail en memoire avec rollback transactionnel par snapshot."""

    def __init__(self, store: InMemorySalesStore, settings: SalesSettings) -> None:
        self._store = store
        self._settings = settings
        self._snapshot: SalesStoreSnapshot | None = None
        self._seq = InMemoryNumberSequence(settings.numbering, store.sequences)
        self.customers = InMemoryCustomerRepository(store)
        self.catalog = InMemoryCatalogRepository(store)
        self.quotes = InMemoryQuoteRepository(store)
        self.orders = InMemoryOrderRepository(store)
        self.invoices = InMemoryInvoiceRepository(store)
        self.payments = InMemoryPaymentRepository(store)
        self.reminders = InMemoryReminderRepository(store)
        self.numbers = self._seq

    def __enter__(self) -> SalesUnitOfWork:
        self._snapshot = self._store.snapshot()
        self._seq.restore_state(self._snapshot.sequences)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self._snapshot = None

    def commit(self) -> None:
        self._store.sequences = self._seq.clone_state()

    def rollback(self) -> None:
        if self._snapshot is not None:
            self._store.restore(self._snapshot)
            self._seq.restore_state(self._snapshot.sequences)
