#!/usr/bin/env python3
"""
Vérification de bout en bout de l'architecture Vorpal (Core + Ventes + DGI + Achats + Compta).

Usage (depuis la racine du dépôt, venv actif) :
    python verifier_architecture.py                 # tout
    python verifier_architecture.py --verbose       # traces complètes en cas d'erreur
    python verifier_architecture.py --only flux     # ne lance que les contrôles dont le nom contient "flux"
    python verifier_architecture.py --json rapport.json

Ne modifie AUCUN fichier du dépôt : bases SQLite en mémoire (ou fichier temporaire supprimé),
FakeOdooClient (pas d'Odoo réel), MockMCFClient (pas de DGI réelle : ce n'est PAS une homologation).

Statuts : OK / ECHEC (assertion fausse) / ERREUR (exception inattendue) /
          AVERT. (écart connu ou à examiner, non bloquant) / IGNORÉ (prérequis absent).
Code de sortie : 0 si aucun ECHEC/ERREUR, 1 sinon.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)
NUMBER_1 = "FAC-2026-000001"
NUMBER_2 = "FAC-2026-000002"
READY = "invoice.ready_for_certification"
CERTIFIED = "invoice.certified"
POSTED = "journal.entry.posted"
PO_CONFIRMED = "achats.purchase_order.confirmed"

# Plan de comptes simulé : mêmes codes que tests/adapters/odoo/helpers.py.
ODOO_ACCOUNTS = {"411100": 1, "701100": 2, "706100": 3, "443100": 4, "661100": 5,
                 "664100": 6, "422000": 7, "431300": 8, "447200": 9}
ODOO_JOURNALS = {"VTE": 10, "PAIE": 11, "CAU": 12, "CAF": 13, "BQU": 14, "BQF": 15}
ODOO_JOURNAL_ACCOUNTS = {"CAU": 21, "CAF": 22, "BQU": 23, "BQF": 24}


class Warn(Exception):
    """Écart connu ou à examiner : non bloquant."""


class Skip(Exception):
    """Prérequis absent."""


@dataclass
class Result:
    section: str
    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Check:
    section: str
    name: str
    fn: Callable[[], None]


CHECKS: list[Check] = []


def check(section: str, name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def deco(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append(Check(section, name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------- #
# Chargement paresseux de vorpal (les contrôles statiques marchent sans dépendances)
# --------------------------------------------------------------------------- #

class V:  # espace de noms rempli par load_runtime()
    loaded = False
    error = ""


def load_runtime() -> None:
    if V.loaded or V.error:
        return
    try:
        import vorpal.core as core
        from vorpal.core.contracts.events import EVENT_HANDLER_FAILED
        from vorpal.core.errors import ConflictError, NotFoundError
        from vorpal.modules.achats.manifest import ACHATS_EVENTS_EMITTED
        from vorpal.modules.achats.module import AchatsModule
        from vorpal.modules.adapters.odoo import (
            AccountingEventHandlers,
            AccountMapping,
            FakeOdooClient,
            OdooAccountingPoster,
            SqliteFiscalInbox,
            SqlitePendingQueue,
        )
        from vorpal.modules.adapters.odoo.module import OdooAccountingModule
        from vorpal.modules.dgi.clients.mock import MockMCFClient
        from vorpal.modules.dgi.manifest import DGI_EVENTS_EMITTED
        from vorpal.modules.dgi.module import DgiModule
        from vorpal.modules.dgi.retry import RetryPolicy
        from vorpal.modules.dgi.tax_groups import TaxGroupTable
        from vorpal.modules.ventes.domain.customers import Customer
        from vorpal.modules.ventes.domain.invoices import InvoiceLine, InvoiceStatus
        from vorpal.modules.ventes.manifest import VENTES_EVENTS_EMITTED
        from vorpal.modules.ventes.module import VentesModule
    except Exception as exc:  # noqa: BLE001
        V.error = f"{type(exc).__name__}: {exc}"
        return
    V.core = core
    V.EVENT_HANDLER_FAILED = EVENT_HANDLER_FAILED
    V.ConflictError, V.NotFoundError = ConflictError, NotFoundError
    V.ACHATS_EMITTED, V.DGI_EMITTED, V.VENTES_EMITTED = (
        ACHATS_EVENTS_EMITTED, DGI_EVENTS_EMITTED, VENTES_EVENTS_EMITTED)
    V.AchatsModule, V.DgiModule, V.VentesModule = AchatsModule, DgiModule, VentesModule
    V.OdooAccountingModule = OdooAccountingModule
    V.AccountingEventHandlers, V.AccountMapping = AccountingEventHandlers, AccountMapping
    V.FakeOdooClient, V.OdooAccountingPoster = FakeOdooClient, OdooAccountingPoster
    V.SqliteFiscalInbox, V.SqlitePendingQueue = SqliteFiscalInbox, SqlitePendingQueue
    V.MockMCFClient, V.RetryPolicy, V.TaxGroupTable = MockMCFClient, RetryPolicy, TaxGroupTable
    V.Customer, V.InvoiceLine, V.InvoiceStatus = Customer, InvoiceLine, InvoiceStatus
    V.loaded = True


def need_runtime() -> None:
    load_runtime()
    if not V.loaded:
        raise Skip(f"imports Vorpal impossibles ({V.error}). Active le venv : pip install -e \".[dev]\"")


# --------------------------------------------------------------------------- #
# Environnement complet : Core + 4 modules câblés sur l'EventBus réel
# --------------------------------------------------------------------------- #

@dataclass
class Env:
    ctx: Any
    bus: Any
    store: Any
    registry: Any
    ventes: Any
    dgi: Any
    achats: Any
    compta: Any
    client: Any        # MockMCFClient (DGI simulée)
    odoo: Any          # FakeOdooClient
    handlers: Any
    queue: Any
    box: dict[str, datetime]
    actor: Any
    engine: Any
    seeded: bool = field(default=True)

    # -- Ventes
    def reload(self, invoice_id: str) -> Any:
        with self.ventes.uow_factory() as uow:
            found = uow.invoices.get(invoice_id)
        assert found is not None, f"facture {invoice_id} introuvable après rechargement"
        return found

    def submit(self, actor: Any = None) -> str:
        svc = self.ventes.invoice_service
        assert svc is not None, "invoice_service non initialisé (module Ventes non démarré ?)"
        draft = svc.create_draft(
            actor or self.actor, customer_id="CUST-001", due_date=date(2026, 12, 20),
            invoice_date=date(2026, 11, 20),
            lines=(V.InvoiceLine(
                item_code="SRV-01", designation="Prestation", quantity=Decimal("2"),
                unit_price_ht=V.core.Money(Decimal("100.00"), V.core.Currency.USD)),),
        )
        svc.validate_and_submit(actor or self.actor, draft.id)
        return str(draft.id)

    # -- observation
    def types(self) -> list[str]:
        return [e.type for e in self.store.list_events()]

    def moves(self) -> list[Any]:
        return list(self.odoo.moves.values())

    def failures(self) -> list[str]:
        return [f"{e.payload.get('failed_event_type')} → {e.payload.get('handler_name')}: "
                f"{e.payload.get('error')}"
                for e in self.store.list_events(V.EVENT_HANDLER_FAILED)]

    def close(self) -> None:
        for obj in (self.handlers, self.queue):
            for attr in ("_inbox", None):
                target = getattr(obj, attr, None) if attr else obj
                db = getattr(target, "_db", None)
                if db is not None:
                    try:
                        db.close()
                    except Exception:  # noqa: BLE001
                        pass
        try:
            self.engine.dispose()
        except Exception:  # noqa: BLE001
            pass


def make_ctx(db_url: str = "sqlite:///:memory:", clock: Callable[[], datetime] = lambda: NOW) -> tuple[Any, Any, Any]:
    core = V.core
    engine = core.create_db_engine(db_url)
    store = core.InMemoryEventStore()
    bus = core.EventBus(store=store)
    ctx = core.AppContext(
        event_bus=bus, services=core.ServiceRegistry(),
        config=core.AppConfig(env="test", db_url=db_url), db=engine,
        audit=core.AuditLog(), authz=core.AuthorizationService(), clock=clock)
    return ctx, engine, store


def build_env(db_url: str = "sqlite:///:memory:", seed_customer: bool = True) -> Env:
    need_runtime()
    core = V.core
    box = {"now": NOW}
    ctx, engine, store = make_ctx(db_url)

    base = V.TaxGroupTable.from_json_file()
    table = V.TaxGroupTable(groups=base.list_active_on(date(2026, 11, 20)), mappings={"A": "TG-01"})
    client = V.MockMCFClient(_device_id="MCF-TEST", clock=lambda: NOW)
    dgi = V.DgiModule(client, table, V.RetryPolicy(max_attempts=5, base_delay_seconds=10, max_delay_seconds=300))
    ventes = V.VentesModule()
    achats = V.AchatsModule()

    odoo = V.FakeOdooClient(accounts=dict(ODOO_ACCOUNTS))
    odoo.journals.update(ODOO_JOURNALS)
    odoo.journal_accounts.update(ODOO_JOURNAL_ACCOUNTS)
    queue = V.SqlitePendingQueue()
    handlers = V.AccountingEventHandlers(
        V.OdooAccountingPoster(odoo), queue, V.AccountMapping.load(),
        inbox=V.SqliteFiscalInbox(), clock=lambda: box["now"])
    compta = V.OdooAccountingModule(V.OdooAccountingPoster(odoo), handlers)

    registry = core.ModuleRegistry()
    for mod in (compta, achats, ventes, dgi):  # ordre volontairement « mélangé »
        registry.register(mod)
    registry.start_all(ctx)
    for name in ("dgi", "ventes", "achats", "comptabilite_odoo"):
        state = registry.get_state(name)
        assert state == core.ModuleState.RUNNING, f"module {name} à l'état {state.value} après démarrage"

    if seed_customer:
        with ventes.uow_factory() as uow:
            uow.customers.save(V.Customer(partner=core.Partner(
                id="CUST-001", name="Client Test", partner_type="client",
                nif="NIF-123", rccm="RCCM-123", address="Lubumbashi")))
            uow.commit()
    actor = core.User(id="U1", username="tester",
                      direct_permissions=frozenset({"ventes.create", "ventes.confirm"}))
    return Env(ctx, ctx.event_bus, store, registry, ventes, dgi, achats, compta, client, odoo,
               handlers, queue, box, actor, engine, seed_customer)


@contextmanager
def env_ctx(**kw: Any) -> Iterator[Env]:
    env = build_env(**kw)
    try:
        yield env
    finally:
        env.close()


def no_failures(env: Env) -> None:
    fails = env.failures()
    assert not fails, "handler(s) en échec publiés sur le bus :\n    " + "\n    ".join(fails)


# =========================================================================== #
# A. ARCHITECTURE STATIQUE (aucune dépendance Vorpal requise)
# =========================================================================== #

def _py_files() -> Iterator[Path]:
    base = ROOT / "vorpal"
    if not base.is_dir():
        raise Skip("dossier ./vorpal introuvable : lance le script depuis la racine du dépôt")
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p


def _absolute_imports(path: Path) -> Iterator[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, node.lineno


@check("A. Architecture", "Aucun import direct entre modules métier")
def _() -> None:
    bad = []
    for p in _py_files():
        parts = p.relative_to(ROOT).parts
        if parts[:2] != ("vorpal", "modules") or len(parts) < 4:
            continue
        own = parts[2]
        for mod, line in _absolute_imports(p):
            seg = mod.split(".")
            if seg[:2] == ["vorpal", "modules"] and len(seg) > 2 and seg[2] != own:
                bad.append(f"{p.relative_to(ROOT)}:{line} importe {mod}")
    assert not bad, "imports croisés :\n    " + "\n    ".join(bad[:15])


@check("A. Architecture", "Le Core n'importe aucun module métier")
def _() -> None:
    bad = [f"{p.relative_to(ROOT)}:{ln} → {m}" for p in _py_files()
           if p.relative_to(ROOT).parts[:2] == ("vorpal", "core")
           for m, ln in _absolute_imports(p) if m.startswith("vorpal.modules")]
    assert not bad, "dépendance Core → modules :\n    " + "\n    ".join(bad)


@check("A. Architecture", "Aucune colonne SQLAlchemy Float/REAL pour les montants")
def _() -> None:
    bad = []
    for p in _py_files():
        if p.relative_to(ROOT).parts[:2] != ("vorpal", "modules"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if name in {"Float", "FLOAT", "REAL"}:
                bad.append(f"{p.relative_to(ROOT)}:{node.lineno} ({name})")
    assert not bad, "types flottants trouvés :\n    " + "\n    ".join(sorted(set(bad))[:15])


@check("A. Architecture", "Usage de float() dans le code des modules")
def _() -> None:
    found = []
    for p in _py_files():
        if p.relative_to(ROOT).parts[:2] != ("vorpal", "modules"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                found.append(f"{p.relative_to(ROOT)}:{node.lineno}")
    if found:
        raise Warn(f"{len(found)} appel(s) à float() à examiner (autorisé hors calcul monétaire) : "
                   + ", ".join(found[:8]))


# =========================================================================== #
# B. SOCLE (Core) : registre de modules, bus d'événements
# =========================================================================== #

def _dummy(name: str, depends: list[str] | None = None, fail: bool = False) -> Any:
    core = V.core

    class Dummy(core.BaseModule):
        manifest = core.ModuleManifest(name=name, version="0", depends=depends or [])
        started = False

        def install(self, ctx: Any) -> None:
            return None

        def start(self, ctx: Any) -> None:
            if fail:
                raise RuntimeError("échec simulé")
            self.started = True

        def stop(self) -> None:
            self.started = False

        def healthcheck(self) -> bool:
            return self.started
    return Dummy()


@check("B. Socle", "Registre : dépendance manquante → NotFoundError")
def _() -> None:
    need_runtime()
    reg = V.core.ModuleRegistry()
    reg.register(_dummy("a", ["inexistant"]))
    try:
        reg.resolve_order()
    except V.NotFoundError:
        return
    raise AssertionError("aucune erreur levée pour une dépendance manquante")


@check("B. Socle", "Registre : cycle de dépendances → ConflictError")
def _() -> None:
    need_runtime()
    reg = V.core.ModuleRegistry()
    reg.register(_dummy("a", ["b"]))
    reg.register(_dummy("b", ["a"]))
    try:
        reg.resolve_order()
    except V.ConflictError:
        return
    raise AssertionError("cycle non détecté")


@check("B. Socle", "Registre : l'échec d'un module n'arrête pas les modules indépendants")
def _() -> None:
    need_runtime()
    ctx, engine, _store = make_ctx()
    try:
        reg = V.core.ModuleRegistry()
        reg.register(_dummy("casse", fail=True))
        reg.register(_dummy("dependant", ["casse"]))
        reg.register(_dummy("sain"))
        reg.start_all(ctx)
        S = V.core.ModuleState
        assert reg.get_state("casse") == S.FAILED
        assert reg.get_state("dependant") == S.FAILED, "un dépendant d'un module FAILED doit être FAILED"
        assert reg.get_state("sain") == S.RUNNING, "un module indépendant doit rester RUNNING"
    finally:
        engine.dispose()


@check("B. Socle", "EventBus : un événement republié n'est traité qu'une fois (idempotence)")
def _() -> None:
    need_runtime()
    bus = V.core.EventBus(store=V.core.InMemoryEventStore())
    seen: list[str] = []
    bus.subscribe("x.test", lambda e: seen.append(e.id))
    ev = V.core.Event(type="x.test", payload={})
    bus.publish(ev)
    bus.publish(ev)
    assert seen == [ev.id], f"handler appelé {len(seen)} fois"


@check("B. Socle", "EventBus : handler en échec → event.handler_failed, puis nouvelle tentative au replay")
def _() -> None:
    need_runtime()
    store = V.core.InMemoryEventStore()
    bus = V.core.EventBus(store=store)
    attempts = {"n": 0}

    def flaky(event: Any) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("panne simulée")

    bus.subscribe("x.flaky", flaky)
    bus.publish(V.core.Event(type="x.flaky", payload={}))
    assert len(store.list_events(V.EVENT_HANDLER_FAILED)) == 1, "event.handler_failed non publié"
    bus.replay("x.flaky")
    assert attempts["n"] == 2, f"pas de nouvelle tentative au replay (tentatives={attempts['n']})"
    bus.replay("x.flaky")
    assert attempts["n"] == 2, "un événement déjà traité avec succès a été rejoué"


@check("B. Socle", "Cohérence des abonnements/émissions d'événements entre manifestes")
def _() -> None:
    need_runtime()
    emitted = set(V.VENTES_EMITTED) | set(V.DGI_EMITTED) | set(V.ACHATS_EMITTED) | {POSTED}
    subs: dict[str, set[str]] = {}
    for mod in (V.VentesModule, V.DgiModule, V.AchatsModule, V.OdooAccountingModule):
        m = mod.manifest
        for ev in m.subscribes:
            subs.setdefault(ev, set()).add(m.name)
    orphan_subs = sorted(ev for ev in subs if ev not in emitted)
    orphan_emit = sorted(ev for ev in emitted if ev not in subs)
    notes = []
    if orphan_subs:
        notes.append("abonnements SANS producteur : " + ", ".join(
            f"{e} ({'/'.join(sorted(subs[e]))})" for e in orphan_subs))
    if orphan_emit:
        notes.append("événements émis SANS consommateur : " + ", ".join(orphan_emit))
    if notes:
        raise Warn(" | ".join(notes))


@check("B. Socle", "Toutes les permissions des manifestes sont déclarées auprès du Core")
def _() -> None:
    with env_ctx() as env:
        missing = []
        for mod in (env.ventes, env.dgi, env.achats, env.compta):
            for perm in mod.manifest.permissions:
                if not env.ctx.authz.is_permission_declared(perm):
                    missing.append(f"{mod.manifest.name}:{perm}")
        assert not missing, "permissions non déclarées : " + ", ".join(missing)


@check("B. Socle", "Démarrage, healthchecks puis arrêt propre des 4 modules")
def _() -> None:
    with env_ctx() as env:
        for mod in (env.ventes, env.dgi, env.achats, env.compta):
            assert mod.healthcheck(), f"healthcheck KO après démarrage : {mod.manifest.name}"
        env.registry.stop_all()
        S = V.core.ModuleState
        for name in ("dgi", "ventes", "achats", "comptabilite_odoo"):
            assert env.registry.get_state(name) == S.STOPPED, f"{name} non STOPPED après stop_all"
        for mod in (env.ventes, env.dgi, env.achats, env.compta):
            assert not mod.healthcheck(), f"healthcheck encore vrai après arrêt : {mod.manifest.name}"


# =========================================================================== #
# C. FLUX MÉTIER BOUT EN BOUT : Ventes → DGI → Ventes → Compta
# =========================================================================== #

@check("C. Flux", "Facture : brouillon → soumission → certification DGI → écriture comptable postée")
def _() -> None:
    with env_ctx() as env:
        inv = env.reload(env.submit())
        assert inv.number == NUMBER_1, f"numéro inattendu : {inv.number}"
        assert inv.status == V.InvoiceStatus.CERTIFIED, f"statut {inv.status} (attendu CERTIFIED)"
        stamp = inv.fiscal_stamp
        assert stamp is not None, "cachet fiscal absent"
        assert stamp.device_id == "MCF-TEST" and stamp.auth_code and stamp.qr_code, "cachet incomplet"

        types = env.types()
        assert READY in types and CERTIFIED in types, f"événements manquants : {types}"
        assert types.index(READY) < types.index(CERTIFIED), "ordre des événements incorrect"
        no_failures(env)

        moves = env.moves()
        assert len(moves) == 1, f"{len(moves)} écriture(s) Odoo (attendu 1)"
        mv = moves[0]
        assert mv.state == "posted", f"écriture à l'état {mv.state}"
        assert NUMBER_1 in mv.prepared.entry.source_ref, f"source_ref={mv.prepared.entry.source_ref}"
        debit = sum((pl.line.debit for pl in mv.prepared.lines), Decimal("0"))
        credit = sum((pl.line.credit for pl in mv.prepared.lines), Decimal("0"))
        assert debit == credit and debit > 0, f"écriture déséquilibrée : débit={debit} crédit={credit}"
        assert types.count(POSTED) == 1, f"{types.count(POSTED)} événement(s) {POSTED} (attendu 1)"


@check("C. Flux", "Idempotence : replay du bus et republication ne dupliquent aucune écriture")
def _() -> None:
    with env_ctx() as env:
        env.submit()
        assert len(env.moves()) == 1
        replayed = env.bus.replay()
        assert replayed > 0, "replay n'a rien rejoué"
        last = env.store.list_events(CERTIFIED)[-1]
        env.bus.publish(V.core.Event(type=CERTIFIED, payload=dict(last.payload)))  # nouvel id, même contenu
        no_failures(env)
        assert len(env.moves()) == 1, f"{len(env.moves())} écritures après replay (doublon)"


@check("C. Flux", "Rejet DGI : facture REJECTED, aucune écriture comptable")
def _() -> None:
    with env_ctx() as env:
        env.client.reject_serials.add(NUMBER_1)
        inv = env.reload(env.submit())
        assert inv.status == V.InvoiceStatus.REJECTED, f"statut {inv.status}"
        assert inv.fiscal_stamp is None and inv.rejection_reason, "cachet/motif incohérents"
        assert not env.moves(), "une écriture a été créée pour une facture rejetée"
        no_failures(env)


@check("C. Flux", "Panne DGI : facture VALIDATED puis certifiée à la reprise, écriture créée ensuite")
def _() -> None:
    with env_ctx() as env:
        env.client.simulate_outage(True)
        invoice_id = env.submit()
        assert env.reload(invoice_id).status == V.InvoiceStatus.VALIDATED, "facture non conservée en VALIDATED"
        assert not env.moves(), "écriture créée avant certification"
        env.client.simulate_outage(False)
        env.dgi.process_retries(NOW + timedelta(hours=1))
        inv = env.reload(invoice_id)
        assert inv.status == V.InvoiceStatus.CERTIFIED, f"statut après reprise : {inv.status}"
        no_failures(env)
        assert len(env.moves()) == 1, f"{len(env.moves())} écriture(s) après reprise DGI (attendu 1)"


@check("C. Flux", "Panne Odoo : la facture reste CERTIFIED, l'écriture est rejouée au retour d'Odoo")
def _() -> None:
    with env_ctx() as env:
        env.odoo.available = False
        inv = env.reload(env.submit())
        assert inv.status == V.InvoiceStatus.CERTIFIED, "la panne comptable a bloqué la facturation"
        assert not env.moves()
        env.odoo.available = True
        env.box["now"] = NOW + timedelta(hours=1)
        env.compta.process_pending()
        if not env.moves():
            env.bus.replay()
            if len(env.moves()) == 1:
                raise Warn("écriture créée seulement via replay du bus, pas via la file d'attente compta")
        assert len(env.moves()) == 1, (
            f"{len(env.moves())} écriture(s) après retour d'Odoo (attendu 1). "
            f"Handlers en échec : {env.failures() or 'aucun'}")


@check("C. Flux", "Numérotation séquentielle sans trou sur deux factures consécutives")
def _() -> None:
    with env_ctx() as env:
        n1 = env.reload(env.submit()).number
        n2 = env.reload(env.submit()).number
        assert (n1, n2) == (NUMBER_1, NUMBER_2), f"numéros : {n1}, {n2}"
        assert len(env.moves()) == 2, f"{len(env.moves())} écritures pour 2 factures"
        no_failures(env)


@check("C. Flux", "Permissions : un utilisateur sans droit ne peut pas créer de facture")
def _() -> None:
    with env_ctx() as env:
        intrus = V.core.User(id="U2", username="intrus", direct_permissions=frozenset())
        try:
            env.submit(actor=intrus)
        except Exception:  # noqa: BLE001  (le type exact dépend du module : on exige un refus)
            assert not env.types().count(READY), "événement émis malgré le refus"
            return
        raise AssertionError("création de facture acceptée sans permission ventes.create")


@check("C. Flux", "Achats : commande fournisseur confirmée → événement émis, sans erreur de handler")
def _() -> None:
    with env_ctx() as env:
        core = V.core
        perms = frozenset({"achats.view", "achats.create", "achats.approve", "achats.receive",
                           "achats.landed_cost", "achats.admin"})
        buyer = core.User(id="U3", username="acheteur", direct_permissions=perms)
        env.achats.supplier_service.create_supplier(buyer, "SUP-001", "Fournisseur Test")
        po_svc = env.achats.purchase_order_service
        po_svc.create_order(buyer, "PO-001", "SUP-001", date(2026, 11, 20), core.Currency.USD)
        po_svc.add_line(buyer, "PO-001", "L1", "ART-01", "Article", Decimal("5"),
                        core.Money(Decimal("10.00"), core.Currency.USD))
        po_svc.confirm_order(buyer, "PO-001")
        assert PO_CONFIRMED in env.types(), f"événement {PO_CONFIRMED} non émis"
        no_failures(env)
        if not env.moves():
            raise Warn("écart de conception connu : Achats n'émet aucun événement comptable, "
                       "donc aucune écriture d'achat n'est générée côté Compta")


# =========================================================================== #
# D. PERSISTANCE : redémarrage sur base fichier
# =========================================================================== #

@check("D. Persistance", "Redémarrage : migrations rejouables, données et numérotation conservées")
def _() -> None:
    need_runtime()
    with tempfile.TemporaryDirectory(prefix="vorpal_verif_") as tmp:
        url = f"sqlite:///{tmp}/verif.db"
        env1 = build_env(db_url=url)
        try:
            first_id = env1.submit()
            assert env1.reload(first_id).number == NUMBER_1
            env1.registry.stop_all()
        finally:
            env1.close()
        env2 = build_env(db_url=url, seed_customer=False)  # client déjà persisté ; migrations rejouées
        try:
            assert env2.reload(first_id).status == V.InvoiceStatus.CERTIFIED, "facture perdue au redémarrage"
            second = env2.reload(env2.submit())
            assert second.number == NUMBER_2, f"numérotation non continue après redémarrage : {second.number}"
        finally:
            env2.close()


# =========================================================================== #
# Exécution et rapport
# =========================================================================== #

def run(selected: list[Check], verbose: bool) -> list[Result]:
    results: list[Result] = []
    for c in selected:
        t0 = time.perf_counter()
        try:
            c.fn()
            status, detail = "OK", ""
        except Skip as exc:
            status, detail = "IGNORÉ", str(exc)
        except Warn as exc:
            status, detail = "AVERT.", str(exc)
        except AssertionError as exc:
            status, detail = "ECHEC", str(exc) or "assertion fausse"
        except Exception as exc:  # noqa: BLE001
            status = "ERREUR"
            detail = f"{type(exc).__name__}: {exc}"
            if verbose:
                detail += "\n" + traceback.format_exc()
            else:
                tb = traceback.extract_tb(exc.__traceback__)
                if tb:
                    last = tb[-1]
                    detail += f"  [{Path(last.filename).name}:{last.lineno}]"
        results.append(Result(c.section, c.name, status, detail, time.perf_counter() - t0))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Vérifie le fonctionnement de l'architecture Vorpal.")
    ap.add_argument("--verbose", action="store_true", help="traces complètes pour les ERREUR")
    ap.add_argument("--only", default="", help="ne lancer que les contrôles dont le nom contient ce texte")
    ap.add_argument("--json", default="", help="écrire le rapport JSON dans ce fichier")
    args = ap.parse_args()

    if not (ROOT / "vorpal" / "core").is_dir():
        print("ERREUR : lance ce script depuis la racine du dépôt (dossier contenant ./vorpal).", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))

    needle = args.only.lower()
    selected = [c for c in CHECKS if needle in (c.section + " " + c.name).lower()]
    print(f"Vérification de l'architecture Vorpal — {len(selected)} contrôle(s) — {ROOT}\n")
    results = run(selected, args.verbose)

    section = ""
    for r in results:
        if r.section != section:
            section = r.section
            print(f"\n{section}")
        print(f"  [{r.status:7}] {r.name}  ({r.seconds:.2f}s)")
        if r.detail:
            for line in r.detail.splitlines():
                print(f"             {line}")

    count = {s: sum(1 for r in results if r.status == s) for s in ("OK", "AVERT.", "IGNORÉ", "ECHEC", "ERREUR")}
    print("\n" + "=" * 70)
    print("  ".join(f"{k}: {v}" for k, v in count.items()))
    if args.json:
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"Rapport JSON : {args.json}")
    print("Rappel : DGI simulée (MockMCFClient) et Odoo simulé (FakeOdooClient) : aucune homologation, "
          "aucune validation Odoo 19.")
    return 1 if count["ECHEC"] or count["ERREUR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
