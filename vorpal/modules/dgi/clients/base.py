"""Interface abstraite (ABC) et modeles d'echange avec le dispositif fiscal (MCF / e-MCF)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from vorpal.modules.dgi.models import FiscalCertificationData, InvoiceDTO


@dataclass(frozen=True)
class DeviceResponse:
    """Reponse retournee par le dispositif fiscal apres soumission ou consultation."""

    accepted: bool
    status_hint: str
    certification: FiscalCertificationData | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None


class FiscalDeviceClient(ABC):
    """Port d'abstraction vers le dispositif fiscal (Mock, HTTP/MCF/e-MCF)."""

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Retourne l'identifiant du dispositif fiscal."""

    @abstractmethod
    def submit(self, invoice: InvoiceDTO) -> DeviceResponse:
        """Soumet une facture ou un avoir au dispositif fiscal."""

    @abstractmethod
    def get_status(self, invoice_serial_or_id: str) -> DeviceResponse:
        """Interroge l'etat d'une soumission aupres du dispositif fiscal."""
