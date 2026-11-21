"""Simulateur MockMCFClient pour developpement, tests et homologation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from vorpal.modules.dgi.clients.base import DeviceResponse, FiscalDeviceClient
from vorpal.modules.dgi.models import FiscalCertificationData, InvoiceDTO


@dataclass
class MockMCFClient(FiscalDeviceClient):
    """Simulateur configurable de dispositif fiscal (certification, rejet, coupure reseau)."""

    _device_id: str = "MCF-MOCK-001"
    unavailable: bool = False
    fail_remaining_calls: int = 0
    reject_serials: set[str] = field(default_factory=set)
    reject_error_code: str = "DGI_INVALID_NIF"
    reject_error_message: str = "Rejet simule par MockMCFClient."
    clock: Callable[[], datetime] | None = None
    _submitted_by_key: dict[str, DeviceResponse] = field(default_factory=dict, init=False)

    @property
    def device_id(self) -> str:
        return self._device_id

    def simulate_outage(self, enabled: bool = True, fail_calls: int = 0) -> None:
        """Active ou desactive une coupure simulée du dispositif fiscal."""
        self.unavailable = enabled
        self.fail_remaining_calls = max(0, fail_calls)

    def submit(self, invoice: InvoiceDTO) -> DeviceResponse:
        request_payload = {
            "device_id": self._device_id,
            "invoice_id": invoice.invoice_id,
            "serial_number": invoice.serial_number,
            "issue_date": invoice.issue_date.isoformat(),
            "currency": invoice.currency.value,
            "total_ttc": str(invoice.total_ttc.amount),
            "is_credit_note": invoice.is_credit_note,
        }

        if self.unavailable or self.fail_remaining_calls > 0:
            if self.fail_remaining_calls > 0:
                self.fail_remaining_calls -= 1
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_UNAVAILABLE",
                error_message="Dispositif fiscal temporairement indisponible (simulation coupure).",
                raw_request=request_payload,
                raw_response={"simulated_outage": True},
            )

        if invoice.serial_number in self.reject_serials or invoice.invoice_id in self.reject_serials:
            return DeviceResponse(
                accepted=False,
                status_hint="REJECTED",
                error_code=self.reject_error_code,
                error_message=self.reject_error_message,
                raw_request=request_payload,
                raw_response={"rejected": True, "code": self.reject_error_code},
            )

        if invoice.serial_number in self._submitted_by_key:
            return self._submitted_by_key[invoice.serial_number]

        digest = hashlib.sha256(
            f"{self._device_id}:{invoice.serial_number}:{invoice.total_ttc.amount}".encode("utf-8")
        ).hexdigest().upper()
        auth_code = f"AUTH-{digest[:12]}"
        qr_payload = f"DGI-RDC|DEV={self._device_id}|SN={invoice.serial_number}|TTC={invoice.total_ttc.amount}|AUTH={auth_code}"
        certified_at = (
            self.clock().isoformat() if self.clock is not None else datetime.now(timezone.utc).isoformat()
        )

        cert = FiscalCertificationData(
            fiscal_device_id=self._device_id,
            authentication_code=auth_code,
            qr_code=qr_payload,
            certified_at=certified_at,
        )
        response = DeviceResponse(
            accepted=True,
            status_hint="CERTIFIED",
            certification=cert,
            raw_request=request_payload,
            raw_response={
                "certified": True,
                "fiscal_device_id": cert.fiscal_device_id,
                "authentication_code": cert.authentication_code,
                "qr_code": cert.qr_code,
            },
        )
        self._submitted_by_key[invoice.serial_number] = response
        return response

    def get_status(self, invoice_serial_or_id: str) -> DeviceResponse:
        if self.unavailable:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_UNAVAILABLE",
                error_message="Dispositif fiscal injoignable.",
            )
        cached = self._submitted_by_key.get(invoice_serial_or_id)
        if cached is not None:
            return cached
        return DeviceResponse(
            accepted=False,
            status_hint="PENDING",
            error_code=None,
            error_message="Aucune soumission enregistree pour cet identifiant.",
        )
