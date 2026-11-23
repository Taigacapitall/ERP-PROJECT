---

## MISE À JOUR DE SESSION — COMPTABILITÉ OHADA / ODOO (21 novembre 2026)

**Statut : lot partiel. Adaptateur Python testé ; addon Odoo écrit mais NON exécuté.**

### Entrées manquantes lors de cette session
`account` (Odoo Community), le dépôt Vorpal (contrats du Core) et toute instance Odoo n'étaient pas disponibles. Audit détaillé : `docs/AUDIT_COMPTA_OHADA.md`.

### Livré
- `vorpal/modules/adapters/odoo/` : `client.py` (OdooClient ABC + XmlRpcOdooClient, config par variables `VORPAL_ODOO_*`), `fake.py`, `poster.py`, `currency.py`, `queue.py` (file SQLite, reprise avec backoff), `handlers.py` (4 événements), `mapping.py` + `data/account_mapping.json` (`A_VALIDER`), `module.py`, `dto.py`, `errors.py`.
- `odoo_addons/vorpal_compta_ohada/` : champs `vorpal_source_ref` (index unique par société), `vorpal_dgi_code/qr`, verrouillage, journaux/devises (hook), `ohada/engine.py` + états JSON, `ohada/depreciation.py`, tests Odoo.
- `scripts/install_vorpal_compta.sh` (sans écrasement, refuse `vorpal/core`).

### Tests exécutés (unittest, Python 3.12, sans Odoo) : 46 OK
Adaptateur (idempotence, rejeu, reprise après indisponibilité, USD/CDF, cachet DGI incomplet, paie, paiement), client XML-RPC simulé, mapping contre le vrai plan, protection, états, amortissements.
`ruff`/`mypy --strict` : **non exécutés** (outils absents) à lancer localement. Tests Odoo (`addon/tests`) : **non exécutés**.

### A faire
1. Fournir `vorpal/core` (port `AccountingPoster`, `JournalEntryDTO`, `ExternalServiceError`, EventBus) et aligner `dto.py`/`module.py`.
2. Installer Odoo 19 + `account` ; exécuter les tests addon ; vérifier la saisie `amount_currency` + `debit/credit` en devise étrangère et la création d'écritures `entry` dans un journal de type vente.
3. Valider avec un expert-comptable les points `A_VALIDER` (voir audit §4).
4. Non commencés : rapprochement bancaire, balance âgée, budgets, modèles Odoo d'immobilisations, TAFIRE/DSF, obligations RDC hors TVA, planificateur périodique de `process_pending()`.
