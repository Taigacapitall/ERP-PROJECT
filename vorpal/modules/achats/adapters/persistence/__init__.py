"""Persistance du module Achats."""

from vorpal.modules.achats.adapters.persistence.mappers import (
    landed_cost_from_model,
    landed_cost_to_model,
    order_from_model,
    order_to_model,
    receipt_from_model,
    receipt_to_model,
    supplier_from_model,
    supplier_to_model,
)
from vorpal.modules.achats.adapters.persistence.memory_repositories import (
    InMemoryGoodsReceiptRepository,
    InMemoryLandedCostRepository,
    InMemoryPurchaseNumberSequence,
    InMemoryPurchaseOrderRepository,
    InMemoryPurchaseUnitOfWork,
    InMemorySupplierRepository,
)
from vorpal.modules.achats.adapters.persistence.migrations import (
    ACHATS_MIGRATIONS,
    ACHATS_SCHEMA_VERSION,
    apply_achats_migrations,
)
from vorpal.modules.achats.adapters.persistence.numbering import SqlPurchaseNumberSequence
from vorpal.modules.achats.adapters.persistence.sql_repositories import (
    SqlGoodsReceiptRepository,
    SqlLandedCostRepository,
    SqlPurchaseOrderRepository,
    SqlSupplierRepository,
)
from vorpal.modules.achats.adapters.persistence.types import DecimalText
from vorpal.modules.achats.adapters.persistence.unit_of_work import SqlPurchaseUnitOfWork

__all__ = [
    "ACHATS_MIGRATIONS",
    "ACHATS_SCHEMA_VERSION",
    "DecimalText",
    "InMemoryGoodsReceiptRepository",
    "InMemoryLandedCostRepository",
    "InMemoryPurchaseNumberSequence",
    "InMemoryPurchaseOrderRepository",
    "InMemoryPurchaseUnitOfWork",
    "InMemorySupplierRepository",
    "SqlGoodsReceiptRepository",
    "SqlLandedCostRepository",
    "SqlPurchaseNumberSequence",
    "SqlPurchaseOrderRepository",
    "SqlPurchaseUnitOfWork",
    "SqlSupplierRepository",
    "apply_achats_migrations",
    "landed_cost_from_model",
    "landed_cost_to_model",
    "order_from_model",
    "order_to_model",
    "receipt_from_model",
    "receipt_to_model",
    "supplier_from_model",
    "supplier_to_model",
]
