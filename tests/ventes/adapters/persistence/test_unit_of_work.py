"""Tests des unités de travail SQL et mémoire."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vorpal.modules.ventes.adapters.persistence.models import Base
from vorpal.modules.ventes.adapters.persistence.memory_repositories import (
    InMemorySalesStore,
)
from vorpal.modules.ventes.adapters.persistence.unit_of_work import (
    InMemorySalesUnitOfWork,
    SqlSalesUnitOfWork,
)
from vorpal.modules.ventes.settings import SalesSettings, SellerSettings


@pytest.fixture
def settings() -> SalesSettings:
    return SalesSettings(
        seller=SellerSettings(
            name="Entreprise test",
            nif="A123456",
        ),
    )


@pytest.fixture
def store() -> InMemorySalesStore:
    return InMemorySalesStore()


@pytest.fixture
def sql_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    yield factory

    engine.dispose()


def test_sql_uow_binds_all_repositories(
    sql_factory,
    settings: SalesSettings,
) -> None:
    with SqlSalesUnitOfWork(sql_factory, settings) as uow:
        assert uow.customers is not None
        assert uow.catalog is not None
        assert uow.quotes is not None
        assert uow.orders is not None
        assert uow.invoices is not None
        assert uow.payments is not None
        assert uow.reminders is not None
        assert uow.numbers is not None
        assert uow._core_uow.session is not None

    assert uow._core_uow.session is None


def test_sql_uow_commit(
    sql_factory,
    settings: SalesSettings,
) -> None:
    uow = SqlSalesUnitOfWork(sql_factory, settings)

    with uow:
        uow.commit()
        assert uow._core_uow.session is not None

    assert uow._core_uow.session is None


def test_sql_uow_rollback(
    sql_factory,
    settings: SalesSettings,
) -> None:
    uow = SqlSalesUnitOfWork(sql_factory, settings)

    with uow:
        uow.rollback()
        assert uow._core_uow.session is not None

    assert uow._core_uow.session is None


@pytest.mark.parametrize("method", ["commit", "rollback"])
def test_sql_uow_rejects_operations_without_open_transaction(
    sql_factory,
    settings: SalesSettings,
    method: str,
) -> None:
    uow = SqlSalesUnitOfWork(sql_factory, settings)

    with pytest.raises(RuntimeError, match="transaction n'est pas ouverte"):
        getattr(uow, method)()


def test_sql_uow_context_rolls_back_on_exception(
    sql_factory,
    settings: SalesSettings,
) -> None:
    uow = SqlSalesUnitOfWork(sql_factory, settings)

    with pytest.raises(ValueError, match="erreur de test"):
        with uow:
            raise ValueError("erreur de test")

    assert uow._core_uow.session is None


def test_memory_uow_binds_all_repositories(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    with InMemorySalesUnitOfWork(store, settings) as uow:
        assert uow.customers is not None
        assert uow.catalog is not None
        assert uow.quotes is not None
        assert uow.orders is not None
        assert uow.invoices is not None
        assert uow.payments is not None
        assert uow.reminders is not None
        assert uow.numbers is uow._seq

    assert uow._snapshot is None


def test_memory_uow_commit_preserves_changes(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    with InMemorySalesUnitOfWork(store, settings) as uow:
        uow.numbers.next_number("quote", 2026)

    assert store.sequences == {("quote", 2026): 1}


def test_memory_uow_rollback_restores_sequences(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    store.sequences[("quote", 2026)] = 4

    with pytest.raises(ValueError, match="erreur de test"):
        with InMemorySalesUnitOfWork(store, settings) as uow:
            uow.numbers.next_number("quote", 2026)
            raise ValueError("erreur de test")

    assert store.sequences == {("quote", 2026): 4}


def test_memory_uow_rollback_restores_store_collections(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    store.customers["avant"] = object()

    with pytest.raises(ValueError):
        with InMemorySalesUnitOfWork(store, settings):
            store.customers["apres"] = object()
            raise ValueError("rollback")

    assert "avant" in store.customers
    assert "apres" not in store.customers


def test_memory_uow_manual_rollback(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    store.sequences[("invoice", 2026)] = 2

    with InMemorySalesUnitOfWork(store, settings) as uow:
        uow.numbers.next_number("invoice", 2026)
        uow.rollback()

    assert store.sequences == {("invoice", 2026): 2}


def test_memory_uow_manual_commit(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    with InMemorySalesUnitOfWork(store, settings) as uow:
        uow.numbers.next_number("payment", 2026)
        uow.commit()

    assert store.sequences == {("payment", 2026): 1}


def test_memory_uow_empty_context_commits(
    store: InMemorySalesStore,
    settings: SalesSettings,
) -> None:
    with InMemorySalesUnitOfWork(store, settings):
        pass

    assert store.sequences == {}
