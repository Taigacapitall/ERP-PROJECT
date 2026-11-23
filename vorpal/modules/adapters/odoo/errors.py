"""Erreurs de l'adaptateur Odoo.

Deux familles : transitoires (on réessaie) et permanentes (on ne réessaie pas).
Le pont vers ``ExternalServiceError`` du Core est à brancher côté module (voir README).
"""
from __future__ import annotations


class OdooAdapterError(Exception):
    """Racine des erreurs de l'adaptateur."""


class OdooUnavailableError(OdooAdapterError):
    """Odoo injoignable ou erreur transitoire : l'écriture doit être reprise plus tard."""


class OdooRejectedError(OdooAdapterError):
    """Refus définitif : réessayer ne servira à rien sans correction."""


class DuplicateSourceRefError(OdooRejectedError):
    """Une écriture avec la même ``vorpal_source_ref`` existe déjà (course entre deux envois)."""


class UnbalancedEntryError(OdooRejectedError):
    """Écriture non équilibrée."""


class MappingError(OdooRejectedError):
    """Compte, journal, taux ou correspondance introuvable."""


class InvalidPayloadError(OdooRejectedError):
    """Payload d'événement incomplet ou incohérent."""


class IncompleteFiscalStampError(InvalidPayloadError):
    """Facture/avoir sans cachet DGI complet : jamais comptabilisé comme certifié."""
