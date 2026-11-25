"""Écritures automatiques à partir des événements métier (ventes, DGI, encaissements).

Contrats consommés (vérifiés dans le dépôt) :
- ``invoice.certified`` / ``credit_note.certified`` (DGI) : cachet complet obligatoire + montants.
- ``ventes.invoice.issued`` / ``ventes.credit_note.issued`` (facturation classique, sans DGI).
- ``payment.received`` / ``ventes.payment.cancelled`` (Ventes).
Chaque écriture a pour ``source_ref`` « <événement>:<numéro> » : un événement rejoué ne crée rien
(le grand livre est l'unique garant de l'idempotence). Un handler en erreur est réessayé par le bus
au rejeu (``EventBus.replay``). Le module ne dépend d'aucun autre module métier.
TODO(A_VALIDER) : payloads définitifs ; partner_ref des factures DGI (NIF/nom) différent de celui des
encaissements (identifiant client) tant que l'événement DGI ne porte pas l'identifiant client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from vorpal.core.contracts.events import CREDIT_NOTE_CERTIFIED, INVOICE_CERTIFIED, PAYMENT_RECEIVED
from vorpal.core.events import Event
from vorpal.modules.comptabilite.domain.entries import DraftEntry, DraftLine
from vorpal.modules.comptabilite.errors import AccountingNotFoundError, AccountingValidationError
from vorpal.modules.comptabilite.mapping import AccountMapping
from vorpal.modules.comptabilite.services.common import AccountingDependencies
from vorpal.modules.comptabilite.services.posting import PostingService

CLASSIC_INVOICE_ISSUED = "ventes.invoice.issued"
CLASSIC_CREDIT_NOTE_ISSUED = "ventes.credit_note.issued"
PAYMENT_CANCELLED = "ventes.payment.cancelled"

LISTENED_EVENTS: tuple[str, ...] = (
    INVOICE_CERTIFIED,
    CREDIT_NOTE_CERTIFIED,
    CLASSIC_INVOICE_ISSUED,
    CLASSIC_CREDIT_NOTE_ISSUED,
    PAYMENT_RECEIVED,
    PAYMENT_CANCELLED,
)
ZERO = Decimal("0")


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AccountingValidationError(f"Événement invalide : champ '{key}' absent ou vide.")
    return value.strip()


def _opt_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _amount(payload: Mapping[str, object], key: str, *, required: bool = True) -> Decimal:
    value = payload.get(key)
    if value is None:
        if required:
            raise AccountingValidationError(f"Événement invalide : montant '{key}' absent.")
        return ZERO
    if isinstance(value, (float, bool)):
        raise AccountingValidationError(f"Événement invalide : '{key}' ne doit pas être un float.")
    try:
        out = Decimal(str(value))
    except InvalidOperation as exc:
        raise AccountingValidationError(f"Événement invalide : '{key}' non numérique.") from exc
    if not out.is_finite() or out < 0:
        raise AccountingValidationError(f"Événement invalide : '{key}' négatif ou non fini.")
    return out


def _date(payload: Mapping[str, object], key: str) -> date:
    try:
        return date.fromisoformat(_text(payload, key)[:10])
    except ValueError as exc:
        raise AccountingValidationError(f"Événement invalide : '{key}' doit être une date ISO.") from exc


@dataclass(frozen=True)
class _Sale:
    number: str
    day: date
    currency: str
    ht: Decimal
    vat: Decimal
    ttc: Decimal
    partner_ref: str
    credit_note: bool
    original: str | None
    dgi_code: str | None = None
    dgi_qr: str | None = None


class AccountingEventHandlers:
    def __init__(self, deps: AccountingDependencies, posting: PostingService, mapping: AccountMapping) -> None:
        self._deps = deps
        self._posting = posting
        self._mapping = mapping

    # ------------------------------------------------------------------ ventes
    def on_dgi_certified(self, event: Event) -> None:
        """Facture ou avoir certifié : comptabilisé UNIQUEMENT avec un cachet fiscal complet."""
        p = event.payload
        stamp = {k: _opt_text(p, k) for k in ("fiscal_device_id", "authentication_code", "qr_code", "certified_at")}
        missing = [k for k, v in stamp.items() if v is None]
        if missing:
            raise AccountingValidationError(
                f"Cachet fiscal incomplet ({', '.join(missing)}) : pas de comptabilisation."
            )
        certified_at = stamp["certified_at"]
        assert certified_at is not None
        try:
            datetime.fromisoformat(certified_at)
        except ValueError as exc:
            raise AccountingValidationError("Cachet fiscal : certified_at illisible.") from exc
        credit_note = event.type == CREDIT_NOTE_CERTIFIED
        if bool(p.get("is_credit_note")) != credit_note:
            raise AccountingValidationError(f"Incohérence : événement {event.type} et is_credit_note.")
        if _amount(p, "non_taxable_amount", required=False) > 0 or _amount(p, "other_taxes_amount", required=False) > 0:
            raise AccountingValidationError("Montant non taxable / autres taxes non pris en charge (A_VALIDER).")
        number = _text(p, "serial_number")
        name = _text(p, "customer_name")
        self._post_sale(
            event.type,
            _Sale(
                number=number,
                day=_date(p, "invoice_date"),
                currency=_text(p, "currency"),
                ht=_amount(p, "total_ht"),
                vat=_amount(p, "total_vat"),
                ttc=_amount(p, "total_ttc"),
                partner_ref=_opt_text(p, "customer_nif") or f"name:{name}",
                credit_note=credit_note,
                original=_opt_text(p, "original_invoice_id"),
                dgi_code=stamp["authentication_code"],
                dgi_qr=stamp["qr_code"],
            ),
        )

    def on_classic_document(self, event: Event) -> None:
        """Facture ou avoir de la facturation classique (sans cachet DGI)."""
        p = event.payload
        credit_note = event.type == CLASSIC_CREDIT_NOTE_ISSUED
        if (_text(p, "invoice_type") == "avoir") != credit_note:
            raise AccountingValidationError(f"Incohérence : événement {event.type} et invoice_type.")
        self._post_sale(
            event.type,
            _Sale(
                number=_text(p, "number"),
                day=_date(p, "invoice_date"),
                currency=_text(p, "currency"),
                ht=_amount(p, "total_ht"),
                vat=_amount(p, "total_vat"),
                ttc=_amount(p, "total_ttc"),
                partner_ref=_text(p, "customer_id"),
                credit_note=credit_note,
                original=_opt_text(p, "original_invoice_number"),
            ),
        )

    def _post_sale(self, event_type: str, sale: _Sale) -> None:
        if sale.ht + sale.vat != sale.ttc:
            raise AccountingValidationError(f"{sale.number} : TTC différent de HT + TVA.")
        if sale.ttc <= 0:
            raise AccountingValidationError(f"{sale.number} : montant nul.")
        cfg = self._mapping.sales
        label = f"{'Avoir' if sale.credit_note else 'Facture'} {sale.number}"
        if sale.credit_note and sale.original:
            label += f" sur {sale.original}"

        # facture : client au débit, produit et TVA au crédit ; avoir : l'inverse
        def line(account: str, amount: Decimal, *, receivable: bool) -> DraftLine:
            debit = receivable != sale.credit_note
            return DraftLine(
                account,
                debit=amount if debit else ZERO,
                credit=ZERO if debit else amount,
                label=label,
                partner_ref=sale.partner_ref if receivable else None,
            )

        lines = [line(cfg.receivable, sale.ttc, receivable=True)]
        if sale.ht > 0:
            lines.append(line(cfg.revenue, sale.ht, receivable=False))
        if sale.vat > 0:
            lines.append(line(cfg.vat_collected, sale.vat, receivable=False))
        self._posting.post(
            DraftEntry(
                source_ref=f"{event_type}:{sale.number}",
                journal_code=cfg.journal,
                entry_date=sale.day,
                currency=sale.currency,
                lines=tuple(lines),
                reference=sale.number,
                label=label,
                dgi_code=sale.dgi_code,
                dgi_qr=sale.dgi_qr,
            )
        )

    # ------------------------------------------------------------- encaissements
    def on_payment_received(self, event: Event) -> None:
        p = event.payload
        amount = _amount(p, "amount")
        if amount <= 0:
            raise AccountingValidationError("Encaissement : montant nul.")
        currency, method = _text(p, "currency"), _text(p, "method")
        journal_code = self._mapping.payments.routes.get(f"{method}:{currency}")
        if journal_code is None:
            raise AccountingValidationError(f"Aucun journal de trésorerie pour {method}/{currency}.")
        with self._deps.uow_factory() as uow:
            journal = uow.ledger.get_journal(journal_code)
        if journal is None or journal.default_account is None:
            raise AccountingValidationError(f"Le journal {journal_code} n'a pas de compte de trésorerie par défaut.")
        payment_id = _text(p, "payment_id")
        label = f"Encaissement {payment_id}"
        self._posting.post(
            DraftEntry(
                source_ref=f"{PAYMENT_RECEIVED}:{payment_id}",
                journal_code=journal_code,
                entry_date=_date(p, "payment_date"),
                currency=currency,
                lines=(
                    DraftLine(journal.default_account, debit=amount, label=label),
                    DraftLine(
                        self._mapping.payments.receivable,
                        credit=amount,
                        label=label,
                        partner_ref=_text(p, "customer_id"),
                    ),
                ),
                reference=payment_id,
                label=label,
            )
        )

    def on_payment_cancelled(self, event: Event) -> None:
        """Extourne l'écriture de l'encaissement. Sans écriture d'origine : erreur (réessayée au rejeu)."""
        payment_id = _text(event.payload, "payment_id")
        with self._deps.uow_factory() as uow:
            original = uow.ledger.get_entry_by_source_ref(f"{PAYMENT_RECEIVED}:{payment_id}")
        if original is None:
            raise AccountingNotFoundError(f"Aucune écriture pour l'encaissement {payment_id} : rien à extourner.")
        self._posting.reverse(
            original.id, on_date=event.occurred_at.date(), reason=f"Annulation de l'encaissement {payment_id}"
        )
