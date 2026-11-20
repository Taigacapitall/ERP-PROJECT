from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vorpal.core.context import AppContext


class ModuleState(str, Enum):
    REGISTERED = "REGISTERED"
    INSTALLED = "INSTALLED"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ModuleManifest:
    name: str
    version: str
    depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    subscribes: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)


class BaseModule(ABC):
    manifest: ModuleManifest

    @abstractmethod
    def install(self, ctx: AppContext) -> None:
        """Installe le module (schéma, permissions, initialisations)."""

    @abstractmethod
    def start(self, ctx: AppContext) -> None:
        """Démarre le module (services, abonnements événements)."""

    @abstractmethod
    def stop(self) -> None:
        """Arrête proprement le module."""

    @abstractmethod
    def healthcheck(self) -> bool:
        """Retourne True si le module est sain."""
