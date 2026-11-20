from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Permission:
    code: str
    description: str = ""


@dataclass(frozen=True)
class Role:
    name: str
    permissions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class User:
    id: str
    username: str
    roles: tuple[Role, ...] = field(default_factory=tuple)
    direct_permissions: frozenset[str] = field(default_factory=frozenset)
    is_active: bool = True

    def effective_permissions(self) -> frozenset[str]:
        if not self.is_active:
            return frozenset()
        perms = set(self.direct_permissions)
        for role in self.roles:
            perms.update(role.permissions)
        return frozenset(perms)


class AuthorizationService:
    def __init__(self) -> None:
        self._declared_permissions: dict[str, set[str]] = {}

    def register_module_permissions(self, module_name: str, permissions: list[str]) -> None:
        self._declared_permissions.setdefault(module_name, set()).update(permissions)

    def is_permission_declared(self, permission: str) -> bool:
        return any(permission in perms for perms in self._declared_permissions.values())

    def check(self, user: User, permission: str) -> bool:
        if not user.is_active:
            return False
        return permission in user.effective_permissions()
