"""Plateforme Vorpal : couche hôte qui assemble les modules (ADR-001).

Squelette de la brique A4. Règle : ce paquet n'importe que ``vorpal.core`` ; les modules métier
sont chargés dynamiquement (ADR-002) et n'importent jamais ce paquet.
"""
