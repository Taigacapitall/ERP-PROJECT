"""DTO propres à l'adaptateur Odoo (aucune dépendance au Core ni à Odoo).

TODO(A_VALIDER) : aligner ces DTO sur le ``JournalEntryDTO`` du Core dès que son
contrat est disponible. Ce fichier est la seule zone à adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .errors import UnbalancedEntryError

ZERO = Decimal("0")


@dataclass(frozen=True)
class JournalLineDTO:
    """Ligne en devise de transaction. ``account_code`` peut valoir ``journal:<CODE>``
    pour désigner le compte par défaut d'un journal (banque/caisse)."""

    account_code: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    label: str = ""
    partner_ref: str | None = None
    partner_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_code": self.account_code,
            "debit": str(self.debit),
            "credit": str(self.credit),
            "label": self.label,
            "partner_ref": self.partner_ref,
            "partner_name": self.partner_name,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JournalLineDTO:
        return cls(
            account_code=d["account_code"],
            debit=Decimal(d["debit"]),
            credit=Decimal(d["credit"]),
            label=d.get("label", ""),
            partner_ref=d.get("partner_ref"),
            partner_name=d.get("partner_name"),
        )


@dataclass(frozen=True)
class JournalEntryDTO:
    source_ref: str
    journal_code: str
    entry_date: date
    currency: str
    lines: tuple[JournalLineDTO, ...]
    reference: str = ""
    narration: str = ""
    exchange_rate: Decimal | None = None  # unités de devise société pour 1 unité de `currency`
    dgi_code: str | None = None
    dgi_qr: str | None = None

    def validate(self) -> None:
        if not self.source_ref:
            raise UnbalancedEntryError("source_ref obligatoire")
        if len(self.lines) < 2:
            raise UnbalancedEntryError("une écriture exige au moins deux lignes")
        total_d = ZERO
        total_c = ZERO
        for ln in self.lines:
            if ln.debit < 0 or ln.credit < 0:
                raise UnbalancedEntryError("montants négatifs interdits")
            if (ln.debit > 0) == (ln.credit > 0):
                raise UnbalancedEntryError(
                    f"la ligne {ln.account_code} doit avoir exactement un côté non nul"
                )
            total_d += ln.debit
            total_c += ln.credit
        if total_d != total_c:
            raise UnbalancedEntryError(f"débit {total_d} != crédit {total_c} ({self.currency})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "journal_code": self.journal_code,
            "entry_date": self.entry_date.isoformat(),
            "currency": self.currency,
            "lines": [ln.to_dict() for ln in self.lines],
            "reference": self.reference,
            "narration": self.narration,
            "exchange_rate": None if self.exchange_rate is None else str(self.exchange_rate),
            "dgi_code": self.dgi_code,
            "dgi_qr": self.dgi_qr,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JournalEntryDTO:
        rate = d.get("exchange_rate")
        return cls(
            source_ref=d["source_ref"],
            journal_code=d["journal_code"],
            entry_date=date.fromisoformat(d["entry_date"]),
            currency=d["currency"],
            lines=tuple(JournalLineDTO.from_dict(x) for x in d["lines"]),
            reference=d.get("reference", ""),
            narration=d.get("narration", ""),
            exchange_rate=None if rate is None else Decimal(rate),
            dgi_code=d.get("dgi_code"),
            dgi_qr=d.get("dgi_qr"),
        )


@dataclass(frozen=True)
class PreparedLine:
    """Ligne avec montants en devise de transaction ET en devise société."""

    line: JournalLineDTO
    debit_company: Decimal
    credit_company: Decimal


@dataclass(frozen=True)
class PreparedMove:
    entry: JournalEntryDTO
    company_currency: str
    rate: Decimal
    lines: tuple[PreparedLine, ...]


@dataclass(frozen=True)
class MoveRef:
    move_id: int
    state: str  # 'draft' | 'posted' | 'cancel'


@dataclass(frozen=True)
class PostResult:
    move_id: int
    created: bool
    posted: bool
