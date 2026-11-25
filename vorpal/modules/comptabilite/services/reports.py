"""États comptables en lecture seule : balance générale, grand livre, bilan, compte de résultat.

Règles de calcul (TODO(A_VALIDER) par un expert-comptable) :
- Comptes de bilan (classes 1 à 5) : soldes cumulés depuis l'origine, écritures de clôture comprises.
- Comptes de gestion (classes 6 à 8) : mouvements de l'exercice uniquement ; les écritures de clôture sont
  exclues des mouvements (sinon la clôture annulerait le résultat de l'exercice qu'elle solde).
- Le bilan reprend le résultat non encore affecté (classes 6 à 8 cumulées) dans les capitaux propres.
- La classe 9 (comptabilité analytique) est hors états.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from vorpal.modules.comptabilite.errors import AccountingValidationError
from vorpal.modules.comptabilite.ports.repositories import AccountTotals, LedgerRepository
from vorpal.modules.comptabilite.reporting import engine
from vorpal.modules.comptabilite.reporting.models import (
    ZERO,
    GeneralLedger,
    LedgerAccount,
    LedgerLine,
    Statement,
    TrialBalance,
    TrialBalanceRow,
    split_sides,
)
from vorpal.modules.comptabilite.services.common import AccountingDependencies
from vorpal.modules.comptabilite.services.posting import fiscal_year_for

BALANCE_SHEET_CLASSES = "12345"
RESULT_CLASSES = "678"


def _nets(totals: list[AccountTotals], classes: str) -> dict[str, Decimal]:
    return {t.account_code: t.debit - t.credit for t in totals if t.account_code[0] in classes}


def _add(a: dict[str, Decimal], b: dict[str, Decimal]) -> dict[str, Decimal]:
    out = dict(a)
    for code, value in b.items():
        out[code] = out.get(code, ZERO) + value
    return out


class ReportService:
    def __init__(self, deps: AccountingDependencies) -> None:
        self._deps = deps

    # ----------------------------------------------------------------- utilitaires
    @staticmethod
    def _year_start(repo: LedgerRepository, day: date) -> date:
        year = fiscal_year_for(repo, day)
        return year.start if year is not None else date(day.year, 1, 1)

    def _opening(self, repo: LedgerRepository, date_from: date) -> tuple[dict[str, Decimal], Decimal]:
        """Soldes d'ouverture par compte + écart de résultat antérieur non reporté (0 si clôturé)."""
        before = date_from - timedelta(days=1)
        start = self._year_start(repo, date_from)
        bs = _nets(repo.account_totals(None, before, include_closing=True), BALANCE_SHEET_CLASSES)
        pl = (
            _nets(repo.account_totals(start, before, include_closing=False), RESULT_CLASSES)
            if start <= before
            else {}
        )
        carried = _nets(repo.account_totals(None, start - timedelta(days=1), include_closing=True), RESULT_CLASSES)
        gap = sum(carried.values(), ZERO)
        return _add(bs, pl), gap

    @staticmethod
    def _check_range(date_from: date, date_to: date) -> None:
        if date_to < date_from:
            raise AccountingValidationError("La date de fin précède la date de début.")

    # ----------------------------------------------------------------- balance
    def trial_balance(self, date_from: date, date_to: date, *, include_zero: bool = False) -> TrialBalance:
        self._check_range(date_from, date_to)
        with self._deps.uow_factory() as uow:
            repo = uow.ledger
            names = {a.code: a.name for a in repo.list_accounts()}
            opening, gap = self._opening(repo, date_from)
            moves = {
                t.account_code: t
                for t in repo.account_totals(date_from, date_to, include_closing=False)
                if t.account_code[0] in "12345678"
            }
        rows: list[TrialBalanceRow] = []
        for code in sorted(set(opening) | set(moves)):
            op = opening.get(code, ZERO)
            mv = moves.get(code)
            debit = mv.debit if mv else ZERO
            credit = mv.credit if mv else ZERO
            closing = op + debit - credit
            if not include_zero and op == 0 and debit == 0 and credit == 0:
                continue
            o_d, o_c = split_sides(op)
            c_d, c_c = split_sides(closing)
            rows.append(TrialBalanceRow(code, names.get(code, code), o_d, o_c, debit, credit, c_d, c_c))
        warnings: list[str] = []
        if gap != 0:
            warnings.append(
                f"Résultat d'exercices antérieurs non clôturés ({gap}) : clôturez les exercices précédents."
            )
        return TrialBalance(date_from, date_to, tuple(rows), tuple(warnings))

    # ----------------------------------------------------------------- grand livre
    def general_ledger(
        self,
        date_from: date,
        date_to: date,
        *,
        account_codes: list[str] | None = None,
        prefix: str | None = None,
    ) -> GeneralLedger:
        self._check_range(date_from, date_to)
        with self._deps.uow_factory() as uow:
            repo = uow.ledger
            names = {a.code: a.name for a in repo.list_accounts()}
            opening, _ = self._opening(repo, date_from)
            if account_codes is not None:
                codes = sorted(set(account_codes))
            else:
                active = {
                    t.account_code
                    for t in repo.account_totals(date_from, date_to, include_closing=False, account_prefix=prefix)
                }
                with_opening = {c for c, v in opening.items() if v != 0 and (prefix is None or c.startswith(prefix))}
                codes = sorted(active | with_opening)
            accounts: list[LedgerAccount] = []
            for code in codes:
                running = opening.get(code, ZERO)
                lines: list[LedgerLine] = []
                for row in repo.ledger_rows(code, date_from, date_to, include_closing=False):
                    running += row.debit - row.credit
                    lines.append(
                        LedgerLine(
                            row.entry_date,
                            row.number,
                            row.journal_code,
                            row.label or row.entry_label,
                            row.reference,
                            row.partner_ref,
                            row.debit,
                            row.credit,
                            running,
                        )
                    )
                accounts.append(LedgerAccount(code, names.get(code, code), opening.get(code, ZERO), tuple(lines)))
        return GeneralLedger(date_from, date_to, tuple(accounts))

    # ----------------------------------------------------------------- états financiers
    def balance_sheet(self, as_of: date) -> Statement:
        with self._deps.uow_factory() as uow:
            totals = uow.ledger.account_totals(None, as_of, include_closing=True)
        balances = _nets(totals, BALANCE_SHEET_CLASSES + RESULT_CLASSES)
        definition = engine.load_definition("bilan")
        lines = engine.evaluate(definition, balances)
        statement = Statement(
            "bilan", str(definition["title"]), str(definition["status"]), None, as_of, tuple(lines)
        )
        gap = statement.value("BZ") - statement.value("DZ")
        if gap != 0:
            return Statement(
                statement.code,
                statement.title,
                statement.status,
                None,
                as_of,
                statement.lines,
                (f"Bilan déséquilibré : actif - passif = {gap} (comptes hors définition ou écritures incomplètes).",),
            )
        return statement

    def income_statement(self, date_from: date, date_to: date) -> Statement:
        self._check_range(date_from, date_to)
        with self._deps.uow_factory() as uow:
            totals = uow.ledger.account_totals(date_from, date_to, include_closing=False)
        balances = _nets(totals, RESULT_CLASSES)
        definition = engine.load_definition("compte_resultat")
        lines = engine.evaluate(definition, balances)
        return Statement(
            "compte_resultat",
            str(definition["title"]),
            str(definition["status"]),
            date_from,
            date_to,
            tuple(lines),
        )
