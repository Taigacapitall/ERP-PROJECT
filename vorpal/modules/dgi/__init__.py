"""Module Conformite DGI (vorpal.modules.dgi)."""

from vorpal.modules.dgi.clients.base import DeviceResponse, FiscalDeviceClient
from vorpal.modules.dgi.clients.http import HttpMCFClient, HttpMCFConfig
from vorpal.modules.dgi.clients.mock import MockMCFClient
from vorpal.modules.dgi.credit_note import CreditNoteRequest
from vorpal.modules.dgi.errors_map import (
    DEFAULT_DGI_ERROR_MAP,
    DgiErrorTranslation,
    DgiErrorTranslator,
)
from vorpal.modules.dgi.journal import FiscalJournal, FiscalJournalEntry
from vorpal.modules.dgi.manifest import (
    DGI_EVENTS_EMITTED,
    DGI_EVENTS_LISTENED,
    DGI_MANIFEST,
    DGI_PERMISSIONS,
)
from vorpal.modules.dgi.models import (
    MANDATORY_MENTIONS_KEYS,
    CertificationStatus,
    FiscalCertificationData,
    InvoiceDTO,
    InvoiceLineDTO,
    PartyTaxInfo,
    ensure_invoice_valid_for_submission,
    strict_decimal,
    validate_mandatory_mentions,
)
from vorpal.modules.dgi.outbox import OutboxEntry, OutboxRepository
from vorpal.modules.dgi.renderers import HtmlRenderer, InvoiceRenderer, TextRenderer
from vorpal.modules.dgi.retry import RetryPolicy
from vorpal.modules.dgi.service import CertificationService
from vorpal.modules.dgi.state_machine import (
    ensure_not_certified_immutable,
    transition_invoice_status,
)
from vorpal.modules.dgi.tax_groups import TaxGroup, TaxGroupTable, TaxMapping

__all__ = [
    "DEFAULT_DGI_ERROR_MAP",
    "DGI_EVENTS_EMITTED",
    "DGI_EVENTS_LISTENED",
    "DGI_MANIFEST",
    "DGI_PERMISSIONS",
    "MANDATORY_MENTIONS_KEYS",
    "CertificationService",
    "CertificationStatus",
    "CreditNoteRequest",
    "DeviceResponse",
    "DgiErrorTranslation",
    "DgiErrorTranslator",
    "FiscalCertificationData",
    "FiscalDeviceClient",
    "FiscalJournal",
    "FiscalJournalEntry",
    "HtmlRenderer",
    "HttpMCFClient",
    "HttpMCFConfig",
    "InvoiceDTO",
    "InvoiceLineDTO",
    "InvoiceRenderer",
    "MockMCFClient",
    "OutboxEntry",
    "OutboxRepository",
    "PartyTaxInfo",
    "RetryPolicy",
    "TaxGroup",
    "TaxGroupTable",
    "TaxMapping",
    "TextRenderer",
    "ensure_invoice_valid_for_submission",
    "ensure_not_certified_immutable",
    "strict_decimal",
    "transition_invoice_status",
    "validate_mandatory_mentions",
]
