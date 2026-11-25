"""Manifeste, permissions et evenements du module Stock."""

from __future__ import annotations

from vorpal.core.module import ModuleManifest

STOCK_PERMISSIONS: tuple[str, ...] = (
    "stock.view",
    "stock.create",
    "stock.inventory",
    "stock.admin",
)

# Evenements ecoutes : les noms sont ceux publies par le module Achats. Stock ne l'importe
# jamais (regle du Core : les modules ne se connaissent pas, ils communiquent par evenements).
EVENT_GOODS_RECEIPT_VALIDATED = "achats.goods_receipt.validated"
EVENT_LANDED_COST_ALLOCATED = "achats.landed_cost.allocated"

STOCK_EVENTS_LISTENED: tuple[str, ...] = (
    EVENT_GOODS_RECEIPT_VALIDATED,
    EVENT_LANDED_COST_ALLOCATED,
)

EVENT_MOVEMENT_RECORDED = "stock.movement.recorded"
EVENT_LEVEL_LOW = "stock.level.low"
EVENT_INVENTORY_VALIDATED = "stock.inventory.validated"
EVENT_LANDED_COST_NOT_CAPITALIZED = "stock.landed_cost.not_capitalized"

STOCK_EVENTS_EMITTED: tuple[str, ...] = (
    EVENT_MOVEMENT_RECORDED,
    EVENT_LEVEL_LOW,
    EVENT_INVENTORY_VALIDATED,
    EVENT_LANDED_COST_NOT_CAPITALIZED,
)

STOCK_MANIFEST = ModuleManifest(
    name="stock",
    version="0.1.0",
    depends=[],
    provides=["stock_management", "stock_valuation", "inventory_count"],
    subscribes=list(STOCK_EVENTS_LISTENED),
    permissions=list(STOCK_PERMISSIONS),
)
