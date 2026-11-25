"""Port de publication des evenements metier du module Ventes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vorpal.core.contracts import InvoiceDTO
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.invoices import SalesInvoice
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

    # Evenements de la facturation classique (sans DGI). Methodes concretes : un publisher qui ne s'y
    # interesse pas n'a rien a implementer.
    def invoice_issued(self, invoice: SalesInvoice, customer: Customer) -> None:  # noqa: B027
        """Publie l'emission d'une facture classique (ni DGI ni cachet fiscal)."""

    def credit_note_issued(self, note: SalesInvoice, customer: Customer) -> None:  # noqa: B027
        """Publie l'emission d'un avoir de la facturation classique."""

    def payment_cancelled(self, payment: Payment) -> None:  # noqa: B027
        """Publie l'annulation d'un encaissement (les ecritures associees doivent etre extournees)."""
