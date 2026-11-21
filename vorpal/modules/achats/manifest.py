"""Manifeste et permissions du module Achats."""

from __future__ import annotations

from vorpal.core.module import ModuleManifest

ACHATS_PERMISSIONS: tuple[str, ...] = (
    "achats.view",
    "achats.create",
    "achats.approve",
    "achats.receive",
    "achats.landed_cost",
    "achats.admin",
)

ACHATS_EVENTS_EMITTED: tuple[str, ...] = (
    "achats.purchase_order.confirmed",
    "achats.goods_receipt.validated",
    "achats.landed_cost.allocated",
    "achats.supplier_invoice_import.requested",
)

ACHATS_MANIFEST = ModuleManifest(
    name="achats",
    version="0.1.0",
    depends=[],
    provides=["purchase_management", "goods_receipt", "landed_cost_allocation"],
    subscribes=[],
    permissions=list(ACHATS_PERMISSIONS),
)
