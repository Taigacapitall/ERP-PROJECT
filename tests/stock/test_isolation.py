"""Garde-fou d'architecture : le module Stock ne depend que du Core (jamais d'un autre module)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

STOCK_DIR = Path(__file__).resolve().parents[2] / "vorpal" / "modules" / "stock"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_stock_never_imports_another_business_module() -> None:
    offenders = [
        f"{path.name}: {module}"
        for path in STOCK_DIR.rglob("*.py")
        for module in imported_modules(path)
        if module.startswith("vorpal.modules.") and not module.startswith("vorpal.modules.stock")
    ]
    assert offenders == []


def test_only_core_stdlib_and_sqlalchemy_are_imported() -> None:
    allowed = {"vorpal", "sqlalchemy"} | set(sys.stdlib_module_names)
    offenders = [
        f"{path.name}: {module}"
        for path in STOCK_DIR.rglob("*.py")
        for module in imported_modules(path)
        if module.split(".")[0] not in allowed
    ]
    assert offenders == []


def test_sqlalchemy_is_confined_to_persistence_and_module_root() -> None:
    offenders = [
        path.name
        for path in STOCK_DIR.rglob("*.py")
        if any(m.startswith("sqlalchemy") for m in imported_modules(path))
        and path.name != "module.py"
        and "persistence" not in path.parts
    ]
    assert offenders == []


def test_no_float_literal_in_domain_and_services() -> None:
    """Montants et quantites : Decimal uniquement (les controles isinstance(x, float) restent permis)."""
    offenders = [
        f"{path.name}: {node.value}"
        for folder in ("domain", "services")
        for path in (STOCK_DIR / folder).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert offenders == []
