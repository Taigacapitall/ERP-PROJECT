"""Ports d'extension (import Excel/CSV factures fournisseurs, futur module Stocks)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImportedSupplierInvoiceTemplate:
    """Structure brute issue d'un fichier modele Excel ou CSV importe par l'organisation."""

    source_filename: str
    file_format: (
        str  # "csv" ou "xlsx" (TODO(A_VALIDER): formats et mapping colonnes par organisation)
    )
    headers: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    linked_purchase_order_number: str | None = None


class SupplierInvoiceTemplateImporter(ABC):
    """Port d'importation de modele de facture fournisseur (CSV / Excel) sans schema fige."""

    @abstractmethod
    def import_template(
        self,
        raw_content: bytes,
        filename: str,
        linked_purchase_order_number: str | None = None,
    ) -> ImportedSupplierInvoiceTemplate:
        """Parse et trace un modele de facture fournisseur importe (TODO(A_VALIDER): mapping colonnes)."""


class StockReceiptNotifier(ABC):
    """Port d'extension optionnel vers le futur module Stocks (degrade proprement si absent)."""

    @abstractmethod
    def notify_receipt_validated(self, payload: dict[str, Any]) -> None:
        """Notifie le module Stocks d'une reception validee."""

    @abstractmethod
    def notify_landed_cost_allocated(self, payload: dict[str, Any]) -> None:
        """Notifie le module Stocks d'une repartition de frais d'approche."""
