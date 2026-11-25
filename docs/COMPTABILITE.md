# Comptabilité OHADA — état au 24 novembre 2026

Document unique remplaçant `HANDOFF_SECTION_COMPTA.md`, `ETAT_DES_TESTS_COMPTA.md` et `INTEGRATION_COMPTA_RAPPORT.md` (supprimés ; leur historique reste dans git). Audit détaillé : `AUDIT_COMPTA_OHADA.md`. Installation : `README_ADAPTATEUR_ODOO.md`.

## Comptabilité native (voie retenue — E16)
Produit unique, sans Odoo : `vorpal/modules/comptabilite/` (tables `compta_*`, migration additive `SchemaMigrationManager("comptabilite")`).
- **Domaine** : comptes (classes OHADA), journaux (compte de trésorerie par défaut), exercices, écritures immuables (extourne au lieu de modification), conversion USD/CDF avec absorption de l'écart d'arrondi (≤ 0,05).
- **Comptabilisation** (`PostingService`) : idempotente par `source_ref` (même référence + contenu différent = conflit), numérotation sans trou par journal et exercice (`VTE-2026-000001`), refus hors exercice ouvert ou période verrouillée, journal en devise imposée, montants stockés en centimes (sommes SQL exactes).
- **États** (`ReportService`) : balance générale (ouverture / mouvements / clôture), grand livre avec solde cumulé, bilan et compte de résultat via les définitions OHADA JSON (reprises de l'addon, copiées sans le modifier).
- **Clôture** (`ClosingService`) : solde 6-8 vers 131/139, clôt et verrouille ; idempotente ; refuse de clôturer dans le désordre. Les écritures de clôture sont exclues des mouvements de l'exercice (le compte de résultat reste lisible) mais comptent dans les soldes de bilan.
- **Port Core** : `CoreLedgerPoster` implémente `AccountingPoster` (mono-devise, codes courts résolus par préfixe). À ne pas enregistrer avec `comptabilite_odoo` (même service).
- **Permissions** : `compta.view`, `compta.post`, `compta.admin`.
- **Plan** : `data/plan_syscohada_base.json` (154 comptes, niveaux 2 à 4 chiffres, **A_VALIDER**). Plan complet : `ChartService.import_csv(<account.account-syscohada.csv d'Odoo>)`.
- **Tests** : `tests/comptabilite/` (43), SQLite mémoire et base fichier avec redémarrage ; scénario chiffré à la main (résultat 1 500, bilan 12 300 / 12 300).

### Limites de la comptabilité native
- **Non branchée** aux événements : la facturation classique n'émet pas d'événement « facture émise » ; seuls `payment.received` (Ventes) et `invoice.certified` (DGI) existent. Achats et Paie n'émettent rien d'exploitable.
- Pas de TVA automatique, rapprochement bancaire, balance âgée, immobilisations (le moteur d'amortissement reste dans l'addon), budgets, TAFIRE/DSF, export PDF/CSV des états.
- Plan, comptes 131/139, journal de clôture, règle d'arrondi, présentation du bilan (par préfixe) : **A_VALIDER** par un expert-comptable OHADA/DGI. Ne vaut pas liasse officielle.
- Écriture multi-devises mono-taux ; taux fourni par l'appelant ou par un `ExchangeRateProviderProtocol` injecté (aucun taux inventé).

## Livré (adaptateur Odoo, voie alternative)
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
