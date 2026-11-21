"""Table declarative de traduction des codes d'erreur DGI / dispositif fiscal vers le francais."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DgiErrorTranslation:
    """Description lisible en francais d'un code d'erreur retourné par le dispositif fiscal."""

    code: str
    message_fr: str
    retryable: bool = False


# TODO(A_VALIDER): remplacer/completer ces codes selon la documentation officielle de la DGI.
DEFAULT_DGI_ERROR_MAP: dict[str, DgiErrorTranslation] = {
    "DGI_INVALID_NIF": DgiErrorTranslation(
        code="DGI_INVALID_NIF",
        message_fr="Le numero d'impot (NIF) du client ou du vendeur est invalide ou absent.",
        retryable=False,
    ),
    "DGI_MISSING_MENTION": DgiErrorTranslation(
        code="DGI_MISSING_MENTION",
        message_fr="Une ou plusieurs mentions obligatoires de la facture normalisee sont manquantes.",
        retryable=False,
    ),
    "DGI_INVALID_TAX_GROUP": DgiErrorTranslation(
        code="DGI_INVALID_TAX_GROUP",
        message_fr="Le groupe de taxation declare ne correspond pas aux regles en vigueur a la date de facture.",
        retryable=False,
    ),
    "DGI_TOTALS_MISMATCH": DgiErrorTranslation(
        code="DGI_TOTALS_MISMATCH",
        message_fr="Incoherence entre les montants HT, taxes, non taxable et total TTC de la facture.",
        retryable=False,
    ),
    "DGI_DUPLICATE_INVOICE": DgiErrorTranslation(
        code="DGI_DUPLICATE_INVOICE",
        message_fr="Cette facture a deja ete traitee par le dispositif fiscal.",
        retryable=False,
    ),
    "DEVICE_UNAVAILABLE": DgiErrorTranslation(
        code="DEVICE_UNAVAILABLE",
        message_fr="Le dispositif fiscal (MCF / e-MCF) est temporairement injoignable (coupure reseau ou service).",
        retryable=True,
    ),
    "DEVICE_TIMEOUT": DgiErrorTranslation(
        code="DEVICE_TIMEOUT",
        message_fr="Delai d'attente depasse lors de la communication avec le dispositif fiscal.",
        retryable=True,
    ),
}


class DgiErrorTranslator:
    """Traducteur extensible des codes d'erreur DGI."""

    def __init__(self, extra_map: dict[str, DgiErrorTranslation] | None = None) -> None:
        self._table: dict[str, DgiErrorTranslation] = dict(DEFAULT_DGI_ERROR_MAP)
        if extra_map:
            for code, entry in extra_map.items():
                self._table[code.upper()] = entry

    def register(self, entry: DgiErrorTranslation) -> None:
        self._table[entry.code.upper()] = entry

    def translate(self, error_code: str | None, fallback_detail: str = "") -> str:
        if not error_code:
            return fallback_detail.strip() or "Erreur non specifiee du dispositif fiscal."
        normalized = error_code.strip().upper()
        entry = self._table.get(normalized)
        if entry is None:
            detail_part = f" ({fallback_detail.strip()})" if fallback_detail.strip() else ""
            # TODO(A_VALIDER): enrichir avec la table officielle des codes rejet DGI.
            return f"Erreur fiscale code '{normalized}'{detail_part}."
        if fallback_detail.strip():
            return f"{entry.message_fr} [{fallback_detail.strip()}]"
        return entry.message_fr

    def is_retryable(self, error_code: str | None) -> bool:
        if not error_code:
            return False
        entry = self._table.get(error_code.strip().upper())
        return entry.retryable if entry is not None else False
