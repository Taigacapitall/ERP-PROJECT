"""FakeOdooClient : double de test en mémoire, sans Odoo."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .client import OdooClient
from .dto import MoveRef, PreparedMove
from .errors import DuplicateSourceRefError, MappingError, OdooUnavailableError


@dataclass
class FakeMove:
    move_id: int
    prepared: PreparedMove
    state: str = "draft"


@dataclass
class FakeOdooClient(OdooClient):
    company_currency: str = "USD"
    rates: dict[str, Decimal] = field(default_factory=lambda: {"CDF": Decimal("0.00035")})
    accounts: dict[str, int] = field(default_factory=dict)  # codes préfixes, 6 chiffres simulés
    journals: dict[str, int] = field(default_factory=dict)
    journal_accounts: dict[str, int] = field(default_factory=dict)
    moves: dict[int, FakeMove] = field(default_factory=dict)
    partners: dict[str, int] = field(default_factory=dict)
    available: bool = True
    fail_post_once: bool = False
    calls: list[str] = field(default_factory=list)
    _seq: int = 0

    def _check(self) -> None:
        if not self.available:
            raise OdooUnavailableError("Odoo indisponible (simulation)")

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def get_company_currency(self) -> str:
        self._check()
        return self.company_currency

    def get_rate(self, currency: str, on: date) -> Decimal | None:
        self._check()
        return self.rates.get(currency)

    def get_account_id(self, code: str) -> int:
        self._check()
        if code in self.accounts:
            return self.accounts[code]
        matches = sorted(c for c in self.accounts if c.startswith(code))
        if matches:
            return self.accounts[matches[0]]
        raise MappingError(f"compte {code} introuvable")

    def get_journal_id(self, code: str) -> int:
        self._check()
        if code not in self.journals:
            raise MappingError(f"journal {code} introuvable")
        return self.journals[code]

    def get_journal_account_id(self, journal_code: str) -> int:
        self._check()
        if journal_code not in self.journal_accounts:
            raise MappingError(f"journal {journal_code} sans compte par défaut")
        return self.journal_accounts[journal_code]

    def ensure_partner(self, ref: str, name: str | None) -> int:
        self._check()
        return self.partners.setdefault(ref, 1000 + len(self.partners))

    def find_move_by_source_ref(self, ref: str) -> MoveRef | None:
        self._check()
        for m in self.moves.values():
            if m.prepared.entry.source_ref == ref:
                return MoveRef(m.move_id, m.state)
        return None

    def create_move(self, move: PreparedMove) -> int:
        self._check()
        if self.find_move_by_source_ref(move.entry.source_ref):
            raise DuplicateSourceRefError(move.entry.source_ref)
        # même contrôle qu'Odoo : toute ligne doit pointer vers un compte connu
        for pl in move.lines:
            if pl.line.account_code.startswith("journal:"):
                self.get_journal_account_id(pl.line.account_code.split(":", 1)[1])
            else:
                self.get_account_id(pl.line.account_code)
        self.get_journal_id(move.entry.journal_code)
        mid = self._next()
        self.moves[mid] = FakeMove(mid, move)
        self.calls.append(f"create:{move.entry.source_ref}")
        return mid

    def post_move(self, move_id: int) -> None:
        self._check()
        if self.fail_post_once:
            self.fail_post_once = False
            raise OdooUnavailableError("échec de validation (simulation)")
        self.moves[move_id].state = "posted"
        self.calls.append(f"post:{move_id}")
