"""Adaptateur Odoo pour la comptabilité Vorpal (Port/Adapter).

``module`` et ``core_poster`` dépendent du Core (pydantic/SQLAlchemy) : importés à la demande.
"""
from .client import OdooClient, OdooConfig, XmlRpcOdooClient
from .dto import JournalEntryDTO, JournalLineDTO
from .fake import FakeOdooClient
from .handlers import AccountingEventHandlers
from .inbox import SqliteFiscalInbox
from .mapping import AccountMapping
from .poster import OdooAccountingPoster
from .queue import SqlitePendingQueue

__all__ = [
    "AccountMapping", "AccountingEventHandlers", "FakeOdooClient", "JournalEntryDTO",
    "JournalLineDTO", "OdooAccountingPoster", "OdooClient", "OdooConfig",
    "SqliteFiscalInbox", "SqlitePendingQueue", "XmlRpcOdooClient",
]
