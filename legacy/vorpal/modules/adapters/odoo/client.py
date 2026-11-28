"""Client Odoo : contrat abstrait, implémentation XML-RPC (stdlib) et faux client de test."""
from __future__ import annotations

import os
import socket
import xmlrpc.client
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .dto import MoveRef, PreparedMove
from .errors import (
    DuplicateSourceRefError,
    MappingError,
    OdooRejectedError,
    OdooUnavailableError,
)


class OdooClient(ABC):
    """Seule porte d'entrée vers Odoo. Aucun modèle interne d'Odoo ne sort d'ici."""

    @abstractmethod
    def get_company_currency(self) -> str: ...

    @abstractmethod
    def get_rate(self, currency: str, on: date) -> Decimal | None:
        """Unités de devise société pour 1 unité de ``currency`` (None si inconnu)."""

    @abstractmethod
    def get_account_id(self, code: str) -> int: ...

    @abstractmethod
    def get_journal_id(self, code: str) -> int: ...

    @abstractmethod
    def get_journal_account_id(self, journal_code: str) -> int:
        """Compte par défaut (banque/caisse) du journal."""

    @abstractmethod
    def ensure_partner(self, ref: str, name: str | None) -> int: ...

    @abstractmethod
    def find_move_by_source_ref(self, ref: str) -> MoveRef | None: ...

    @abstractmethod
    def create_move(self, move: PreparedMove) -> int:
        """Crée l'écriture en brouillon. Lève DuplicateSourceRefError si la référence existe."""

    @abstractmethod
    def post_move(self, move_id: int) -> None: ...


# --------------------------------------------------------------------------- XML-RPC
@dataclass(frozen=True)
class OdooConfig:
    url: str
    db: str
    user: str
    api_key: str
    company_id: int | None = None
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> OdooConfig:
        e = os.environ if env is None else env
        missing = [k for k in ("VORPAL_ODOO_URL", "VORPAL_ODOO_DB", "VORPAL_ODOO_USER",
                               "VORPAL_ODOO_API_KEY") if not e.get(k)]
        if missing:
            raise MappingError(f"variables d'environnement manquantes : {', '.join(missing)}")
        cid = e.get("VORPAL_ODOO_COMPANY_ID")
        return cls(
            url=e["VORPAL_ODOO_URL"].rstrip("/"),
            db=e["VORPAL_ODOO_DB"],
            user=e["VORPAL_ODOO_USER"],
            api_key=e["VORPAL_ODOO_API_KEY"],
            company_id=int(cid) if cid else None,
            timeout=float(e.get("VORPAL_ODOO_TIMEOUT", "30")),
        )


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> Any:
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


_TRANSIENT_MARKERS = ("could not serialize", "deadlock", "lock timeout", "connection", "timeout")
_DUP_MARKERS = ("vorpal_source_ref", "duplicate key", "unique constraint")


def classify_fault(fault: xmlrpc.client.Fault) -> Exception:
    text = str(fault.faultString).lower()
    if any(m in text for m in _DUP_MARKERS) and "vorpal_source_ref" in text:
        return DuplicateSourceRefError(str(fault.faultString)[:300])
    if any(m in text for m in _TRANSIENT_MARKERS):
        return OdooUnavailableError(str(fault.faultString)[:300])
    return OdooRejectedError(str(fault.faultString)[:300])


ProxyFactory = Callable[[str], Any]


