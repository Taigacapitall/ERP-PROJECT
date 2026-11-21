"""Manifeste et declarations du module Conformite DGI."""

from __future__ import annotations

from vorpal.core.module import ModuleManifest

DGI_EVENTS_LISTENED: tuple[str, ...] = (
    "invoice.ready_for_certification",
)

DGI_EVENTS_EMITTED: tuple[str, ...] = (
    "invoice.certified",
    "invoice.rejected",
    "credit_note.certified",
)

DGI_PERMISSIONS: tuple[str, ...] = (
    "dgi.view",
    "dgi.certify",
    "dgi.credit_note",
    "dgi.admin",
)

DGI_MANIFEST = ModuleManifest(
    name="dgi",
    version="0.1.0",
    description="Conformite fiscale DGI RDC (MCF / e-MCF, 12 mentions, avoirs, journal, homologation)",
    dependencies=("core",),
    permissions=DGI_PERMISSIONS,
    events_listened=DGI_EVENTS_LISTENED,
    events_emitted=DGI_EVENTS_EMITTED,
)
