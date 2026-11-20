from __future__ import annotations

from collections import deque
import logging

from vorpal.core.context import AppContext
from vorpal.core.errors import ConflictError, NotFoundError, ValidationError
from vorpal.core.module import BaseModule, ModuleState


class ModuleRegistry:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._modules: dict[str, BaseModule] = {}
        self._states: dict[str, ModuleState] = {}
        self._start_order: list[str] = []
        self._logger = logger or logging.getLogger("vorpal.core.registry")

    def register(self, module: BaseModule) -> None:
        name = module.manifest.name
        if not name:
            raise ValidationError("Le manifeste du module doit avoir un nom non vide.")
        if name in self._modules:
            raise ConflictError(f"Module déjà enregistré: {name}")
        self._modules[name] = module
        self._states[name] = ModuleState.REGISTERED

    def get_state(self, module_name: str) -> ModuleState:
        if module_name not in self._states:
            raise NotFoundError(f"Module inconnu: {module_name}")
        return self._states[module_name]

    def resolve_order(self) -> list[str]:
        for name, mod in self._modules.items():
            for dep in mod.manifest.depends:
                if dep not in self._modules:
                    raise NotFoundError(
                        f"Dépendance manquante pour '{name}': module '{dep}' non enregistré."
                    )

        in_degree: dict[str, int] = {name: 0 for name in self._modules}
        dependents: dict[str, list[str]] = {name: [] for name in self._modules}

        for name, mod in self._modules.items():
            for dep in mod.manifest.depends:
                in_degree[name] += 1
                dependents[dep].append(name)

        queue: deque[str] = deque([n for n, deg in in_degree.items() if deg == 0])
        ordered: list[str] = []

        while queue:
            current = queue.popleft()
            ordered.append(current)
            for child in dependents[current]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(ordered) != len(self._modules):
            raise ConflictError("Cycle détecté dans les dépendances des modules.")

        return ordered

    def start_all(self, ctx: AppContext) -> None:
        ordered = self.resolve_order()
        self._start_order = []

        for name in ordered:
            mod = self._modules[name]
            failed_deps = [
                dep for dep in mod.manifest.depends if self._states.get(dep) == ModuleState.FAILED
            ]
            if failed_deps:
                self._states[name] = ModuleState.FAILED
                self._logger.error(
                    "Module '%s' marqué FAILED: dépendance(s) en échec: %s",
                    name,
                    failed_deps,
                )
                continue

            try:
                ctx.authz.register_module_permissions(name, mod.manifest.permissions)
                mod.install(ctx)
                self._states[name] = ModuleState.INSTALLED
                mod.start(ctx)
                self._states[name] = ModuleState.RUNNING
                self._start_order.append(name)
            except Exception as exc:
                self._states[name] = ModuleState.FAILED
                self._logger.exception("Échec du démarrage du module '%s': %s", name, exc)

    def stop_all(self) -> None:
        for name in reversed(self._start_order):
            mod = self._modules[name]
            try:
                mod.stop()
                self._states[name] = ModuleState.STOPPED
            except Exception as exc:
                self._states[name] = ModuleState.FAILED
                self._logger.exception("Erreur lors de l'arrêt du module '%s': %s", name, exc)
