"""Traduction des événements RÉELS du Core v1 en écritures Odoo.

Contrats consommés (vérifiés dans le dépôt) :
- ``invoice.ready_for_certification`` : ``{"invoice": InvoiceDTO.model_dump(mode="json")}``
  (publié par Ventes). Sert à mémoriser montants/client ; ne comptabilise RIEN.
- ``invoice.certified`` / ``credit_note.certified`` (publiés par DGI) : ``serial_number``,
  ``fiscal_device_id``, ``authentication_code``, ``qr_code``, ``certified_at`` (pas de montants).
  La comptabilisation n'a lieu que lorsque document ET cachet complet sont connus.
- ``invoice.rejected`` : le document en attente est oublié.
- ``payment.received`` (Ventes, payload marqué TODO(A_VALIDER) côté Ventes).
- ``payroll.validated`` : aucun producteur dans le dépôt ; payload = ``PayrollResultDTO`` du Core
  (hypothèse documentée, A_VALIDER).

Le module ne dépend pas de pydantic/Core : les événements sont lus par duck typing
(``type``, ``payload``).
"""
from __future__ import annotations

import calendar
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .dto import ZERO, JournalEntryDTO, JournalLineDTO
from .errors import (
    IncompleteFiscalStampError,
    InvalidPayloadError,
    OdooRejectedError,
    OdooUnavailableError,
)
from .inbox import SqliteFiscalInbox
from .mapping import AccountMapping
from .poster import OdooAccountingPoster
from .queue import DONE, REJECTED, PendingQueue, QueueItem

Publisher = Callable[[str, dict[str, Any]], None]
PartnerResolver = Callable[[str], tuple[str, str | None]]
EV_READY = "invoice.ready_for_certification"
EV_INVOICE = "invoice.certified"
EV_CREDIT = "credit_note.certified"
EV_REJECTED = "invoice.rejected"
EV_PAYMENT = "payment.received"
EV_PAYROLL = "payroll.validated"
EVENTS = (EV_READY, EV_INVOICE, EV_CREDIT, EV_REJECTED, EV_PAYMENT, EV_PAYROLL)


class EventLike(Protocol):
    @property
    def type(self) -> str: ...
    @property
    def payload(self) -> Mapping[str, Any]: ...


def _dec(d: Mapping[str, Any], key: str, *, required: bool = True) -> Decimal:
    v = d.get(key)
    if v is None:
        if required:
            raise InvalidPayloadError(f"champ {key} manquant")
        return ZERO
    if isinstance(v, (float, bool)):
        raise InvalidPayloadError(f"champ {key} : float/bool interdit, utiliser une chaîne")
    try:
        out = Decimal(str(v))
    except InvalidOperation as exc:
        raise InvalidPayloadError(f"champ {key} non numérique") from exc
    if not out.is_finite() or out < 0:
        raise InvalidPayloadError(f"champ {key} négatif ou non fini")
    return out


