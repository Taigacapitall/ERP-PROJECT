"""Tests d'architecture (brique A4) : ADR-001, 002, 008 et 012."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

from vorpal.plateforme import catalog
from vorpal.plateforme.catalog import CatalogEntry, load_modules

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "vorpal" / "modules"

# Dépendances interdites dans web/packages/offline-core (ADR-012) : DOM et service worker.
FORBIDDEN_OFFLINE_CORE = re.compile(
    r"\b(document|window)\s*\.|\bnavigator\s*\.\s*serviceWorker|\bServiceWorker\w*|"
    r"\bself\s*\.\s*registration|\bimportScripts\s*\(|\blocalStorage\b|\bHTMLElement\b|"
    r"""from\s+['"]react|lib\s*:\s*\[[^\]]*['"]dom""",
)


def imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def offline_core_violations(package_dir: Path) -> list[str]:
    """Fichiers TypeScript du paquet qui utilisent le DOM ou le service worker."""
    found: list[str] = []
    for f in sorted(package_dir.rglob("*")):
        if f.suffix in {".ts", ".tsx", ".js", ".json"} and "node_modules" not in f.parts:
            if FORBIDDEN_OFFLINE_CORE.search(f.read_text(encoding="utf-8")):
                found.append(str(f.relative_to(package_dir)))
    return found


def test_no_module_or_core_imports_plateforme() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for base in (MODULES, ROOT / "vorpal" / "core")
        for p in base.rglob("*.py")
        if any(n == "vorpal.plateforme" or n.startswith("vorpal.plateforme.") for n in imported_names(p))
    ]
    assert offenders == []


def test_modules_do_not_import_each_other() -> None:
    offenders: list[str] = []
    for p in MODULES.rglob("*.py"):
        own = p.relative_to(MODULES).parts[0]
        for name in imported_names(p):
            parts = name.split(".")
            if parts[:2] == ["vorpal", "modules"] and len(parts) > 2 and parts[2] != own:
                offenders.append(f"{p.relative_to(ROOT)} -> {name}")
    assert offenders == []


def test_plateforme_imports_only_core_not_modules() -> None:
    offenders = [
        f"{p.relative_to(ROOT)} -> {n}"
        for p in (ROOT / "vorpal" / "plateforme").rglob("*.py")
        for n in imported_names(p)
        if n == "vorpal.modules" or n.startswith("vorpal.modules.")
    ]
    assert offenders == []


def test_offline_core_has_no_dom_or_service_worker() -> None:
    package = ROOT / "web" / "packages" / "offline-core"
    # Paquet créé en phase P3b : tant qu'il n'existe pas, il n'y a rien à violer (le détecteur
    # est vérifié par test_offline_core_scanner_detects_violations).
    assert not package.exists() or offline_core_violations(package) == []


def test_offline_core_scanner_detects_violations(tmp_path: Path) -> None:
    (tmp_path / "clean.ts").write_text("export const q = new Map<string, number>();\n")
    (tmp_path / "dom.ts").write_text("const el = document.getElementById('x');\n")
    (tmp_path / "sw.ts").write_text("navigator.serviceWorker.register('/sw.js');\n")
    (tmp_path / "win.ts").write_text("window.addEventListener('online', f);\n")
    assert offline_core_violations(tmp_path) == ["dom.ts", "sw.ts", "win.ts"]


def test_startup_without_absent_or_archived_module(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    imported: list[str] = []
    real_import = catalog.importlib.import_module

    def spy(name: str, package: str | None = None) -> object:
        imported.append(name)
        return real_import(name, package)

    monkeypatch.setattr(catalog.importlib, "import_module", spy)
    entries = [
        CatalogEntry("absent", "vorpal.modules.module_inexistant_xyz.module", "XModule"),
        CatalogEntry("archive", "vorpal.modules.module_archive_xyz.module", "YModule", "archived"),
        CatalogEntry("pas_un_module", "logging", "Logger"),
        CatalogEntry("mauvaise_classe", "logging", "ClasseInexistante"),
    ]
    with caplog.at_level(logging.WARNING):
        result = load_modules(entries)
    assert result.loaded == {}
    assert set(result.skipped) == {"absent", "archive", "pas_un_module", "mauvaise_classe"}
    assert "vorpal.modules.module_archive_xyz.module" not in imported


def test_present_module_is_loaded_next_to_absent_one() -> None:
    entries = [
        CatalogEntry("absent", "vorpal.modules.module_inexistant_xyz.module", "XModule"),
        CatalogEntry("stock", "vorpal.modules.stock.module", "StockModule"),
    ]
    result = load_modules(entries)
    assert list(result.loaded) == ["stock"]
    assert list(result.skipped) == ["absent"]
