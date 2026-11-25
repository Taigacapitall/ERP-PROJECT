"""Facturation commerciale classique (sans DGI) : emission, paiements, avoirs, annulation.

Ce service est independant de la certification fiscale : il n'emet JAMAIS
`invoice.ready_for_certification`. Une facture emise reste au statut VALIDATED
(libelle « Emise » dans l'interface). Les documents produits sont des factures
commerciales, pas des factures normalisees DGI.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from vorpal.core import Money, User
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.invoices import InvoiceLine, InvoiceStatus, SalesInvoice
from vorpal.modules.ventes.domain.payments import Allocation, Payment, PaymentMethod, PaymentStatus
from vorpal.modules.ventes.ports.unit_of_work import SalesUnitOfWork
from vorpal.modules.ventes.services.common import ApplicationService, PostCommitBatch

PERM_CREATE = "ventes.create"
PERM_CONFIRM = "ventes.confirm"
PERM_ADMIN = "ventes.admin"

_ISSUED = (InvoiceStatus.VALIDATED, InvoiceStatus.CERTIFIED)


class AcceptAnyTaxId:
    """Validateur de NIF permissif : en facturation classique, le client peut ne pas avoir de NIF."""

    def validate(self, nif: str | None) -> bool:
        return True


def credited_total(credit_notes: Sequence[SalesInvoice], invoice: SalesInvoice) -> Money:
    """Somme TTC des avoirs emis sur une facture."""
    total = Money(Decimal("0.00"), invoice.currency)
    for note in credit_notes:
        if note.status in _ISSUED and note.total_ttc is not None:
            total = total + note.total_ttc
    return total


def net_balance_due(invoice: SalesInvoice, credit_notes: Sequence[SalesInvoice]) -> Money:
    """Solde net = TTC - avoirs - encaissements lettres (peut etre negatif : a rembourser)."""
    return invoice.effective_total_ttc - credited_total(credit_notes, invoice) - invoice.effective_paid_amount


class ClassicInvoicingService(ApplicationService):
    """Cas d'usage de la facture commerciale classique."""

    # ------------------------------------------------------------------ factures
    def invoice_from_order(
        self,
        actor: User,
        order_id: str,
        *,
        quantities: dict[str, Decimal] | None = None,
        invoice_date: date | None = None,
        due_date: date | None = None,
    ) -> SalesInvoice:
        """Cree un brouillon de facture depuis une commande confirmee (totale ou partielle)."""
        self._require(actor, PERM_CREATE)
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            order = uow.orders.get(order_id)
            if order is None:
                raise SalesValidationError(f"Commande '{order_id}' introuvable.")
            customer = uow.customers.get(order.customer_id)
            if customer is None:
                raise SalesValidationError(f"Client '{order.customer_id}' introuvable.")
            billed = (
                {line.line_id: line.remaining_quantity for line in order.lines if line.remaining_quantity > 0}
                if quantities is None
                else {k: v for k, v in quantities.items() if v > 0}
            )
            if not billed:
                raise SalesConflictError("Rien a facturer : toutes les quantites sont deja facturees.")
            updated = order.record_invoiced_quantities(billed)  # controle statut et reliquats
            lines = tuple(
                InvoiceLine(
                    item_code=line.item_code,
                    designation=line.designation,
                    quantity=billed[line.line_id],
                    unit_price_ht=line.unit_price_ht,
                    discount_percent=line.discount_percent,
                    tax_group=line.tax_group,
                    vat_rate=line.vat_rate,
                    is_out_of_scope=line.is_out_of_scope,
                    order_line_id=line.line_id,
                )
                for line in order.lines
                if line.line_id in billed
            )
            issue_day = invoice_date or self._today()
            invoice = SalesInvoice(
                id=self._deps.id_factory(),
                customer_id=order.customer_id,
                invoice_date=issue_day,
                due_date=due_date or issue_day + timedelta(days=customer.payment_terms.days),
                currency=order.currency,
                lines=lines,
                order_id=order.id,
            )
            uow.orders.save(updated)
            uow.invoices.save(invoice)
            batch.add_audit(
                user=actor.username, action="INVOICE_CREATED_FROM_ORDER", entity="sales_invoice",
                entity_id=invoice.id, after={"order_id": order.id, "status": invoice.status.value},
            )
            uow.commit()
        batch.emit(self._deps)
        return invoice

    def issue_invoice(self, actor: User, invoice_id: str) -> SalesInvoice:
        """Emet une facture classique : numerote, fige les totaux, sans evenement DGI."""
        self._require(actor, PERM_CONFIRM)
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            invoice = self._get_invoice(uow, invoice_id)
            if invoice.invoice_type != "vente":
                raise SalesComplianceError("Utilisez credit_invoice pour emettre un avoir.")
            customer = uow.customers.get(invoice.customer_id)
            if customer is None:
                raise SalesValidationError(f"Client '{invoice.customer_id}' introuvable.")
            number = invoice.number or uow.numbers.next_number("invoice", invoice.invoice_date.year)
            issued = invoice.validate(
                customer=customer,
                assigned_number=number,
                decimals=self._decimals(invoice.currency),
                tax_id_validator=AcceptAnyTaxId(),
            )
            uow.invoices.save(issued)
            batch.issued_invoices.append((issued, customer))
            batch.add_audit(
                user=actor.username, action="INVOICE_ISSUED", entity="sales_invoice", entity_id=issued.id,
                before={"status": invoice.status.value},
                after={"status": issued.status.value, "number": issued.number, "mode": "classique"},
            )
            uow.commit()
        batch.emit(self._deps)
        return issued

    # ----------------------------------------------------------------- paiements
    def record_payment(
        self,
        actor: User,
        *,
        customer_id: str,
        amount: Money,
        method: PaymentMethod,
        payment_date: date | None = None,
        reference: str | None = None,
        allocations: Sequence[tuple[str, Money]] = (),
    ) -> Payment:
        """Enregistre un encaissement et le lettre (totalement ou partiellement) sur des factures."""
        self._require(actor, PERM_CREATE)
        invoice_ids = [inv_id for inv_id, _ in allocations]
        if len(invoice_ids) != len(set(invoice_ids)):
            raise SalesValidationError("Une facture ne peut apparaitre qu'une fois dans les lettrages d'un paiement.")
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            if uow.customers.get(customer_id) is None:
                raise SalesValidationError(f"Client '{customer_id}' introuvable.")
            if reference and uow.payments.get_by_method_and_reference(method, reference) is not None:
                raise SalesConflictError(f"Un paiement {method.value} de reference '{reference}' existe deja (doublon).")
            day = payment_date or self._today()
            payment = Payment(
                id=self._deps.id_factory(), customer_id=customer_id, amount=amount,
                payment_date=day, method=method, reference=reference,
            )
            for invoice_id, part in allocations:
                payment = self._allocate(uow, payment, invoice_id, part, day)
            uow.payments.save(payment)
            for invoice_id in invoice_ids:
                self._sync_paid(uow, invoice_id)
            batch.received_payments.append(payment)
            batch.add_audit(
                user=actor.username, action="PAYMENT_RECORDED", entity="sales_payment", entity_id=payment.id,
                after={"amount": str(amount.amount), "currency": amount.currency.value,
                       "allocated": str(payment.allocated_amount.amount)},
            )
            uow.commit()
        batch.emit(self._deps)
        return payment

    def allocate_payment(
        self, actor: User, payment_id: str, invoice_id: str, amount: Money, *, allocated_at: date | None = None
    ) -> Payment:
        """Lettre le surplus non affecte d'un paiement (acompte) sur une facture."""
        self._require(actor, PERM_CREATE)
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            payment = uow.payments.get(payment_id)
            if payment is None:
                raise SalesValidationError(f"Paiement '{payment_id}' introuvable.")
            updated = self._allocate(uow, payment, invoice_id, amount, allocated_at or self._today())
            uow.payments.save(updated)
            self._sync_paid(uow, invoice_id)
            batch.add_audit(
                user=actor.username, action="PAYMENT_ALLOCATED", entity="sales_payment", entity_id=payment_id,
                after={"invoice_id": invoice_id, "amount": str(amount.amount)},
            )
            uow.commit()
        batch.emit(self._deps)
        return updated

    def cancel_payment(self, actor: User, payment_id: str) -> Payment:
        """Annule un paiement et libere ses lettrages (les soldes des factures sont recalcules)."""
        self._require(actor, PERM_ADMIN)
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            payment = uow.payments.get(payment_id)
            if payment is None:
                raise SalesValidationError(f"Paiement '{payment_id}' introuvable.")
            cancelled, released = payment.cancel()
            uow.payments.save(cancelled)
            for invoice_id in {alloc.invoice_id for alloc in released}:
                self._sync_paid(uow, invoice_id)
            batch.cancelled_payments.append(cancelled)
            batch.add_audit(
                user=actor.username, action="PAYMENT_CANCELLED", entity="sales_payment", entity_id=payment_id,
                before={"status": PaymentStatus.RECORDED.value}, after={"status": cancelled.status.value},
            )
            uow.commit()
        batch.emit(self._deps)
        return cancelled

    # ------------------------------------------------------------ avoirs, annulation
    def credit_invoice(
        self,
        actor: User,
        invoice_id: str,
        *,
        reason: str,
        lines: Sequence[InvoiceLine] | None = None,
        credit_date: date | None = None,
    ) -> SalesInvoice:
        """Emet un avoir (total si `lines` est omis, partiel sinon) sur une facture emise."""
        self._require(actor, PERM_ADMIN)
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            note = self._credit(uow, actor, batch, invoice_id, reason, lines, credit_date)
            uow.commit()
        batch.emit(self._deps)
        return note

    def cancel_invoice(self, actor: User, invoice_id: str, *, reason: str) -> SalesInvoice:
        """Annule une facture emise par un avoir total. Refuse si elle est payee ou deja creditee."""
        self._require(actor, PERM_ADMIN)
        batch = PostCommitBatch()
        with self._deps.uow_factory() as uow:
            invoice = self._get_invoice(uow, invoice_id)
            if invoice.effective_paid_amount.amount > 0:
                raise SalesConflictError("Facture encaissee : annulez d'abord les paiements lettres.")
            if uow.invoices.list_credit_notes_for_invoice(invoice.id):
                raise SalesConflictError("Cette facture a deja fait l'objet d'un avoir.")
            note = self._credit(uow, actor, batch, invoice_id, reason, None, None)
            uow.commit()
        batch.emit(self._deps)
        return note

    # ------------------------------------------------------------------- interne
    def _get_invoice(self, uow: SalesUnitOfWork, invoice_id: str) -> SalesInvoice:
        invoice = uow.invoices.get(invoice_id)
        if invoice is None:
            raise SalesValidationError(f"Facture '{invoice_id}' introuvable.")
        return invoice

    def _allocate(
        self, uow: SalesUnitOfWork, payment: Payment, invoice_id: str, amount: Money, day: date
    ) -> Payment:
        invoice = self._get_invoice(uow, invoice_id)
        if invoice.invoice_type != "vente" or invoice.status not in _ISSUED:
            raise SalesConflictError("Seule une facture emise peut recevoir un paiement.")
        if invoice.customer_id != payment.customer_id:
            raise SalesConflictError("La facture et le paiement n'appartiennent pas au meme client.")
        net = net_balance_due(invoice, uow.invoices.list_credit_notes_for_invoice(invoice.id))
        allocation = Allocation(
            id=self._deps.id_factory(), payment_id=payment.id, invoice_id=invoice.id, amount=amount, allocated_at=day
        )
        room = net if net.amount > 0 else Money(Decimal("0.00"), net.currency)  # solde net deja diminue des lettrages
        return payment.allocate(allocation, room)

    def _sync_paid(self, uow: SalesUnitOfWork, invoice_id: str) -> None:
        """Recalcule `paid_amount` depuis les lettrages actifs de la facture."""
        invoice = self._get_invoice(uow, invoice_id)
        total = Money(Decimal("0.00"), invoice.currency)
        for payment in uow.payments.list_by_invoice(invoice_id):
            if payment.status != PaymentStatus.RECORDED:
                continue
            for alloc in payment.allocations:
                if alloc.invoice_id == invoice_id:
                    total = total + alloc.amount
        uow.invoices.save(invoice.apply_paid_amount(total))

    def _credit(
        self,
        uow: SalesUnitOfWork,
        actor: User,
        batch: PostCommitBatch,
        invoice_id: str,
        reason: str,
        lines: Sequence[InvoiceLine] | None,
        credit_date: date | None,
    ) -> SalesInvoice:
        if not reason or not reason.strip():
            raise SalesValidationError("Le motif de l'avoir est obligatoire.")
        original = self._get_invoice(uow, invoice_id)
        if original.invoice_type != "vente" or original.status not in _ISSUED:
            raise SalesComplianceError("Un avoir ne peut porter que sur une facture de vente emise.")
        customer = uow.customers.get(original.customer_id)
        if customer is None:
            raise SalesValidationError(f"Client '{original.customer_id}' introuvable.")
        prior_notes = uow.invoices.list_credit_notes_for_invoice(original.id)
        day = credit_date or self._today()
        draft = SalesInvoice(
            id=self._deps.id_factory(),
            customer_id=original.customer_id,
            invoice_date=day,
            due_date=day,
            currency=original.currency,
            lines=tuple(lines) if lines is not None else original.lines,
            invoice_type="avoir",
            original_invoice_id=original.id,
            original_invoice_number=original.number,
        )
        note = draft.validate(
            customer=customer,
            assigned_number=uow.numbers.next_number("credit_note", day.year),
            decimals=self._decimals(original.currency),
            tax_id_validator=AcceptAnyTaxId(),
            original_invoice=original,
            already_credited_ttc=credited_total(prior_notes, original),
            allow_uncertified_original=True,
        )
        uow.invoices.save(note)
        batch.issued_credit_notes.append((note, customer))
        batch.add_audit(
            user=actor.username, action="CREDIT_NOTE_ISSUED", entity="sales_invoice", entity_id=note.id,
            after={"number": note.number, "original": original.number, "reason": reason.strip(),
                   "total_ttc": str(note.total_ttc.amount) if note.total_ttc else None},
        )
        return note
