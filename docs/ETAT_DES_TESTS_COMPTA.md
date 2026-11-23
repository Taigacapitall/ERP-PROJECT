# État des tests — comptabilité Odoo (21 novembre 2026)
Environnement : Python 3.12.3, stdlib uniquement (pas de pydantic, SQLAlchemy, pytest, ruff, mypy ; pas de réseau).

## A. Validations locales / simulateur (FakeOdooClient, XML-RPC simulé) — EXÉCUTÉES
| Commande | Résultat |
|---|---|
| `python3 -m unittest discover -s tests/adapters/odoo -t . -p "test_[px]*.py"` | `Ran 42 tests … OK (skipped=1)` |
| `python3 -m unittest discover -s tests/odoo_addons -t .` | `Ran 14 tests … OK (skipped=1)` |
| `python3 -m compileall -q vorpal/modules/adapters tests/adapters odoo_addons` | OK |
| SHA-256 agrégé de `vorpal/core/**/*.py` avant/après | identique (`0a68be2e…0137`) |
Ignorés : comparaison au plan CSV `l10n_syscohada` (variable `VORPAL_SYSCOHADA_CSV`, fichier non fourni).
Couvre : cachet DGI complet obligatoire, corrélation facture/cachet dans les deux ordres, rejet DGI, avoir, idempotence/rejeu, contenu divergent refusé, USD/CDF et arrondi, taux absent, reprise après indisponibilité, brouillon repris, file persistante, course de doublon, paiement, paie, pont `CoreAccountingPoster` (stand-in duck-typé).

## B. Écrits mais NON exécutés
- `tests/adapters/odoo/test_core_contracts.py` (vrais Event/EventBus/DTO/Protocole Core) : `unittest` l'importe → échec d'import pytest ici ; nécessite pydantic + pytest.
- Suite existante du dépôt (core, dgi, ventes, achats, intégration) : non lancée, **non-régression non vérifiée** (aucun fichier existant modifié hors `pyproject.toml`, une ligne).
- ruff, mypy : non exécutés.

## C. Odoo réel
**Aucune validation.** `odoo_addons/vorpal_compta_ohada/tests/` (TransactionCase) non exécutés.
