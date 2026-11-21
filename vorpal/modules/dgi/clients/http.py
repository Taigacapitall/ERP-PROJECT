"""Client HTTP configurable pour dispositif fiscal DGI (MCF / e-MCF)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from vorpal.core.errors import ComplianceError
from vorpal.modules.dgi.clients.base import DeviceResponse, FiscalDeviceClient
from vorpal.modules.dgi.models import FiscalCertificationData, InvoiceDTO


@dataclass(frozen=True)
class HttpMCFConfig:
    """Configuration injectable des endpoints, jetons et delais du client HTTP DGI."""

    base_url: str
    submit_path: str = "/api/v1/invoices/certify"  # TODO(A_VALIDER): endpoint officiel DGI e-MCF / MCF
    status_path_template: str = "/api/v1/invoices/{invoice_ref}/status"  # TODO(A_VALIDER): endpoint statut DGI
    device_id: str = "MCF-HTTP-CONFIGURED"  # TODO(A_VALIDER): identifiant officiel du dispositif fiscal
    bearer_token: str | None = None
    timeout_seconds: float = 10.0

    def build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # TODO(A_VALIDER): schema d'authentification officiel (Bearer, API Key, signature HMAC ou certificat).
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers


class HttpMCFClient(FiscalDeviceClient):
    """Client HTTP base sur httpx, sans secret en dur et avec format payload a valider."""

    def __init__(
        self,
        config: HttpMCFConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not config.base_url.strip():
            raise ComplianceError("L'URL de base (base_url) de HttpMCFClient est obligatoire.")
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_seconds,
            headers=config.build_headers(),
            transport=transport,
        )

    @property
    def device_id(self) -> str:
        return self._config.device_id

    def _serialize_invoice(self, invoice: InvoiceDTO) -> dict[str, Any]:
        # TODO(A_VALIDER): adapter le schema JSON exact aux specifications de la plateforme developpeurs DGI.
        return {
            "device_id": self._config.device_id,
            "invoice_id": invoice.invoice_id,
            "serial_number": invoice.serial_number,
            "issue_date": invoice.issue_date.isoformat(),
            "currency": invoice.currency.value,
            "is_credit_note": invoice.is_credit_note,
            "original_invoice_id": invoice.original_invoice_id,
            "seller": {
                "name": invoice.seller.name,
                "tax_id": invoice.seller.tax_id,
                "rccm": invoice.seller.rccm,
                "address": invoice.seller.address,
            },
            "customer": {
                "name": invoice.customer.name,
                "tax_id": invoice.customer.tax_id,
                "rccm": invoice.customer.rccm,
                "address": invoice.customer.address,
            },
            "totals": {
                "total_ht": str(invoice.total_ht.amount),
                "total_vat": str(invoice.total_vat.amount),
                "non_taxable_amount": str(invoice.non_taxable_amount.amount),
                "other_taxes_amount": str(invoice.other_taxes_amount.amount),
                "total_ttc": str(invoice.total_ttc.amount),
            },
            "lines": [
                {
                    "line_id": ln.line_id,
                    "item_code": ln.item_code,
                    "designation": ln.designation,
                    "quantity": str(ln.quantity),
                    "unit_price_ht": str(ln.unit_price_ht.amount),
                    "line_total_ht": str(ln.line_total_ht.amount),
                    "vat_rate": str(ln.vat_rate),
                    "vat_amount": str(ln.vat_amount.amount),
                    "line_total_ttc": str(ln.line_total_ttc.amount),
                    "tax_group_code": ln.tax_group_code,
                    "out_of_scope": ln.out_of_scope,
                }
                for ln in invoice.lines
            ],
        }

    def submit(self, invoice: InvoiceDTO) -> DeviceResponse:
        payload = self._serialize_invoice(invoice)
        try:
            response = self._client.post(self._config.submit_path, json=payload)
        except httpx.TimeoutException as exc:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_TIMEOUT",
                error_message=str(exc),
                raw_request=payload,
            )
        except httpx.HTTPError as exc:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_UNAVAILABLE",
                error_message=str(exc),
                raw_request=payload,
            )

        data: dict[str, Any] = response.json() if response.content else {}
        if response.status_code >= 500:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code=str(data.get("error_code") or "DEVICE_UNAVAILABLE"),
                error_message=str(data.get("message") or f"Erreur HTTP {response.status_code}"),
                raw_request=payload,
                raw_response=data,
            )

        # TODO(A_VALIDER): mapper les champs de reponse (code authentification, QR, identifiant dispositif, codes rejet).
        if response.is_success and bool(data.get("certified", data.get("accepted", False))):
            cert = FiscalCertificationData(
                fiscal_device_id=str(data.get("fiscal_device_id") or self._config.device_id),
                authentication_code=str(data.get("authentication_code", "")),
                qr_code=str(data.get("qr_code", "")),
                certified_at=data.get("certified_at"),
            )
            return DeviceResponse(
                accepted=True,
                status_hint="CERTIFIED",
                certification=cert,
                raw_request=payload,
                raw_response=data,
            )

        return DeviceResponse(
            accepted=False,
            status_hint="REJECTED",
            error_code=str(data.get("error_code") or f"HTTP_{response.status_code}"),
            error_message=str(data.get("message") or "Rejet par le dispositif fiscal."),
            raw_request=payload,
            raw_response=data,
        )

    def get_status(self, invoice_serial_or_id: str) -> DeviceResponse:
        path = self._config.status_path_template.format(invoice_ref=invoice_serial_or_id)
        try:
            response = self._client.get(path)
        except httpx.TimeoutException as exc:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_TIMEOUT",
                error_message=str(exc),
            )
        except httpx.HTTPError as exc:
            return DeviceResponse(
                accepted=False,
                status_hint="RETRY",
                error_code="DEVICE_UNAVAILABLE",
                error_message=str(exc),
            )

        data: dict[str, Any] = response.json() if response.content else {}
        if response.is_success and bool(data.get("certified", False)):
            cert = FiscalCertificationData(
                fiscal_device_id=str(data.get("fiscal_device_id") or self._config.device_id),
                authentication_code=str(data.get("authentication_code", "")),
                qr_code=str(data.get("qr_code", "")),
                certified_at=data.get("certified_at"),
            )
            return DeviceResponse(
                accepted=True,
                status_hint="CERTIFIED",
                certification=cert,
                raw_response=data,
            )

        return DeviceResponse(
            accepted=False,
            status_hint=str(data.get("status") or "PENDING"),
            error_code=data.get("error_code"),
            error_message=str(data.get("message") or ""),
            raw_response=data,
        )

    def close(self) -> None:
        self._client.close()

