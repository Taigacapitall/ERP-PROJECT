"""Sous-package de persistance du module Ventes (types, ORM, mappers, repositories, UoW, migrations)."""

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
from vorpal.modules.ventes.adapters.persistence.migrations import MIGRATIONS, create_schema_v1
from vorpal.modules.ventes.adapters.persistence.numbering import (
    InMemoryNumberSequence,
    SqlNumberSequence,
    format_document_number,
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
from vorpal.modules.ventes.adapters.persistence.types import DecimalText
from vorpal.modules.ventes.adapters.persistence.unit_of_work import (
    InMemorySalesUnitOfWork,
    SqlSalesUnitOfWork,
)

__all__ = [
    "MIGRATIONS",
    "DecimalText",
    "InMemoryCatalogRepository",
    "InMemoryCustomerRepository",
    "InMemoryInvoiceRepository",
    "InMemoryNumberSequence",
    "InMemoryOrderRepository",
    "InMemoryPaymentRepository",
    "InMemoryQuoteRepository",
    "InMemoryReminderRepository",
    "InMemorySalesStore",
    "InMemorySalesUnitOfWork",
    "SalesStoreSnapshot",
    "SqlCatalogRepository",
    "SqlCustomerRepository",
    "SqlInvoiceRepository",
    "SqlNumberSequence",
    "SqlOrderRepository",
    "SqlPaymentRepository",
    "SqlQuoteRepository",
    "SqlReminderRepository",
    "SqlSalesUnitOfWork",
    "create_schema_v1",
    "format_document_number",
]