def _str(d: Mapping[str, Any], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise InvalidPayloadError(f"champ {key} manquant")
    return v.strip()


def _date(d: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(_str(d, key)[:10])
    except ValueError as exc:
        raise InvalidPayloadError(f"champ {key} : date ISO attendue") from exc


def _mapping(v: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(v, Mapping):
        raise InvalidPayloadError(f"{what} absent ou invalide")
    return v


def _line(code: str, *, debit: Decimal = ZERO, credit: Decimal = ZERO, label: str = "",
          partner: tuple[str, str | None] | None = None) -> JournalLineDTO:
    return JournalLineDTO(code, debit, credit, label,
                          partner[0] if partner else None, partner[1] if partner else None)


def _identity_partner(customer_id: str) -> tuple[str, str | None]:
    return customer_id, None


def parse_stamp(payload: Mapping[str, Any]) -> dict[str, str]:
    """Cachet DGI complet obligatoire ; sinon JAMAIS de comptabilisation."""
    out: dict[str, str] = {}
    for key in ("fiscal_device_id", "authentication_code", "qr_code", "certified_at"):
        v = payload.get(key)
        if not isinstance(v, str) or not v.strip():
            raise IncompleteFiscalStampError(f"cachet DGI incomplet ({key}) : pas de comptabilisation")
        out[key] = v.strip()
    try:
        datetime.fromisoformat(out["certified_at"])
    except ValueError as exc:
        raise IncompleteFiscalStampError("cachet DGI : certified_at illisible") from exc
    number = payload.get("serial_number")
    if not isinstance(number, str) or not number.strip():
        raise InvalidPayloadError("champ serial_number manquant")
    out["serial_number"] = number.strip()
    return out


class AccountingEventHandlers:
    def __init__(self, poster: OdooAccountingPoster, queue: PendingQueue,
                 mapping: AccountMapping, inbox: SqliteFiscalInbox | None = None,
                 publish: Publisher | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 partner_resolver: PartnerResolver = _identity_partner) -> None:
        self._poster = poster
        self._queue = queue
        self._mapping = mapping
        self._inbox = inbox or SqliteFiscalInbox()
        self._publish = publish
        self._clock = clock
        self._resolve_partner = partner_resolver

    # -- point d'entrée EventBus (handler unique, s'abonne aux EVENTS)
    def handle(self, event: EventLike) -> None:
        etype, payload = event.type, event.payload
        if etype == EV_READY:
            self.register_document(_mapping(payload.get("invoice"), "section 'invoice'"))
        elif etype in (EV_INVOICE, EV_CREDIT):
            self.on_certified(etype, payload)
        elif etype == EV_REJECTED:
            self._inbox.discard_incomplete(_str(payload, "serial_number"))
        elif etype == EV_PAYMENT:
            self._submit(self._payment(payload))
        elif etype == EV_PAYROLL:
            self._submit(self._payroll(payload))
        else:
            raise InvalidPayloadError(f"événement non pris en charge : {etype}")

    def register_document(self, doc: Mapping[str, Any]) -> None:
        """Mémorise une facture/un avoir (aussi appelable par le workflow avoir DGI, qui
        n'emprunte pas ``invoice.ready_for_certification``)."""
        number = _str(doc, "number")
        if not self._inbox.put_document(number, dict(doc)):
            raise InvalidPayloadError(f"{number} déjà comptabilisé avec un contenu différent")
        self._complete(number)

    def on_certified(self, etype: str, payload: Mapping[str, Any]) -> None:
        stamp = parse_stamp(payload)
        self._inbox.put_stamp(stamp["serial_number"], etype, stamp)
        self._complete(stamp["serial_number"])

    def process_pending(self, limit: int = 100) -> int:
        """À appeler périodiquement : rejoue les écritures en attente. Retourne le nb de succès."""
        ok = 0
        for item in self._queue.due(self._clock(), limit):
            if self._attempt(item):
                ok += 1
        return ok

    # -- corrélation facture + cachet
    def _complete(self, number: str) -> None:
        rec = self._inbox.get(number)
        if rec is None or rec.completed or rec.document is None or rec.stamp is None:
            return
        assert rec.stamp_event is not None
        self._submit(self._sales(rec.document, rec.stamp, rec.stamp_event))
        self._inbox.mark_completed(number)

    def _sales(self, doc: Mapping[str, Any], stamp: Mapping[str, str], event: str
               ) -> JournalEntryDTO:
        credit_note = event == EV_CREDIT
        kind = doc.get("invoice_type", "vente")
        if (kind == "avoir") != credit_note:
            raise InvalidPayloadError(
                f"incohérence : événement {event} pour un document de type {kind!r}")
        if credit_note and not doc.get("original_invoice_ref"):
            raise InvalidPayloadError("avoir sans original_invoice_ref")
        cfg = self._mapping.event(event)
        ht, vat, ttc = _dec(doc, "total_ht"), _dec(doc, "total_vat"), _dec(doc, "total_ttc")
        if ht + vat != ttc:
            raise InvalidPayloadError("total TTC différent de HT + TVA")
        if ttc <= 0:
            raise InvalidPayloadError("montant nul")
        if _dec(doc, "other_taxes", required=False) > 0:
            raise InvalidPayloadError("other_taxes non pris en charge (A_VALIDER)")
        number = _str(doc, "number")
        customer = _mapping(doc.get("customer"), "section 'customer'")
        name = _str(customer, "name")
        nif = customer.get("nif")
        partner = (nif.strip() if isinstance(nif, str) and nif.strip() else f"name:{name}", name)
        sign = {"credit": ttc} if credit_note else {"debit": ttc}
        rev = {"debit": ht} if credit_note else {"credit": ht}
        tax = {"debit": vat} if credit_note else {"credit": vat}
        lines = [_line(cfg["receivable"], partner=partner, label=number, **sign)]
        if ht > 0:
            lines.append(_line(cfg["revenue"]["default"], label=number, **rev))
        if vat > 0:
            lines.append(_line(cfg["vat_collected"], label=number, **tax))
        narration = f"Avoir sur facture {doc['original_invoice_ref']}" if credit_note else ""
        return JournalEntryDTO(
            source_ref=f"{event}:{number}", journal_code=cfg["journal"],
            entry_date=_date(doc, "invoice_date"), currency=_str(doc, "currency"),
            lines=tuple(lines), reference=number, narration=narration,
            exchange_rate=None, dgi_code=stamp["authentication_code"], dgi_qr=stamp["qr_code"])

    def _payment(self, pay: Mapping[str, Any]) -> JournalEntryDTO:
        cfg = self._mapping.event(EV_PAYMENT)
        amount = _dec(pay, "amount")
        if amount <= 0:
            raise InvalidPayloadError("montant nul")
        currency, method = _str(pay, "currency"), _str(pay, "method")
        route = cfg["routes"].get(f"{method}:{currency}")
        if route is None:
            raise InvalidPayloadError(f"aucun journal pour {method}/{currency}")
        pid = _str(pay, "payment_id")
        partner = self._resolve_partner(_str(pay, "customer_id"))
        label = f"Encaissement {pid}"
        return JournalEntryDTO(
            source_ref=f"{EV_PAYMENT}:{pid}", journal_code=route,
            entry_date=_date(pay, "payment_date"), currency=currency, reference=pid,
            lines=(_line(f"journal:{route}", debit=amount, label=label),
                   _line(cfg["receivable"], credit=amount, label=label, partner=partner)))

    def _payroll(self, payload: Mapping[str, Any]) -> JournalEntryDTO:
        cfg = self._mapping.event(EV_PAYROLL)
        p = payload
        for key in ("payroll", "result"):
            if isinstance(payload.get(key), Mapping):
                p = payload[key]
        gross, net = _dec(p, "gross_pay"), _dec(p, "net_pay")
        ded, er = _dec(p, "deductions"), _dec(p, "employer_charges")
        if gross != net + ded:
            raise InvalidPayloadError("paie : brut != net + retenues")
        bd = p.get("breakdown") or {}
        emp_soc = _dec(bd, "employee_social", required=False)
        tax = _dec(bd, "employee_income_tax", required=False)
        if emp_soc + tax != ded:
            raise InvalidPayloadError("paie : breakdown (employee_social + employee_income_tax) "
                                      "ne couvre pas les retenues")
        period, emp = _str(p, "period"), _str(p, "employee_id")
        try:
            year, month = (int(x) for x in period.split("-"))
            end = date(year, month, calendar.monthrange(year, month)[1])
        except (ValueError, calendar.IllegalMonthError) as exc:
            raise InvalidPayloadError("paie : period au format AAAA-MM attendu") from exc
        lbl = f"Paie {period} {emp}"
        lines = [_line(cfg["gross_salary_expense"], debit=gross, label=lbl)]
        if er > 0:
            lines.append(_line(cfg["employer_social_expense"], debit=er, label="Charges patronales"))
        lines.append(_line(cfg["net_pay_due"], credit=net, label="Net à payer"))
        if emp_soc + er > 0:
            lines.append(_line(cfg["social_security_due"], credit=emp_soc + er, label="INSS"))
        if tax > 0:
            lines.append(_line(cfg["income_tax_due"], credit=tax, label="IPR"))
        return JournalEntryDTO(
            source_ref=f"{EV_PAYROLL}:{period}:{emp}", journal_code=cfg["journal"],
            entry_date=end, currency=str(cfg["currency"]), lines=tuple(lines), reference=lbl)

    # -- soumission durable
    def _submit(self, entry: JournalEntryDTO) -> None:
        entry.validate()  # une écriture déséquilibrée n'entre jamais dans la file
        if not self._queue.enqueue(entry):
            known = self._queue.get(entry.source_ref)
            if known is not None and known.entry != entry:
                raise InvalidPayloadError(
                    f"{entry.source_ref} déjà reçu avec un contenu différent")
        item = self._queue.get(entry.source_ref)
        if item is None or item.status in (DONE, REJECTED):
            return
        self._attempt(item)

    def _attempt(self, item: QueueItem) -> bool:
        try:
            result = self._poster.post(item.entry)
        except OdooUnavailableError as exc:
            self._queue.mark_retry(item.source_ref, str(exc), self._clock())
            return False
        except OdooRejectedError as exc:
            self._queue.mark_rejected(item.source_ref, str(exc))
            return False
        self._queue.mark_done(item.source_ref)
        if self._publish is not None:
            self._publish("journal.entry.posted", {
                "source_ref": item.source_ref, "move_id": result.move_id,
                "journal_code": item.entry.journal_code,
                "entry_date": item.entry.entry_date.isoformat(), "created": result.created})
        return True