class XmlRpcOdooClient(OdooClient):
    """Client XML-RPC. Identifiants uniquement via configuration/variables d'environnement.

    TODO(A_VALIDER) : non exercé contre une instance Odoo réelle (testé avec un proxy simulé).
    """

    def __init__(self, config: OdooConfig, proxy_factory: ProxyFactory | None = None) -> None:
        self._cfg = config
        self._factory: ProxyFactory = proxy_factory or (
            lambda path: xmlrpc.client.ServerProxy(
                f"{config.url}{path}", transport=_TimeoutTransport(config.timeout),
                allow_none=True)
        )
        self._uid: int | None = None
        self._account_cache: dict[str, int] = {}
        self._journal_cache: dict[str, int] = {}
        self._company_currency: str | None = None

    # -- bas niveau
    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except xmlrpc.client.Fault as fault:
            raise classify_fault(fault) from fault
        except (OSError, socket.timeout, xmlrpc.client.ProtocolError) as exc:
            raise OdooUnavailableError(f"Odoo injoignable : {exc}") from exc

    def _authenticate(self) -> int:
        if self._uid is None:
            common = self._factory("/xmlrpc/2/common")
            uid = self._call(lambda: common.authenticate(
                self._cfg.db, self._cfg.user, self._cfg.api_key, {}))
            if not uid:
                raise OdooRejectedError("authentification Odoo refusée")
            self._uid = int(uid)
        return self._uid

    def _ctx(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {}
        if self._cfg.company_id:
            ctx["allowed_company_ids"] = [self._cfg.company_id]
        return ctx

    def _exec(self, model: str, method: str, args: list[Any],
              kwargs: dict[str, Any] | None = None) -> Any:
        uid = self._authenticate()
        obj = self._factory("/xmlrpc/2/object")
        kw = dict(kwargs or {})
        kw["context"] = {**self._ctx(), **kw.get("context", {})}
        return self._call(lambda: obj.execute_kw(
            self._cfg.db, uid, self._cfg.api_key, model, method, args, kw))

    def _company_domain(self) -> list[Any]:
        return [("company_id", "=", self._cfg.company_id)] if self._cfg.company_id else []

    # -- contrat
    def get_company_currency(self) -> str:
        if self._company_currency is None:
            cid = self._cfg.company_id
            domain: list[Any] = [("id", "=", cid)] if cid else []
            rows = self._exec("res.company", "search_read", [domain],
                              {"fields": ["currency_id"], "limit": 1})
            if not rows:
                raise MappingError("société Odoo introuvable")
            self._company_currency = str(rows[0]["currency_id"][1])
        return self._company_currency

    def get_rate(self, currency: str, on: date) -> Decimal | None:
        rows = self._exec("res.currency", "search_read",
                          [[("name", "=", currency), ("active", "=", True)]],
                          {"fields": ["id"], "limit": 1})
        if not rows:
            return None
        rates = self._exec("res.currency.rate", "search_read",
                           [[("currency_id", "=", rows[0]["id"]), ("name", "<=", on.isoformat())]
                            + self._company_domain()],
                           {"fields": ["company_rate", "rate"], "order": "name desc", "limit": 1})
        if not rates:
            return None
        per_company = rates[0].get("company_rate") or rates[0].get("rate")
        if not per_company:
            return None
        # Odoo stocke des unités de devise étrangère pour 1 unité de devise société.
        return Decimal(1) / Decimal(str(per_company))

    def get_account_id(self, code: str) -> int:
        if code in self._account_cache:
            return self._account_cache[code]
        # Le plan est généré sur 6 chiffres (ex. 4111 -> 411100) : repli par préfixe.
        for domain in ([("code", "=", code)], [("code", "=like", f"{code}%")]):
            rows = self._exec("account.account", "search_read", [domain],
                              {"fields": ["id"], "order": "code asc", "limit": 1})
            if rows:
                self._account_cache[code] = int(rows[0]["id"])
                return self._account_cache[code]
        raise MappingError(f"compte {code} introuvable dans Odoo")

    def get_journal_id(self, code: str) -> int:
        if code not in self._journal_cache:
            rows = self._exec("account.journal", "search_read",
                              [[("code", "=", code)] + self._company_domain()],
                              {"fields": ["id"], "limit": 1})
            if not rows:
                raise MappingError(f"journal {code} introuvable dans Odoo")
            self._journal_cache[code] = int(rows[0]["id"])
        return self._journal_cache[code]

    def get_journal_account_id(self, journal_code: str) -> int:
        rows = self._exec("account.journal", "read", [[self.get_journal_id(journal_code)]],
                          {"fields": ["default_account_id"]})
        acc = rows[0].get("default_account_id") if rows else None
        if not acc:
            raise MappingError(f"journal {journal_code} sans compte par défaut")
        return int(acc[0])

    def ensure_partner(self, ref: str, name: str | None) -> int:
        rows = self._exec("res.partner", "search_read", [[("ref", "=", ref)]],
                          {"fields": ["id"], "limit": 1})
        if rows:
            return int(rows[0]["id"])
        return int(self._exec("res.partner", "create", [{"name": name or ref, "ref": ref}]))

    def find_move_by_source_ref(self, ref: str) -> MoveRef | None:
        rows = self._exec("account.move", "search_read",
                          [[("vorpal_source_ref", "=", ref)] + self._company_domain()],
                          {"fields": ["state"], "limit": 1})
        return MoveRef(int(rows[0]["id"]), str(rows[0]["state"])) if rows else None

    def _resolve_account(self, code: str) -> int:
        if code.startswith("journal:"):
            return self.get_journal_account_id(code.split(":", 1)[1])
        return self.get_account_id(code)

    def create_move(self, move: PreparedMove) -> int:
        e = move.entry
        foreign = e.currency != move.company_currency
        cur_id = None
        if foreign:
            rows = self._exec("res.currency", "search_read", [[("name", "=", e.currency)]],
                              {"fields": ["id"], "limit": 1})
            if not rows:
                raise MappingError(f"devise {e.currency} inactive/introuvable")
            cur_id = int(rows[0]["id"])
        line_cmds: list[Any] = []
        for pl in move.lines:
            ln = pl.line
            vals: dict[str, Any] = {
                "account_id": self._resolve_account(ln.account_code),
                "name": ln.label or e.reference or e.source_ref,
                "debit": float(pl.debit_company),
                "credit": float(pl.credit_company),
            }
            if ln.partner_ref:
                vals["partner_id"] = self.ensure_partner(ln.partner_ref, ln.partner_name)
            if foreign:
                vals["currency_id"] = cur_id
                vals["amount_currency"] = float(ln.debit - ln.credit)
            line_cmds.append((0, 0, vals))
        vals_move: dict[str, Any] = {
            "move_type": "entry",
            "journal_id": self.get_journal_id(e.journal_code),
            "date": e.entry_date.isoformat(),
            "ref": e.reference or e.source_ref,
            "narration": e.narration,
            "vorpal_source_ref": e.source_ref,
            "line_ids": line_cmds,
        }
        if e.dgi_code:
            vals_move["vorpal_dgi_code"] = e.dgi_code
            vals_move["vorpal_dgi_qr"] = e.dgi_qr or ""
        return int(self._exec("account.move", "create", [vals_move]))

    def post_move(self, move_id: int) -> None:
        self._exec("account.move", "action_post", [[move_id]])
