from __future__ import annotations

from typing import TypeVar

from vorpal.core.errors import ConflictError, NotFoundError, ValidationError

TService = TypeVar("TService")


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[type[object], object] = {}

    def register(self, interface: type[TService], impl: TService, replace: bool = False) -> None:
        if not isinstance(interface, type):
            raise ValidationError("L'interface enregistrée doit être un type.")
        if interface in self._services and not replace:
            raise ConflictError(
                f"Service déjà enregistré pour l'interface: {interface.__name__} (utiliser replace=True)."
            )
        self._services[interface] = impl

    def get(self, interface: type[TService]) -> TService:
        impl = self.get_optional(interface)
        if impl is None:
            raise NotFoundError(f"Service non enregistré pour l'interface: {interface.__name__}")
        return impl

    def get_optional(self, interface: type[TService]) -> TService | None:
        return self._services.get(interface)  # type: ignore[return-value]
