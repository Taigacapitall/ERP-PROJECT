# Comptabilité OHADA / Odoo — état au 24 novembre 2026

Document unique remplaçant `HANDOFF_SECTION_COMPTA.md`, `ETAT_DES_TESTS_COMPTA.md` et `INTEGRATION_COMPTA_RAPPORT.md` (supprimés ; leur historique reste dans git). Audit détaillé : `AUDIT_COMPTA_OHADA.md`. Installation : `README_ADAPTATEUR_ODOO.md`.

## Livré
- `vorpal/modules/adapters/odoo/` : client Odoo (ABC, XML-RPC, simulateur `FakeOdooClient`), poster, conversion de devises, file d'attente SQLite avec reprise (backoff), handlers d'événements, boîte de corrélation facture/cachet DGI, mapping des comptes, module `BaseModule`.
- `odoo_addons/vorpal_compta_ohada/` : champs `vorpal_source_ref` (index unique par société), cachet DGI, verrouillage, journaux/devises, moteur d'états OHADA (JSON), amortissements.
- `scripts/install_vorpal_compta.sh` : installation sans écrasement, refuse de toucher `vorpal/core`.

## Tests (exécutés le 24/11/2026 avec la suite complète : 719 réussis, 2 ignorés)
- Adaptateur Odoo : 44 tests ; addon (logique pure) : 13 tests ; contrats Core réels (`test_core_contracts.py`) inclus.
- Ignorés (2) : comparaison au plan `l10n_syscohada` — fichier CSV non fourni (`VORPAL_SYSCOHADA_CSV`).
- Ruff : 6 erreurs dans l'adaptateur (`client.py`, `mapping.py`, `module.py`) ; Mypy strict : 3 erreurs (`handlers.py`, `inbox.py`) — à corriger sans relâcher les règles.

## Non validé
- **Aucun Odoo réel n'a été exercé** : les tests `TransactionCase` de l'addon n'ont jamais tourné ; la saisie en devise étrangère (`amount_currency` + débit/crédit) reste à vérifier sur Odoo 19 avec le module `account`.
- Points `A_VALIDER` à confirmer avec un expert-comptable (voir `AUDIT_COMPTA_OHADA.md` §4).
- Le circuit Achats → Comptabilité n'est pas démontré ; le circuit Ventes (facture, cachet DGI, paiement) → Comptabilité n'est testé qu'avec le simulateur.

## À faire
1. Installer Odoo 19 + `account`, exécuter les tests addon, valider les écritures en USD/CDF.
2. Décider : conserver Odoo (lourd à installer chez un client) ou écrire un grand livre léger natif (E16 de `HANDOFF.md`).
3. Corriger les 9 erreurs Ruff/Mypy de l'adaptateur.
4. Non commencés : rapprochement bancaire, balance âgée, budgets, immobilisations Odoo, TAFIRE/DSF, obligations RDC hors TVA, planificateur périodique de `process_pending()`.
