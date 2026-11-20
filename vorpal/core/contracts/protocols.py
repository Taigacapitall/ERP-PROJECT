from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from vorpal.core.contracts.dto import CertifiedInvoiceDTO, InvoiceDTO, JournalEntryDTO
from vorpal.core.money import Currency


@runtime_checkable
class FiscalCertifier(Protocol):
    def certify(self, invoice: InvoiceDTO) -> CertifiedInvoiceDTO:
        """Certifie une facture ou un avoir auprès du dispositif fiscal."""


@runtime_checkable
class AccountingPoster(Protocol):
    def post(self, entry: JournalEntryDTO) -> str:
        """Comptabilise une écriture et retourne son identifiant."""


@runtime_checkable
class ExchangeRateProviderProtocol(Protocol):
    def get_rate(self, from_currency: Currency, to_currency: Currency, on_date: date) -> Decimal:
        """Retourne le taux de change à une date donnée."""
