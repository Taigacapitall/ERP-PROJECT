"""Port de publication des evenements metier du module Ventes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vorpal.core.contracts import InvoiceDTO
from vorpal.modules.ventes.domain.payments import Payment


class SalesEventPublisher(ABC):
    """Interface de publication post-commit des evenements du module Ventes."""

    @abstractmethod
    def invoice_ready_for_certification(self, invoice: InvoiceDTO) -> None:
        """Publie l'evenement indiquant qu'une facture ou un avoir est pret pour certification."""
        ...

    @abstractmethod
    def payment_received(self, payment: Payment) -> None:
        """Publie l'evenement d'encaissement recu."""
        ...
