"""Chargement dynamique des modules depuis un catalogue (ADR-002).

Un module absent, archivé ou dont l'import échoue est ignoré et signalé : il ne bloque jamais
le démarrage. Les modules ne sont ni instanciés ni démarrés ici (briques ultérieures).
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Literal

from vorpal.core.module import BaseModule


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    module_path: str
    class_name: str
    status: Literal["active", "archived"] = "active"


@dataclass
class LoadResult:
    loaded: dict[str, type[BaseModule]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def load_modules(entries: list[CatalogEntry], logger: logging.Logger | None = None) -> LoadResult:
    log = logger or logging.getLogger("vorpal.plateforme")
    result = LoadResult()
    for entry in entries:
        if entry.status == "archived":
            result.skipped[entry.name] = "archivé"
            continue
        try:
            cls = getattr(importlib.import_module(entry.module_path), entry.class_name)
        except (ImportError, AttributeError) as exc:
            log.warning("Module %s indisponible : %s", entry.name, exc)
            result.skipped[entry.name] = f"indisponible : {exc}"
            continue
        if not (isinstance(cls, type) and issubclass(cls, BaseModule)):
            result.skipped[entry.name] = "classe invalide (BaseModule attendu)"
            continue
        result.loaded[entry.name] = cls
    return result
