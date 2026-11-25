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

### Écritures automatiques (événements)
`events.py` + `mapping.py` + `data/account_mapping.json` (**A_VALIDER**). Idempotence portée par le grand livre (`source_ref` = « <événement>:<numéro> ») ; un handler en erreur est réessayé par `EventBus.replay()` (test : exercice manquant, puis création, puis rejeu).
| Événement | Producteur | Écriture |
|---|---|---|
| `invoice.certified` / `credit_note.certified` | DGI (payload enrichi : montants, client, date) | Client 4111 / produit 7011 / TVA 4431, **uniquement avec cachet complet** ; `dgi_code` et `dgi_qr` conservés sur l'écriture |
| `ventes.invoice.issued` / `ventes.credit_note.issued` | Ventes (facturation classique, sans DGI) | idem, sans cachet ; avoir = sens inverse |
| `payment.received` | Ventes | Compte de trésorerie du journal (route méthode:devise) / 4111 ; CDF converti au taux du fournisseur de taux injecté |
| `ventes.payment.cancelled` | Ventes | Extourne de l'écriture de l'encaissement |
Ces trois derniers événements Ventes sont nouveaux (publisher : méthodes concrètes, les doubles de test existants restent valides). `comptabilite` et `comptabilite_odoo` s'abonnent aux mêmes événements : **ne jamais enregistrer les deux**.

### Limites de la comptabilité native
- **Achats et Paie non branchés** : les événements Achats n'ont pas de montants comptabilisables validés ; aucun producteur `payroll.validated`.
- **Partenaire** : les écritures de facture DGI portent NIF ou nom du client, celles de la facturation classique et des encaissements l'identifiant client. Le Core ne porte pas l'identifiant client sur l'événement DGI : une balance âgée par client exige de l'unifier (à faire avant d'écrire la balance âgée).
- Un seul compte de produit et de TVA (pas de ventilation par groupe de taxe, ni TVA exonérée distincte) ; montant non taxable et autres taxes refusés (A_VALIDER).
- Taux de change : requis pour toute écriture hors devise société (aucun taux inventé). Facture en CDF sans taux déclaré : refusée et réessayée au rejeu.
- Pas de TVA à déclarer, rapprochement bancaire, balance âgée, immobilisations (moteur d'amortissement resté dans l'addon), budgets, TAFIRE/DSF, export PDF/CSV des états.
- Plan, comptes 131/139, journal de clôture, règle d'arrondi, présentation du bilan (par préfixe), comptes de la table de correspondance : **A_VALIDER** par un expert-comptable OHADA/DGI. Ne vaut pas liasse officielle.
- Écriture multi-devises mono-taux.

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
