"""Compare le depot local au depot GitHub, verifie les exigences du projet, corrige l'automatisable, pousse, planifie.

Usage (depuis la racine du depot, venv actif) :
    python synchroniser_vorpal.py                 # 1) ecart  2) controles  3) plan  (AUCUNE modification du depot)
    python synchroniser_vorpal.py --pull          # + rattrape le retard sur GitHub (fast-forward uniquement)
    python synchroniser_vorpal.py --fix           # + corrections automatiques sures (annulees si les tests regressent)
    python synchroniser_vorpal.py --fix --push    # + commit(s) et push sur une NOUVELLE branche maintenance/...
Options : --pull (implicite avec --fix et --push), --to-main (pousse HEAD sur main, fast-forward uniquement), --yes (pas de question), --remote-branch=main,
          --any-origin (autorise une origine autre que ERP-PROJECT, pour les tests), --no-fetch.

Garde-fous : jamais de --force ; le Core (vorpal/core, tests/core) n'est jamais modifie et bloque le push s'il differe du
tag core-v1 ; le pyproject.toml n'est jamais modifie ; tout correctif qui fait baisser les tests est annule ;
sauvegarde des fichiers touches dans ../vorpal_sauvegardes/ avant toute correction. Rapport et plan : dossier parent.
Outil a usage unique : ne pas le commiter.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

CORE_PATHS = ("vorpal/core", "tests/core")
JUNK_NAMES = {"patch_repo.py", "patch_lot_a.py", "patch_lot_b.py", "lot_A_core.patch", ".coverage", "organiser_vorpal.py",
              "reunifier_vorpal.py", "audit_reunification.py", "inspect_vorpal_erp.py", "synchroniser_vorpal.py"}
JUNK_PATTERNS = ("*.tgz", "*.bak", "rapport_*.txt", "organisation_log.txt")
ALLOWED_THIRD_PARTY = {"pydantic", "sqlalchemy", "httpx", "pytest", "coverage", "vorpal", "tests"}
CLOCK_CALLS = {"datetime.now", "datetime.utcnow", "date.today", "datetime.datetime.now", "datetime.date.today"}
EXPECTED: dict[str, list[str]] = {
    "ventes": ["manifest.py", "module.py", "settings.py", "domain/invoices.py", "domain/payments.py", "domain/quotes.py",
               "domain/orders.py", "domain/reminders.py", "domain/totals.py", "services/customers.py", "services/pricing.py",
               "services/quotes.py", "services/orders.py", "services/invoicing.py", "services/payments.py",
               "services/reminders.py", "adapters/events.py", "adapters/channels.py", "adapters/persistence/models.py",
               "adapters/persistence/sql_repositories.py", "adapters/persistence/numbering.py", "data/sales_settings.json"],
    "dgi": ["manifest.py", "models.py", "tax_groups.py", "state_machine.py", "clients/base.py", "clients/mock.py",
            "clients/http.py", "service.py", "outbox.py", "retry.py", "credit_note.py", "renderers.py", "journal.py",
            "errors_map.py", "data/tax_groups.json"],
    "adapters/odoo": ["client.py", "poster.py", "handlers.py"],
    "rh_paie": ["manifest.py", "employees.py", "contracts.py", "leaves.py", "attendance.py", "expenses.py",
                "payroll/rules.py", "payroll/engine.py", "payroll/run.py", "payroll/payslip.py", "payroll/loader.py",
                "renderers.py", "declarations.py", "data/payroll_rules_rdc.json"],
    "achats": [],
    "stocks": [],
}
OUT: list[str] = []


def say(text: str = "") -> None:
    print(text)
    OUT.append(text)


def run(cmd: list[str], cwd: Path, timeout: int = 900, env_repo: bool = True) -> tuple[int, str]:
    env = dict(os.environ)
    if env_repo:
        env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


class Sync:
    def __init__(self, repo: Path, flags: set[str], remote_branch: str) -> None:
        self.repo, self.flags, self.rb = repo, flags, remote_branch
        self.remote = f"origin/{remote_branch}"
        self.py = sys.executable
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.parent = repo.parent
        self.touched_notes: list[str] = []

    def git(self, *args: str) -> tuple[int, str]:
        return run(["git", *args], self.repo, 120, env_repo=False)

    # ------------------------------------------------------------ 1. ecart
    def compare(self) -> dict[str, object]:
        say("== 1. ECART LOCAL <-> GITHUB ==")
        origin = self.git("remote", "get-url", "origin")[1]
        say(f"origine : {origin}")
        if "ERP-PROJECT" not in origin and "--any-origin" not in self.flags:
            say("ARRET : l'origine n'est pas ERP-PROJECT (--any-origin pour passer outre).")
            return {"stop": True}
        if "--no-fetch" not in self.flags:
            code, out = self.git("fetch", "origin", "--tags", "-q")
            say("fetch : " + ("OK" if code == 0 else f"ECHEC ({out[:120]}) : comparaison avec les references deja connues"))
        branch = self.git("branch", "--show-current")[1] or "(detachee)"
        counts = self.git("rev-list", "--left-right", "--count", f"HEAD...{self.remote}")[1].split()
        ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
        status = [ln for ln in self.git("status", "--porcelain")[1].splitlines() if ln.strip()]
        say(f"branche : {branch} | en avance : {ahead} commit(s) | en retard : {behind} commit(s) | fichiers modifies/non suivis : {len(status)}")
        diff = self.git("diff", "--name-status", self.remote)[1].splitlines()
        untracked = [ln[3:] for ln in status if ln.startswith("??")]
        for ln in diff[:40]:
            say(f"   {ln}")
        for name in untracked[:20]:
            say(f"   ?? {name}")
        if diff:
            say("   (D = sur GitHub mais absent en local ; M = different ; A = ajoute en local ; ?? = non suivi)")
        if len(diff) > 40:
            say(f"   ... {len(diff) - 40} autre(s)")
        core = self.git("diff", "--stat", "core-v1", "--", *CORE_PATHS)[1]
        say("Core vs tag core-v1 : " + ("IDENTIQUE" if not core else "DIFFERENT\n" + core))
        junk = [n for n in untracked if Path(n.rstrip("/")).name in JUNK_NAMES
                or any(Path(n).match(p) for p in JUNK_PATTERNS)]
        if junk:
            say(f"fichiers parasites non suivis (ne seront jamais commites) : {junk}")
        identical = ahead == 0 and behind == 0 and not status
        say("=> " + ("AUCUN ECART." if identical else "ECART DETECTE."))
        return {"ahead": ahead, "behind": behind, "dirty": status, "core_changed": bool(core), "identical": identical,
                "junk": junk, "branch": branch, "stop": False}

    def sync_down(self, info: dict[str, object]) -> bool:
        behind = int(str(info["behind"]))
        if not behind:
            return True
        incoming = set(self.git("diff", "--name-only", f"HEAD...{self.remote}")[1].splitlines())
        dirty = {ln[3:].strip().strip('"') for ln in info["dirty"]}  # type: ignore[attr-defined]
        if incoming & dirty:
            say(f"ARRET : GitHub a modifie des fichiers que vous avez aussi modifies : {sorted(incoming & dirty)}")
            return False
        if int(str(info["ahead"])):
            say("ARRET : historique divergent (commits locaux ET distants). Resolvez par un rebase ou un merge manuel.")
            return False
        say(f"Mise a jour fast-forward depuis {self.remote} ({behind} commit(s))")
        stash = bool(dirty) and self.git("stash", "push", "-u", "-m", "sync-auto")[0] == 0
        code, out = self.git("merge", "--ff-only", self.remote)
        if stash:
            self.git("stash", "pop")
        say("   " + ("OK" if code == 0 else out[:200]))
        return code == 0

    # ------------------------------------------------------------ 2. exigences
    def pytest(self) -> dict[str, object]:
        tmp = Path(tempfile.mkdtemp())
        code, out = run([self.py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=vorpal",
                         f"--cov-report=json:{tmp / 'cov.json'}"], self.repo)
        passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
        failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
        errors = int(m.group(1)) if (m := re.search(r"(\d+) errors?", out)) else 0
        cover: dict[str, list[int]] = {}
        total = 0.0
        cov_file = tmp / "cov.json"
        if cov_file.is_file():
            data = json.loads(cov_file.read_text(encoding="utf-8"))
            total = float(data["totals"]["percent_covered"])
            for name, info in data["files"].items():
                parts = Path(name).parts
                idx = parts.index("modules") if "modules" in parts else -1
                key = "core" if "core" in parts[:3] else (parts[idx + 1] if idx >= 0 and len(parts) > idx + 2 else "autre")
                bucket = cover.setdefault(key, [0, 0])
                bucket[0] += info["summary"]["covered_lines"]
                bucket[1] += info["summary"]["num_statements"]
        shutil.rmtree(tmp, ignore_errors=True)
        return {"passed": passed, "failed": failed, "errors": errors, "total": total, "cover": cover, "rc": code,
                "tail": out.splitlines()[-1] if out else ""}

    def ruff(self) -> list[dict[str, str]]:
        skip = [x for n in sorted(JUNK_NAMES) if (self.repo / n).is_file() for x in ("--extend-exclude", n)]
        _, out = run([self.py, "-m", "ruff", "check", ".", "--output-format=json", *skip], self.repo)
        try:
            return [{"file": str(Path(i["filename"]).relative_to(self.repo)), "code": i["code"]} for i in json.loads(out)]
        except (ValueError, KeyError):
            return [{"file": "?", "code": "ruff-illisible"}]

    def static_rules(self) -> list[str]:
        alerts: list[str] = []
        for path in sorted((self.repo / "vorpal").rglob("*.py")):
            rel = path.relative_to(self.repo)
            parts = rel.parts
            mod = parts[parts.index("modules") + 1] if "modules" in parts and parts.index("modules") + 1 < len(parts) else None
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                alerts.append(f"SYNTAXE {rel}:{exc.lineno}")
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    top = name.split(".")[0]
                    if top not in sys.stdlib_module_names and top != "__future__" and top not in ALLOWED_THIRD_PARTY:
                        alerts.append(f"DEPENDANCE HORS STACK {rel}:{node.lineno} {name}")
                    seg = name.split(".")
                    if mod and seg[:2] == ["vorpal", "modules"] and len(seg) > 2 and seg[2] != mod:
                        alerts.append(f"IMPORT ENTRE MODULES {rel}:{node.lineno} {name}")
                    if mod and "domain" in parts and (top == "sqlalchemy" or ".services" in name or ".adapters" in name
                                                      or name.startswith("vorpal.core.events")):
                        alerts.append(f"DOMAINE IMPUR {rel}:{node.lineno} {name}")
                if isinstance(node, ast.Call):
                    call = ast.unparse(node.func)
                    if call in {"eval", "exec"}:
                        alerts.append(f"EVAL/EXEC {rel}:{node.lineno}")
                    if mod and call in CLOCK_CALLS:
                        alerts.append(f"HORLOGE DIRECTE {rel}:{node.lineno}")
                    if mod and call.split(".")[-1] == "Float":
                        alerts.append(f"COLONNE SQL Float (montant ?) {rel}:{node.lineno}")
                if isinstance(node, ast.Global):
                    alerts.append(f"GLOBAL {rel}:{node.lineno}")
        return alerts

    def structure(self) -> list[str]:
        problems = [f"manquant : {p}" for p in ("pyproject.toml", "vorpal/__init__.py", "vorpal/core", "vorpal/modules/__init__.py",
                                                 "tests", "docs", "HANDOFF.md", ".gitignore") if not (self.repo / p).exists()]
        problems += [f"__init__.py manquant : {d}" for d in self.missing_inits()]
        problems += [f"depot imbrique : {p.parent.relative_to(self.repo)}" for p in self.repo.rglob(".git")
                     if p.parent != self.repo and ".venv" not in p.parts]
        handoff = self.repo / "HANDOFF.md"
        if handoff.is_file() and any(ln.startswith("- ## ") for ln in handoff.read_text(encoding="utf-8").splitlines()):
            problems.append("HANDOFF.md : titres mal formes ('- ## ')")
        tracked = self.git("ls-files")[1].splitlines()
        problems += [f"fichier parasite suivi par Git : {t}" for t in tracked
                     if Path(t).name in JUNK_NAMES or any(Path(t).match(p) for p in JUNK_PATTERNS)]
        if list(self.repo.glob("vorpal/modules/*/data/*.json")) and "package-data" not in (self.repo / "pyproject.toml").read_text(encoding="utf-8"):
            problems.append("JSON de modules sans package-data dans pyproject.toml")
        return problems

    def missing_inits(self) -> list[str]:
        missing: list[str] = []
        for root in (self.repo / "vorpal", self.repo / "tests"):
            for folder in [root, *sorted(p for p in root.rglob("*") if p.is_dir())] if root.is_dir() else []:
                if "__pycache__" in folder.parts or (folder / "__init__.py").exists():
                    continue
                if any(f.suffix == ".py" for f in folder.iterdir() if f.is_file()):
                    missing.append(str(folder.relative_to(self.repo)))
        return missing

    def evaluate(self, label: str) -> dict[str, object]:
        say(f"== EXIGENCES DU PROJET ({label}) ==")
        res = self.pytest()
        lint = self.ruff()
        mypy_code, mypy_out = run([self.py, "-m", "mypy", "--strict", "vorpal"], self.repo)
        ex = self.repo / "docs" / "check_examples.py"
        ex_code = run([self.py, str(ex)], self.repo)[0] if ex.is_file() else -1
        static, struct = self.static_rules(), self.structure()
        cover = {k: round(100 * v[0] / v[1], 1) if v[1] else 100.0 for k, v in res["cover"].items()}  # type: ignore[attr-defined]
        weak = {k: v for k, v in cover.items() if k != "autre" and v < 85}
        rows = [
            (res["failed"] == 0 and res["errors"] == 0 and res["passed"] > 0, f"pytest : {res['passed']} reussis, {res['failed']} echecs, {res['errors']} erreurs"),
            (not weak, f"couverture : total {float(res['total']):.1f} % ; par composant {cover} (seuil 85 %)"),
            (not lint, f"ruff check . : {len(lint)} erreur(s)"),
            (mypy_code == 0, f"mypy --strict vorpal : {mypy_out.splitlines()[-1] if mypy_out else '?'}"),
            (ex_code in (0, -1), "exemples de documentation : " + ("OK" if ex_code == 0 else "absent" if ex_code == -1 else "ECHEC")),
            (not static, f"regles d'architecture : {len(static)} alerte(s)"),
            (not struct, f"structure du depot : {len(struct)} probleme(s)"),
        ]
        for ok, text in rows:
            say(f"[{'OK' if ok else 'A CORRIGER'}] {text}")
        for item in [f"ruff {i['code']} {i['file']}" for i in lint[:10]] + static[:15] + struct[:15]:
            say(f"      - {item}")
        return {"res": res, "lint": lint, "mypy": mypy_code == 0, "static": static, "struct": struct,
                "green": all(ok for ok, _ in rows), "tests_ok": rows[0][0]}

    # ------------------------------------------------------------ 3. corrections sures
    def backup(self, files: list[Path]) -> None:
        target = self.parent / "vorpal_sauvegardes"
        target.mkdir(exist_ok=True)
        archive = target / f"avant_correctifs_{self.stamp}.tgz"
        with tarfile.open(archive, "w:gz") as tar:
            for f in files:
                if f.is_file():
                    tar.add(f, arcname=str(f.relative_to(self.repo)))
        say(f"sauvegarde des fichiers concernes : {archive}")

    def guarded(self, label: str, files: list[Path], action: object, before_passed: int) -> bool:
        snap = {f: f.read_bytes() for f in files if f.is_file()}
        created_before = set(files)
        action()  # type: ignore[operator]
        after = self.pytest()
        ok = int(str(after["passed"])) >= before_passed and after["failed"] == 0 and after["errors"] == 0
        if not ok:
            for f, data in snap.items():
                f.write_bytes(data)
            for f in created_before - set(snap):
                f.unlink(missing_ok=True)
            say(f"   [ANNULE] {label} : les tests regressent ({after['passed']} reussis, {after['failed']} echecs)")
        else:
            say(f"   [APPLIQUE] {label}")
        return ok

    def improve(self, state: dict[str, object]) -> None:
        say("== 3. CORRECTIONS AUTOMATIQUES SURES (hors Core, sans toucher a pyproject.toml) ==")
        if not state["tests_ok"]:
            say("ARRET : les tests echouent deja avant correction ; rien n'est modifie automatiquement.")
            return
        before = int(str(state["res"]["passed"]))  # type: ignore[index]
        lint = [i for i in state["lint"] if not i["file"].startswith(CORE_PATHS) and i["file"] != "?"]  # type: ignore[attr-defined]
        files = sorted({self.repo / i["file"] for i in lint})
        self.backup(files + [self.repo / d / "__init__.py" for d in self.missing_inits()])
        inits = [self.repo / d / "__init__.py" for d in self.missing_inits() if not d.startswith(CORE_PATHS)]
        if inits:
            self.guarded(f"creation de {len(inits)} fichier(s) __init__.py",
                         inits, lambda: [p.write_text("", encoding="utf-8") for p in inits], before)
        if files:
            self.guarded(f"ruff --fix (corrections sures) sur {len(files)} fichier(s)", files,
                         lambda: run([self.py, "-m", "ruff", "check", "--fix", *map(str, files)], self.repo), before)
            remaining = [i for i in self.ruff() if not i["file"].startswith(CORE_PATHS) and i["code"] in {"E701", "E702", "E703"}]
            fmt = sorted({self.repo / i["file"] for i in remaining})
            if fmt:
                self.guarded(f"ruff format sur {len(fmt)} fichier(s) (E701/E702 : une instruction par ligne)", fmt,
                             lambda: run([self.py, "-m", "ruff", "format", *map(str, fmt)], self.repo), before)
        unused = sorted({self.repo / i["file"] for i in self.ruff() if i["code"] == "F841" and i["file"].startswith("tests/")})
        if unused:
            self.guarded(f"suppression des variables inutilisees (F841, tests uniquement) dans {len(unused)} fichier(s)", unused,
                         lambda: run([self.py, "-m", "ruff", "check", "--fix", "--unsafe-fixes", "--select", "F841",
                                      *map(str, unused)], self.repo), before)
            files = files or unused
        if not (inits or files):
            say("rien d'automatisable : les ecarts restants demandent du travail de developpement (voir le plan).")

    # ------------------------------------------------------------ 4. commit et push
    def push(self, info: dict[str, object], final: dict[str, object]) -> None:
        say("== 4. COMMIT ET PUSH ==")
        if "--push" not in self.flags:
            say("(non demande : ajoutez --push pour commiter et pousser)")
            return
        blockers = []
        if info["core_changed"]:
            blockers.append("le Core differe du tag core-v1")
        if not final["green"]:
            blockers.append("les exigences ne sont pas toutes respectees (voir plus haut)")
        if blockers:
            say("PUSH REFUSE : " + " ; ".join(blockers))
            return
        branch = f"maintenance/assainissement-{self.stamp[:8]}-{self.stamp[9:13]}"
        current = str(info["branch"])
        target = "main" if "--to-main" in self.flags else branch
        if "--yes" not in self.flags and input(f"Commiter et pousser vers origin/{target} ? Tapez OUI : ").strip() != "OUI":
            say("Annule par l'utilisateur.")
            return
        if target != "main" and self.git("checkout", "-b", branch)[0] != 0:
            say("ECHEC : creation de la branche")
            return
        self.git("add", "-A")
        for name in self.git("diff", "--cached", "--name-only")[1].splitlines():
            if Path(name).name in JUNK_NAMES or any(Path(name).match(p) for p in JUNK_PATTERNS):
                self.git("reset", "-q", "--", name)
        staged = self.git("diff", "--cached", "--name-only")[1].splitlines()
        if staged:
            code, out = self.git("commit", "-m", "chore: synchronisation locale et assainissement automatique\n\n"
                                 f"{len(staged)} fichier(s) ; exigences verifiees par synchroniser_vorpal.py")
            say(f"commit : {'OK' if code == 0 else out[:200]} ({len(staged)} fichier(s))")
        elif int(str(info["ahead"])) == 0:
            say("rien a commiter ni a pousser.")
            return
        cmd = ["push", "origin", "HEAD:main"] if target == "main" else ["push", "-u", "origin", branch]
        code, out = self.git(*cmd)
        say(f"push {'-> origin/' + target}: {'OK' if code == 0 else 'ECHEC : ' + out[:300]}")
        if code == 0 and target != "main":
            url = self.git("remote", "get-url", "origin")[1].removesuffix(".git")
            say(f"Pour fusionner : {url}/pull/new/{branch}  (branche d'origine : {current})")

    # ------------------------------------------------------------ 5. plan
    def plan(self, final: dict[str, object]) -> Path:
        lines = [f"# PLAN DE TRAVAIL RESTANT (genere le {datetime.now():%Y-%m-%d %H:%M} depuis l'etat reel du depot)", ""]
        res = final["res"]
        etat = (
            f"Etat : {res['passed']} tests reussis, {res['failed']} echecs ; ruff {len(final['lint'])} erreur(s) ; "  # type: ignore[index,arg-type]
            f"mypy {'OK' if final['mypy'] else 'ERREURS'} ; alertes d'architecture {len(final['static'])} ; "  # type: ignore[arg-type]
            f"problemes de structure {len(final['struct'])}."  # type: ignore[arg-type]
        )
        lines += [etat, ""]
        lines += ["## P0. Assainissement restant", ""]
        todo0 = [f"- Ruff : {i['code']} dans `{i['file']}`" for i in final["lint"][:15]]  # type: ignore[index]
        todo0 += [f"- {x}" for x in (final["static"] + final["struct"])[:15]]  # type: ignore[operator]
        if not final["mypy"]:
            todo0.append("- mypy --strict vorpal : erreurs a corriger")
        lines += todo0 or ["- aucun"]
        lines += ["", "## P1. Core v1.1 (sur autorisation, tag core-v1.1)", ""]
        core_items = {"vorpal/core/contracts/event_payloads.py": "schemas de payload des evenements",
                      "vorpal/core/bootstrap.py": "fonction d'amorcage du Core"}
        for path, label in core_items.items():
            lines.append(f"- [{'x' if (self.repo / path).exists() else ' '}] `{path}` : {label}")
        persisted = any("class SqlAlchemy" in p.read_text(encoding="utf-8") for p in (self.repo / "vorpal/core").glob("*.py") if "Audit" in p.read_text(encoding="utf-8"))
        lines.append(f"- [{'x' if persisted else ' '}] persistance SQL de AuditLog / EventStore (spec 01)")
        lines += ["", "## Modules dans l'ordre decide : Ventes, Achats, DGI, puis Compta Odoo, Stocks, Paie", ""]
        for mod, files in EXPECTED.items():
            base = self.repo / ("vorpal/modules/" + mod)
            if not files:
                lines.append(f"### {mod} : specification a rediger (aucun code)")
                lines.append(f"- [{'x' if base.is_dir() else ' '}] code present sous `vorpal/modules/{mod}/`")
                continue
            present = [f for f in files if (base / f).exists()]
            status = "TERMINE" if len(present) == len(files) else "PARTIEL" if present else "ABSENT"
            lines.append(f"### {mod} : {status} ({len(present)}/{len(files)} fichiers attendus par la spec)")
            lines += [f"- [ ] `vorpal/modules/{mod}/{f}`" for f in files if f not in present]
            tests_dir = self.repo / "tests" / mod.split("/")[-1]
            lines.append(f"- tests : {'presents' if tests_dir.is_dir() else 'ABSENTS'} (`tests/{mod.split('/')[-1]}/`)")
        addon = self.repo / "odoo_addons" / "vorpal_compta_ohada" / "__manifest__.py"
        lines += [f"- [{'x' if addon.exists() else ' '}] addon Odoo `odoo_addons/vorpal_compta_ohada/__manifest__.py`", ""]
        decisions = (
            "- D1 systeme de reference (Vorpal ou Odoo) ; D2 interface et API ; D3 evolution du Core ; "
            "D4 Achats sans Stocks ; D5 piste administrative DGI en parallele ; D6 CI, README, licence."
        )
        criteres = (
            "Tests verts, couverture >= 85 %, `ruff check .` et `mypy --strict vorpal` propres, aucun import entre modules, "
            "`HANDOFF.md` a jour avec resultats reels, points A_VALIDER listes."
        )
        lines += ["## Decisions en attente", "", decisions, "", "## Rappel des criteres de fin d'un module", "", criteres]
        path = self.parent / f"vorpal_plan_{self.stamp}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    start = Path(args[0]) if args else Path.cwd()
    code, top = run(["git", "rev-parse", "--show-toplevel"], start.resolve(), 30, env_repo=False)
    if code != 0:
        print("ERREUR : pas un depot Git. Lancez le script depuis la racine du depot.")
        return 1
    rb = next((f.split("=", 1)[1] for f in flags if f.startswith("--remote-branch=")), "main")
    job = Sync(Path(top), flags, rb)
    info = job.compare()
    if info.get("stop"):
        return 1
    final: dict[str, object] | None = None
    if info["identical"]:
        say("Rien a synchroniser : depot local identique a GitHub. (--fix ou --push n'ont pas d'objet.)")
    else:
        if flags & {"--pull", "--fix", "--push"}:
            if not job.sync_down(info):
                return 1
        elif int(str(info["behind"])):
            say("(le retard sur GitHub n'est PAS rattrape en lecture seule : ajoutez --pull, --fix ou --push)")
        state = job.evaluate("avant correction")
        final = state
        if "--fix" in flags and not state["green"]:
            job.improve(state)
            final = job.evaluate("apres correction")
        elif not state["green"]:
            say("(ajoutez --fix pour tenter les corrections automatiques sures)")
        job.push(info, final)
    if final is None:
        final = job.evaluate("etat actuel")
    plan = job.plan(final)
    say(f"\nPlan de travail restant : {plan}")
    report = job.parent / f"rapport_ecart_{job.stamp}.txt"
    report.write_text("\n".join(OUT), encoding="utf-8")
    print(f"Rapport : {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
