from __future__ import annotations

INVOICE_READY_FOR_CERTIFICATION = "invoice.ready_for_certification"
INVOICE_CERTIFIED = "invoice.certified"
INVOICE_REJECTED = "invoice.rejected"
CREDIT_NOTE_CERTIFIED = "credit_note.certified"
JOURNAL_ENTRY_POSTED = "journal.entry.posted"
PAYROLL_VALIDATED = "payroll.validated"
PAYMENT_RECEIVED = "payment.received"
EVENT_HANDLER_FAILED = "event.handler_failed"

OFFICIAL_EVENTS: tuple[str, ...] = (
    INVOICE_READY_FOR_CERTIFICATION,
    INVOICE_CERTIFIED,
    INVOICE_REJECTED,
    CREDIT_NOTE_CERTIFIED,
    JOURNAL_ENTRY_POSTED,
    PAYROLL_VALIDATED,
    PAYMENT_RECEIVED,
)
