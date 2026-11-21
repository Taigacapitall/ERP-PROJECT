"""Manifeste et permissions du module Ventes."""

from __future__ import annotations

from vorpal.core.module import ModuleManifest

VENTES_PERMISSIONS: tuple[str, ...] = (
    "ventes.view",
    "ventes.create",
    "ventes.confirm",
    "ventes.admin",
)

VENTES_EVENTS_LISTENED: tuple[str, ...] = (
    "invoice.certified",
    "invoice.rejected",
)

VENTES_EVENTS_EMITTED: tuple[str, ...] = (
    "invoice.ready_for_certification",
    "payment.received",
)

# Aucune dependance declaree vers DGI : les deux modules ne communiquent que par evenements.
VENTES_MANIFEST = ModuleManifest(
    name="ventes",
    version="0.1.0",
    depends=[],
    provides=["sales_invoicing"],
    subscribes=list(VENTES_EVENTS_LISTENED),
    permissions=list(VENTES_PERMISSIONS),
)
