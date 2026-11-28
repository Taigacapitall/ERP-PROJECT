"""Règles de verrouillage des écritures fiscales certifiées (logique pure)."""
from __future__ import annotations

from collections.abc import Iterable

# Champs dont la modification est interdite dès qu'un cachet DGI existe.
# (Les champs techniques de validation/lettrage restent modifiables.)
PROTECTED_MOVE_FIELDS = frozenset({
    "line_ids", "invoice_line_ids", "partner_id", "invoice_date", "currency_id",
    "journal_id", "company_id", "move_type", "vorpal_source_ref",
    "vorpal_dgi_code", "vorpal_dgi_qr", "invoice_date_due", "fiscal_position_id",
})
PROTECTED_LINE_FIELDS = frozenset({
    "debit", "credit", "balance", "amount_currency", "account_id", "partner_id",
    "currency_id", "tax_ids", "tax_line_id", "product_id", "quantity", "price_unit",
    "move_id", "company_id",
})


def move_write_violations(vals_keys: Iterable[str], has_stamp: bool) -> list[str]:
    """Champs interdits pour une écriture déjà certifiée (liste vide = écriture autorisée)."""
    if not has_stamp:
        return []
    return sorted(set(vals_keys) & PROTECTED_MOVE_FIELDS)


def line_write_violations(vals_keys: Iterable[str], parent_has_stamp: bool) -> list[str]:
    if not parent_has_stamp:
        return []
    return sorted(set(vals_keys) & PROTECTED_LINE_FIELDS)


def may_reset_or_delete(has_stamp: bool) -> bool:
    """Suppression, annulation et retour en brouillon interdits si cachet DGI."""
    return not has_stamp
