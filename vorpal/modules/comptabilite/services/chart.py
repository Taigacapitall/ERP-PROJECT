"""Plan comptable, journaux et exercices : installation, import, résolution de codes."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

from vorpal.modules.comptabilite.domain.accounts import Account, Journal, JournalKind
from vorpal.modules.comptabilite.domain.periods import FiscalYear
from vorpal.modules.comptabilite.errors import (
    AccountingConflictError,
    AccountingNotFoundError,
    AccountingValidationError,
)
from vorpal.modules.comptabilite.services.common import AccountingDependencies

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_PLAN_PATH = DATA_DIR / "plan_syscohada_base.json"
DEFAULT_JOURNALS_PATH = DATA_DIR / "journals.json"

_TRUE = {"1", "true", "t", "yes", "oui", "vrai"}


class ChartService:
    """Opérations de paramétrage du grand livre. Toutes sont idempotentes (rejouables sans effet)."""

    def __init__(self, deps: AccountingDependencies) -> None:
        self._deps = deps

    # ------------------------------------------------------------------ plan
    def install_default_plan(self, path: Path | None = None) -> int:
        """Charge un plan JSON (plan de base fourni par défaut). N'écrase pas les comptes existants."""
        data: dict[str, Any] = json.loads((path or DEFAULT_PLAN_PATH).read_text(encoding="utf-8"))
        accounts = [
            Account(code=str(a["code"]), name=str(a["name"]), reconcilable=bool(a.get("reconcilable", False)))
            for a in data["accounts"]
        ]
        return self._insert_missing(accounts)

    def import_csv(
        self,
        source: Path | str,
        *,
        code_column: str = "code",
        name_column: str = "name",
        reconcile_column: str = "reconcile",
        update_existing: bool = False,
    ) -> int:
        """Importe un plan CSV (ex. ``account.account-syscohada.csv`` d'Odoo). Retourne le nombre de
        comptes créés (ou mis à jour si ``update_existing``). Une ligne invalide arrête l'import entier."""
        text = source.read_text(encoding="utf-8-sig") if isinstance(source, Path) else source
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or code_column not in reader.fieldnames or name_column not in reader.fieldnames:
            raise AccountingValidationError(
                f"Colonnes '{code_column}' et '{name_column}' obligatoires dans le fichier CSV."
            )
        accounts: list[Account] = []
        for number, row in enumerate(reader, start=2):
            code = (row.get(code_column) or "").strip()
            name = (row.get(name_column) or "").strip()
            if not code and not name:
                continue
            try:
                accounts.append(
                    Account(
                        code=code,
                        name=name,
                        reconcilable=(row.get(reconcile_column) or "").strip().lower() in _TRUE,
                    )
                )
            except AccountingValidationError as exc:
                raise AccountingValidationError(f"Ligne {number} du CSV : {exc.message}") from exc
        return self._insert_missing(accounts, update_existing=update_existing)

    def _insert_missing(self, accounts: list[Account], *, update_existing: bool = False) -> int:
        count = 0
        with self._deps.uow_factory() as uow:
            for account in accounts:
                if uow.ledger.get_account(account.code) is None or update_existing:
                    uow.ledger.save_account(account)
                    count += 1
            uow.commit()
        return count

    def add_account(self, account: Account) -> None:
        with self._deps.uow_factory() as uow:
            if uow.ledger.get_account(account.code) is not None:
                raise AccountingConflictError(f"Le compte {account.code} existe déjà.")
            uow.ledger.save_account(account)
            uow.commit()

    def set_account_active(self, code: str, active: bool) -> None:
        with self._deps.uow_factory() as uow:
            current = uow.ledger.get_account(code)
            if current is None:
                raise AccountingNotFoundError(f"Compte '{code}' introuvable.")
            uow.ledger.save_account(
                Account(code=current.code, name=current.name, reconcilable=current.reconcilable, is_active=active)
            )
            uow.commit()

    def resolve_account(self, code: str) -> Account:
        """Compte exact, sinon premier compte actif dont le code commence par ``code`` (plans à 6 chiffres)."""
        with self._deps.uow_factory() as uow:
            exact = uow.ledger.get_account(code)
            if exact is not None and exact.is_active:
                return exact
            found = uow.ledger.find_first_by_prefix(code)
        if found is None:
            raise AccountingNotFoundError(f"Aucun compte actif ne correspond à '{code}'.")
        return found

    # --------------------------------------------------------------- journaux
    def install_default_journals(self, path: Path | None = None) -> int:
        data: dict[str, Any] = json.loads((path or DEFAULT_JOURNALS_PATH).read_text(encoding="utf-8"))
        count = 0
        with self._deps.uow_factory() as uow:
            for j in data["journals"]:
                if uow.ledger.get_journal(str(j["code"])) is None:
                    uow.ledger.save_journal(
                        Journal(
                            code=str(j["code"]),
                            name=str(j["name"]),
                            kind=JournalKind(str(j["kind"])),
                            currency=j.get("currency"),
                            default_account=j.get("default_account"),
                        )
                    )
                    count += 1
            uow.commit()
        return count

    def add_journal(self, journal: Journal) -> None:
        with self._deps.uow_factory() as uow:
            if uow.ledger.get_journal(journal.code) is not None:
                raise AccountingConflictError(f"Le journal {journal.code} existe déjà.")
            if journal.default_account is not None and uow.ledger.get_account(journal.default_account) is None:
                raise AccountingNotFoundError(f"Compte par défaut '{journal.default_account}' introuvable.")
            uow.ledger.save_journal(journal)
            uow.commit()

    # --------------------------------------------------------------- exercices
    def ensure_fiscal_year(self, year: int) -> FiscalYear:
        """Crée l'exercice civil ``year`` s'il n'existe pas (refuse tout chevauchement)."""
        wanted = FiscalYear.calendar(year)
        with self._deps.uow_factory() as uow:
            existing = uow.ledger.get_fiscal_year(wanted.code)
            if existing is not None:
                return existing
            for other in uow.ledger.list_fiscal_years():
                if not (wanted.end < other.start or wanted.start > other.end):
                    raise AccountingConflictError(f"L'exercice {wanted.code} chevauche l'exercice {other.code}.")
            uow.ledger.save_fiscal_year(wanted)
            uow.commit()
        return wanted

    def install_defaults(self, current_year: date | None = None) -> None:
        """Plan de base, journaux standards et exercice courant (tout est idempotent)."""
        self.install_default_plan()
        self.install_default_journals()
        self.ensure_fiscal_year((current_year or self._deps.clock().date()).year)
