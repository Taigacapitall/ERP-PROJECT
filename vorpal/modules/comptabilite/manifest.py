"""Manifeste et permissions du module Comptabilité native."""

from __future__ import annotations

from vorpal.core.module import ModuleManifest
from vorpal.modules.comptabilite.events import LISTENED_EVENTS
from vorpal.modules.comptabilite.settings import PERM_ADMIN, PERM_POST, PERM_VIEW

COMPTA_PERMISSIONS: tuple[str, ...] = (PERM_VIEW, PERM_POST, PERM_ADMIN)
COMPTA_EVENTS_EMITTED: tuple[str, ...] = ("journal.entry.posted",)

# Ne pas enregistrer en même temps que `comptabilite_odoo` : les deux fournissent `accounting_posting`.
COMPTA_MANIFEST = ModuleManifest(
    name="comptabilite",
    version="0.1.0",
    depends=[],
    provides=["accounting_posting", "general_ledger"],
    subscribes=list(LISTENED_EVENTS),
    permissions=list(COMPTA_PERMISSIONS),
)
