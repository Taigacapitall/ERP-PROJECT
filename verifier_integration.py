#!/usr/bin/env python3
"""Vérification d'intégration Vorpal : dépôt local <-> zip livré, architecture, outils, chaîne Ventes->DGI->Compta.

Usage (depuis la racine du dépôt, venv actif) :
    python verifier_integration.py --zip ~/Downloads/vorpal_compta_integre.zip
Options : --repo CHEMIN (défaut .)   --skip-tools (pas de pytest/ruff/mypy)   --show-diff FICHIER (diff local/zip)
          --out CHEMIN.md (rapport ; défaut : dossier parent du dépôt)
Ne modifie AUCUN fichier du dépôt (seul le rapport .md est écrit, hors dépôt). Pas de commit, pas de push.
Statuts : OK | ECHEC | ALERTE | NON EXECUTE.  Priorités : P0 bloquant, P1 à corriger avant la suite, P2 hygiène.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", "build", "dist"}
SKIP_FILES = {".coverage"}
EXPECTED_TO_DIFFER = ("HANDOFF.md", "docs/", "pyproject.toml", ".gitignore")
CRUFT = re.compile(r"\.(bak|orig|tmp|patch|rej)$|\.step\d*\.bak$")
AREAS = {"core": "vorpal/core/", "dgi": "vorpal/modules/dgi/", "ventes": "vorpal/modules/ventes/",
         "achats": "vorpal/modules/achats/", "adapters.odoo": "vorpal/modules/adapters/odoo/"}
RESULTS: list[tuple[str, str, str, str]] = []   # (priorité, statut, contrôle, détail)


def rec(prio: str, status: str, name: str, detail: str = "") -> None:
    RESULTS.append((prio, status, name, detail))
    print(f"[{status:<11}] {prio:<4} {name}" + (f" : {detail}" if detail else ""))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def walk(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for f in files:
            if f in SKIP_FILES or f.endswith((".pyc", ".tgz", ".part")):
                continue
            p = Path(dirpath) / f
            out[p.relative_to(root).as_posix()] = p
    return out


def find_tree(zip_path: Path, tmp: Path) -> Path:
    """Extrait le zip (zips imbriqués acceptés) et retourne le dossier qui contient vorpal/core/__init__.py."""
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
    for _ in range(3):
        for hit in tmp.rglob("__init__.py"):
            if hit.parent.name == "core" and hit.parent.parent.name == "vorpal":
                return hit.parent.parent.parent
        inner = [p for p in tmp.rglob("*.zip") if "vorpal" in p.name]
        if not inner:
            break
        with zipfile.ZipFile(inner[0]) as z:
            z.extractall(tmp / "inner")
        tmp = tmp / "inner"
    raise SystemExit("ARRET : dossier vorpal/core introuvable dans le zip.")


def sh(cmd: list[str], cwd: Path, env_extra: dict[str, str] | None = None, timeout: int = 1200) -> tuple[int, str]:
    env = {**os.environ, **(env_extra or {})}
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout + r.stderr).rstrip()


# ---------------------------------------------------------------- A. dépôt local <-> zip
def compare_with_zip(repo: Path, ref: Path, show: str | None) -> None:
    a, b = walk(repo), walk(ref)
    same = [k for k in b if k in a and sha(a[k]) == sha(b[k])]
    diff = sorted(k for k in b if k in a and sha(a[k]) != sha(b[k]))
    missing = sorted(k for k in b if k not in a)           # dans le zip, absents en local
    extra = sorted(k for k in a if k not in b)             # en local, absents du zip
    expected = lambda k: k.startswith(EXPECTED_TO_DIFFER)  # noqa: E731
    code_diff = [k for k in diff if not expected(k)]
    rec("INFO", "OK", "comparaison locale/zip", f"{len(same)} identiques, {len(diff)} différents, {len(missing)} absents en local, {len(extra)} en plus en local")
    core = [k for k in code_diff + missing if k.startswith("vorpal/core/")]
    rec("P0", "OK" if not core else "ECHEC", "Core local = Core du zip", ", ".join(core[:8]))
    for area, prefix in AREAS.items():
        if area == "core":
            continue
        bad = [k for k in code_diff + missing if k.startswith(prefix)]
        rec("P1", "OK" if not bad else "ECHEC", f"module {area} local = zip", f"{len(bad)} fichier(s) : " + ", ".join(bad[:6]) if bad else "")
    tests = [k for k in code_diff + missing if k.startswith("tests/")]
    rec("P1", "OK" if not tests else "ALERTE", "tests locaux = zip", f"{len(tests)} fichier(s) : " + ", ".join(tests[:6]) if tests else "")
    addon = [k for k in code_diff + missing if k.startswith("odoo_addons/")]
    rec("P1", "OK" if not addon else "ECHEC", "addon Odoo local = zip", ", ".join(addon[:6]))
    if extra:
        rec("P2", "ALERTE", "fichiers locaux absents du zip (à examiner)", ", ".join(extra[:12]) + (" ..." if len(extra) > 12 else ""))
    if diff and any(expected(k) for k in diff):
        rec("INFO", "OK", "différences attendues (docs, HANDOFF, pyproject, .gitignore)", ", ".join(k for k in diff if expected(k))[:200])
    if show and show in a and show in b:
        print(f"\n--- diff local/zip : {show} ---")
        for i, line in enumerate(difflib.unified_diff(a[show].read_text(errors="replace").splitlines(), b[show].read_text(errors="replace").splitlines(), "local", "zip", lineterm="")):
            if i > 80:
                print("... (tronqué)")
                break
            print(line)


# ---------------------------------------------------------------- B. Core gelé
def core_frozen(repo: Path) -> None:
    code, out = sh(["git", "rev-parse", "--verify", "--quiet", "core-v1^{commit}"], repo)
    if code != 0:
        rec("P0", "NON EXECUTE", "Core identique au tag core-v1", "tag introuvable ou git indisponible")
        return
    _, d1 = sh(["git", "diff", "--name-status", "--no-renames", "core-v1", "--", "vorpal/core"], repo)
    _, d2 = sh(["git", "ls-files", "--others", "--exclude-standard", "--", "vorpal/core"], repo)
    extra = [x for x in d2.splitlines() if "__pycache__" not in x]
    rec("P0", "OK" if not d1 and not extra else "ECHEC", "vorpal/core identique au tag core-v1 (tests/core exclu : il a pu grandir)", (d1 + "\n" + "\n".join(extra)).strip().replace("\n", " | ")[:300])


# ---------------------------------------------------------------- C. architecture statique (AST)
def mod_of(rel: str) -> str | None:
    parts = rel.split("/")
    if len(parts) < 4 or parts[:2] != ["vorpal", "modules"]:
        return None
    return "adapters.odoo" if parts[2:4] == ["adapters", "odoo"] else parts[2]


def abs_import(rel: str, node: ast.AST) -> list[str]:
    pkg = rel.split("/")[:-1]
    if isinstance(node, ast.Import):
        return [n.name for n in node.names]
    assert isinstance(node, ast.ImportFrom)
    base = pkg[: len(pkg) - (node.level - 1)] if node.level else []
    mod = ".".join(base + (node.module.split(".") if node.module else []))
    return [mod] + [f"{mod}.{n.name}" for n in node.names]


def static_checks(repo: Path) -> None:
    files = {k: p for k, p in walk(repo).items() if k.endswith(".py")}
    cross: list[str] = []
    float_use: list[str] = []
    core_to_mod: list[str] = []
    addon_to_vorpal: list[str] = []
    for rel, p in files.items():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            rec("P0", "ECHEC", "syntaxe Python", f"{rel}: {exc}")
            continue
        own = mod_of(rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in abs_import(rel, node):
                    if target.startswith("vorpal.modules."):
                        rest = target.split(".")[2:]
                        other = "adapters.odoo" if rest[:2] == ["adapters", "odoo"] else rest[0]
                        if own and other != own and other != "adapters":
                            cross.append(f"{rel} -> {other}")
                        if rel.startswith("vorpal/core/"):
                            core_to_mod.append(rel)
                    if rel.startswith("odoo_addons/") and target.split(".")[0] == "vorpal":
                        addon_to_vorpal.append(rel)
            if own and not rel.startswith("tests/"):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float") or (isinstance(node, ast.Name) and node.id == "Float"):
                    float_use.append(f"{rel}:{getattr(node, 'lineno', '?')}")
    rec("P0", "OK" if not cross else "ECHEC", "aucun module n'en importe un autre", "; ".join(sorted(set(cross))[:8]))
    rec("P0", "OK" if not core_to_mod else "ECHEC", "le Core n'importe aucun module", ", ".join(sorted(set(core_to_mod))))
    rec("P1", "OK" if not addon_to_vorpal else "ALERTE", "l'addon Odoo n'importe pas vorpal", ", ".join(sorted(set(addon_to_vorpal))))
    boundary = [f for f in float_use if f.startswith("vorpal/modules/adapters/odoo/client.py")]   # Odoo XML-RPC n'accepte pas Decimal
    other = [f for f in float_use if f not in boundary]
    rec("P1", "OK" if not other else "ALERTE", "aucun float()/Float dans les modules (règle montants)", ", ".join(other[:8]))
    if boundary:
        rec("INFO", "ALERTE", "float() à la frontière XML-RPC Odoo (Decimal -> float imposé par Odoo ; hors timeout)", f"{len(boundary)} occurrence(s) dans adapters/odoo/client.py : vérifier l'arrondi des montants côté Odoo")

    # catalogue d'événements Core vs manifestes
    catalog = set(re.findall(r'^[A-Z_]+ = "([a-z_.]+)"', (repo / "vorpal/core/contracts/events.py").read_text(), re.M)) if (repo / "vorpal/core/contracts/events.py").exists() else set()
    emitted: dict[str, set[str]] = {}
    listened: dict[str, set[str]] = {}
    for rel, p in files.items():
        m = mod_of(rel)
        if not m or not (rel.endswith("manifest.py") or rel.endswith("handlers.py")):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) for t in n.targets if isinstance(t, ast.Name)}
        for n in tree.body:
            tgt = n.targets[0] if isinstance(n, ast.Assign) else (n.target if isinstance(n, ast.AnnAssign) else None)
            val = getattr(n, "value", None)
            if isinstance(tgt, ast.Name) and isinstance(val, ast.Tuple):
                names = {e.value if isinstance(e, ast.Constant) else consts.get(getattr(e, "id", ""), "") for e in val.elts}
                names.discard("")
                if "EMITTED" in tgt.id:
                    emitted.setdefault(m, set()).update(names)
                elif "LISTENED" in tgt.id or (tgt.id == "EVENTS" and m == "adapters.odoo"):
                    listened.setdefault(m, set()).update(names)
    used = set().union(*emitted.values(), *listened.values()) if (emitted or listened) else set()
    off = sorted(e for e in used if e not in catalog)
    rec("P1", "OK" if not off else "ALERTE", "événements émis/écoutés tous dans le catalogue du Core", ", ".join(off))
    no_prod = sorted(e for e in catalog if e not in set().union(*emitted.values()) and e not in {"event.handler_failed", "journal.entry.posted"}) if emitted else []
    rec("P1", "OK" if not no_prod else "ALERTE", "chaque événement du catalogue a un producteur", ", ".join(no_prod) + (" (ex. payroll.validated : RH/Paie non construit)" if no_prod else ""))
    prod_all = set().union(*emitted.values()) if emitted else set()
    cons_all = set().union(*listened.values()) if listened else set()
    orphan = sorted(e for e in prod_all if e not in cons_all and e not in {"journal.entry.posted"})
    rec("P1", "OK" if not orphan else "ALERTE", "chaque événement émis a un consommateur", ", ".join(orphan))
    print("    producteurs :", {k: sorted(v) for k, v in emitted.items()})
    print("    consommateurs :", {k: sorted(v) for k, v in listened.items()})

    # package-data : chaque dossier data/ de module doit être déclaré (sinon absent d'un paquet installé)
    pp = repo / "pyproject.toml"
    if pp.exists():
        pkg = tomllib.loads(pp.read_text()).get("tool", {}).get("setuptools", {}).get("package-data", {})
        missing = []
        for rel in walk(repo):
            m = re.match(r"(vorpal/modules/.+)/data/[^/]+\.json$", rel)
            if m and m.group(1).replace("/", ".") not in pkg:
                missing.append(m.group(1).replace("/", "."))
        rec("P1", "OK" if not missing else "ECHEC", "JSON des modules déclarés dans package-data", "manquant : " + ", ".join(sorted(set(missing))) if missing else "")

    cruft = sorted(k for k in walk(repo) if CRUFT.search(k))
    rec("P2", "OK" if not cruft else "ALERTE", "aucun fichier de sauvegarde (.bak/.step*.bak/.patch) dans l'arbre", ", ".join(cruft[:8]))
    todo: dict[str, int] = {}
    for rel, p in files.items():
        if "A_VALIDER" in p.read_text(errors="replace") and not rel.startswith("tests/"):
            todo[mod_of(rel) or rel.split("/")[0]] = todo.get(mod_of(rel) or rel.split("/")[0], 0) + p.read_text(errors="replace").count("A_VALIDER")
    rec("INFO", "OK", "inventaire TODO(A_VALIDER) par module", str(todo))


# ---------------------------------------------------------------- D. outils (pytest, couverture par module, ruff, mypy)
def tools(repo: Path) -> None:
    py = sys.executable
    code, out = sh([py, "-m", "pytest", "--cov=vorpal", "--cov-report=term", "-q", "-p", "no:cacheprovider"], repo)
    summ = next((ln for ln in reversed(out.splitlines()) if re.search(r"\b(passed|failed|error)", ln)), "sortie illisible")
    rec("P0", "OK" if code == 0 else "ECHEC", "pytest (suite complète)", summ.strip("= "))
    cov: dict[str, list[int]] = {k: [0, 0] for k in AREAS}
    for ln in out.splitlines():
        m = re.match(r"(vorpal/\S+\.py)\s+(\d+)\s+(\d+)\s+\d+%", ln)
        if m:
            for area, prefix in AREAS.items():
                if m.group(1).startswith(prefix):
                    cov[area][0] += int(m.group(2))
                    cov[area][1] += int(m.group(3))
    for area, (st, ms) in cov.items():
        if st:
            pct = round(100 * (st - ms) / st, 1)
            rec("P1", "OK" if pct >= 85 else "ALERTE", f"couverture {area} >= 85 %", f"{pct} %")
    code, out = sh([py, "-m", "ruff", "check", "."], repo)
    rec("P1", "OK" if code == 0 else "ECHEC", "ruff check .", out.splitlines()[-1] if out else "")
    code, out = sh([py, "-m", "mypy", "--strict", "vorpal"], repo)
    rec("P1", "OK" if code == 0 else "ECHEC", "mypy --strict vorpal", out.splitlines()[-1] if out else "")
    code, out = sh([py, "-m", "compileall", "-q", "odoo_addons"], repo)
    rec("P2", "OK" if code == 0 else "ECHEC", "addon Odoo : compilation Python", out[:120])
    ex = repo / "docs" / "check_examples.py"
    if ex.exists():
        code, out = sh([py, str(ex)], repo)
        rec("P2", "OK" if code == 0 else "ECHEC", "exemples de la documentation", out.splitlines()[-1] if out else "")


# ---------------------------------------------------------------- E. chaîne réelle Ventes -> DGI -> Compta (objets réels du Core)
E2E = r'''
from __future__ import annotations
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from vorpal.core import (AppConfig, AppContext, AuditLog, AuthorizationService, Currency, EventBus, InMemoryEventStore,
                         ModuleRegistry, ModuleState, Money, Partner, ServiceRegistry, User, create_db_engine)
from vorpal.core.contracts.events import JOURNAL_ENTRY_POSTED
from vorpal.modules.adapters.odoo import AccountingEventHandlers, AccountMapping, OdooAccountingPoster, SqlitePendingQueue
from vorpal.modules.adapters.odoo.module import OdooAccountingModule
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.module import DgiModule
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.tax_groups import TaxGroupTable
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import InvoiceLine
from vorpal.modules.ventes.module import VentesModule
from tests.adapters.odoo.helpers import make_client

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


class Chain:
    def __init__(self) -> None:
        self.bus = EventBus(store=InMemoryEventStore())
        ctx = AppContext(event_bus=self.bus, services=ServiceRegistry(), config=AppConfig(env="test", db_url="sqlite:///:memory:"),
                         db=create_db_engine("sqlite:///:memory:"), audit=AuditLog(), authz=AuthorizationService(), clock=lambda: NOW)
        base = TaxGroupTable.from_json_file()
        table = TaxGroupTable(groups=base.list_active_on(date(2026, 11, 20)), mappings={"A": "TG-01"})
        self.mcf = MockMCFClient(_device_id="MCF-TEST", clock=lambda: NOW)
        self.dgi = DgiModule(self.mcf, table, RetryPolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300))
        self.ventes = VentesModule()
        self.odoo = make_client()
        handlers = AccountingEventHandlers(OdooAccountingPoster(self.odoo), SqlitePendingQueue(), AccountMapping.load(), clock=lambda: NOW)
        self.compta = OdooAccountingModule(OdooAccountingPoster(self.odoo), handlers)
        reg = ModuleRegistry()
        for m in (self.dgi, self.ventes, self.compta):
            reg.register(m)
        reg.start_all(ctx)
        for name in ("dgi", "ventes", "comptabilite_odoo"):
            assert reg.get_state(name) == ModuleState.RUNNING, name
        with self.ventes.uow_factory() as uow:
            uow.customers.save(Customer(partner=Partner(id="CUST-001", name="Client", partner_type="client", nif="NIF-1", rccm="R-1", address="Lubumbashi")))
            uow.commit()
        self.actor = User(id="U1", username="t", direct_permissions=frozenset({"ventes.create", "ventes.confirm"}))

    def submit(self) -> str:
        svc = self.ventes.invoice_service
        d = svc.create_draft(self.actor, customer_id="CUST-001", due_date=date(2026, 12, 20), invoice_date=date(2026, 11, 20),
                             lines=(InvoiceLine(item_code="SRV-01", designation="Prestation", quantity=Decimal("2"),
                                                unit_price_ht=Money(Decimal("100.00"), Currency.USD)),))
        svc.validate_and_submit(self.actor, d.id)
        return d.id

    def moves(self):
        return list(self.odoo.moves.values())


def totals(move):
    lines = move.prepared.entry.lines
    return sum((l.debit for l in lines), Decimal(0)), sum((l.credit for l in lines), Decimal(0))


def test_certified_invoice_is_posted_once_and_balanced():
    c = Chain(); c.submit()
    assert len(c.moves()) == 1 and c.moves()[0].state == "posted"
    debit, credit = totals(c.moves()[0])
    assert debit == credit == Decimal("232.00"), (debit, credit)
    assert c.bus._store.list_events(JOURNAL_ENTRY_POSTED), "journal.entry.posted non publié"


def test_replay_does_not_duplicate_accounting_entry():
    c = Chain(); c.submit(); c.bus.replay()
    assert len(c.moves()) == 1


def test_no_accounting_before_certification_then_exactly_one_after_retry():
    c = Chain(); c.mcf.simulate_outage(True); c.submit()
    assert len(c.moves()) == 0
    c.mcf.simulate_outage(False); c.dgi.process_retries(NOW + timedelta(hours=1))
    assert len(c.moves()) == 1


def test_final_dgi_rejection_never_reaches_accounting():
    c = Chain(); c.mcf.reject_serials.add("FAC-2026-000001"); c.submit()
    assert len(c.moves()) == 0


def test_odoo_down_then_back_posts_without_duplicate():
    c = Chain(); c.odoo.available = False; c.submit()
    assert len(c.moves()) == 0
    c.odoo.available = True; c.compta.process_pending()
    assert len(c.moves()) == 1
'''


def e2e(repo: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "test_chain_ventes_dgi_compta.py"
        t.write_text(E2E, encoding="utf-8")
        code, out = sh([sys.executable, "-m", "pytest", str(t), "-q", "-p", "no:cacheprovider", "--rootdir", str(repo), "-x"], repo, {"PYTHONPATH": f"{repo}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"})
    summ = next((ln for ln in reversed(out.splitlines()) if re.search(r"\b(passed|failed|error)", ln)), out[-200:])
    rec("P0", "OK" if code == 0 else "ECHEC", "chaîne réelle Ventes -> DGI -> Compta (5 scénarios, Odoo simulé)", summ.strip("= "))
    if code != 0:
        print("\n".join(out.splitlines()[-40:]))


# ---------------------------------------------------------------- rapport
def report(path: Path) -> None:
    prios = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}
    bad = [r for r in RESULTS if r[1] in ("ECHEC", "ALERTE")]
    lines = [f"# Rapport de vérification Vorpal ({datetime.now():%Y-%m-%d %H:%M})\n", "| Prio | Statut | Contrôle | Détail |", "|---|---|---|---|"]
    for p, s, n, d in sorted(RESULTS, key=lambda r: (prios[r[0]], r[1] == "OK")):
        lines.append(f"| {p} | {s} | {n} | {d.replace('|', '/')[:300]} |")
    verdict = "TOUT EST OK" if not bad and all(r[1] == "OK" for r in RESULTS) else f"{len(bad)} point(s) à traiter ; {sum(1 for r in RESULTS if r[1] == 'NON EXECUTE')} contrôle(s) non exécuté(s)"
    lines.append(f"\n**Verdict : {verdict}**\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n== VERDICT : {verdict}\nRapport : {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--zip")
    ap.add_argument("--skip-tools", action="store_true")
    ap.add_argument("--show-diff")
    ap.add_argument("--out")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    if not (repo / "vorpal" / "core").is_dir():
        print(f"ARRET : {repo} ne contient pas vorpal/core (lancez depuis la racine du dépôt).")
        return 2
    print(f"Dépôt : {repo}\n== A. comparaison avec le zip")
    if a.zip:
        with tempfile.TemporaryDirectory() as tmp:
            compare_with_zip(repo, find_tree(Path(a.zip).expanduser(), Path(tmp)), a.show_diff)
    else:
        rec("P1", "NON EXECUTE", "comparaison locale/zip", "--zip non fourni")
    print("== B. Core gelé")
    core_frozen(repo)
    print("== C. architecture statique")
    static_checks(repo)
    if a.skip_tools:
        rec("P0", "NON EXECUTE", "pytest/ruff/mypy/chaîne réelle", "--skip-tools")
    else:
        print("== D. outils")
        tools(repo)
        print("== E. chaîne réelle")
        e2e(repo)
    out = Path(a.out) if a.out else repo.parent / f"rapport_verification_{datetime.now():%Y%m%d_%H%M%S}.md"
    report(out)
    return 0 if not any(r[1] == "ECHEC" and r[0] == "P0" for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
