# Audit comptabilité OHADA — Phase 1 (21 novembre 2026)

## 0. Périmètre réellement audité (limites)
| Source | Fournie | Audit |
|---|---|---|
| `l10n_syscohada` (zip) | oui | code + CSV lus et mesurés |
| `l10n_cd` (zip) | oui | code + CSV + rapport TVA lus |
| `account` (Odoo Community) | **non** | **non audité** : ce qui le concerne ci-dessous vient de la connaissance générale d'Odoo, marqué « à vérifier » |
| Modules Enterprise, OCA | non | analyse de principe uniquement, aucun code lu ni copié |
| Dépôt Vorpal (`vorpal/core`, EventBus, `AccountingPoster`, `JournalEntryDTO`) | **non** (seul `HANDOFF.md`) | contrats du Core **non vus** → DTO/payloads de l'adaptateur sont provisoires (`A_VALIDER`) |

Aucune instance Odoo, pas de réseau, ni pytest/ruff/mypy dans l'environnement de travail : les tests Odoo sont écrits mais non exécutés.

## 1. Constats mesurés (fichiers fournis)
- **Plan SYSCOHADA révisé complet** : 1 134 comptes, classes 1–9 (123/245/61/216/57/214/101/45/72), 0 doublon de code, 57 comptes lettrables (40x, 41x, 421–423, 4441, 4445, 485x, 488…), plan associations SYSCEBNL en plus. → **ne pas recréer le plan**.
- **Codes sur 6 chiffres** (`code_digits=6`) : « 4111 » devient « 411100 ». L'adaptateur résout donc par préfixe.
- **Comptes 52x (banques) absents du CSV** : générés à la création des journaux (préfixes banque 521, caisse 571, virements 585).
- **Écarts de change** déjà branchés : gain 776, perte 676. Escomptes 6019/7019.
- **l10n_cd** = fiscalité TVA uniquement : 13 taxes (9 actives) à 16 % / 0 %, positions fiscales National/International, groupes TVA 0 % et 16 %, **déclaration TVA RDC** (rapport déclaratif, 98 enregistrements, tags `CD_*`).
- **Aucune** taxe hors TVA (IBP, IPR, retenues à la source), **aucun** journal défini (hérités du modèle Odoo générique), **aucun** état OHADA (bilan, compte de résultat, TAFIRE), **aucune** immobilisation, **aucune** logique paie.
- Menu « Syscohada » sous Rapports déclaré **vide** : il est prévu pour être rempli par le module Enterprise de rapports.
- Points de typage à valider : 4451/4452 (TVA récupérable) typés `liability_current`; 4711 « débiteurs divers » typé passif.
- Les rapports `account.report` livrés (TVA) sont des **définitions**; leur moteur d'affichage est (à vérifier) côté Enterprise → un moteur de rendu propre est nécessaire.

## 2. Inventaire fonctionnel
Légende : **1** déjà dans Community · **2** présent, à adapter · **3** absent, à développer · **4** équivalent Enterprise à reproduire indépendamment · **5** à valider SYSCOHADA/RDC.
« (V) » = vérifié dans les fichiers fournis ; « (?) » = non vérifié, `account` non fourni.

| Fonction | Cat. | Statut / décision |
|---|---|---|
| Plan SYSCOHADA | 1 (V) | Existant complet. Rien à recréer. |
| Plan SYSCOHADA RDC (6 chiffres, TVA) | 1+5 (V) | `l10n_cd`. Valider typage 4451/4452/4711. |
| Écritures en partie double | 1 (?) | Natif Odoo. |
| Journaux | 2 (V) | **Livré** : VTE, ACH, OD, PAIE, IMMO, caisses/banques USD et CDF (hook idempotent). |
| Séquences sans trou par exercice | 1+5 (?) | Séquence native par journal/année ; valider l'absence de trou après annulations. |
| Factures clients/fournisseurs | 1 (?) | Natif. Vorpal reste maître des factures ; Odoo reçoit des écritures. |
| Paiements, rapprochement manuel | 1 (?) | Natif. |
| Rapprochement bancaire assisté (import, règles) | 4 | **Non fait** (lot suivant). |
| Banques/caisses | 2 | Journaux USD/CDF livrés. |
| Multi-devises USD/CDF, taux modifiables | 1+2 (?) | Natif ; hook active USD/CDF ; devise société USD configurable. **Livré** : conversion adaptateur testée. |
| Écarts de change | 1 (V) | 776/676 branchés. |
| Grand livre, balance | 1/4 (?) | À vérifier : rapports d'affichage complets souvent Enterprise. Balance SQL fournie (`vorpal.ohada.report.balances`). |
| Balance âgée | 4 (?) | **Non fait**. |
| Bilan, compte de résultat | 3+4+5 | **Livré (squelette)** : moteur + 2 définitions JSON, couverture du plan vérifiée. **A valider expert-comptable**. TAFIRE / liasse DSF : non fait. |
| Immobilisations, amortissements | 4 | **Livré (moteur)** : plans linéaire/dégressif en Decimal, testés. Modèles Odoo + écritures auto : **non faits**. |
| Clôtures, verrouillage de période | 1 (?) | Dates de verrouillage natives. Verrou facture certifiée DGI **livré**. |
| Hash d'inaltérabilité | 1 (?) | A vérifier sur l'instance ; non activé. |
| Budgets | 4 | **Non fait**. |
| Analytique | 1 (?) | Natif (comptes analytiques). A cadrer. |
| Déclaration TVA RDC | 1+5 (V) | Définition `l10n_cd` présente ; rendu Community à vérifier. |
| Autres obligations RDC (IBP, IPR, DSF, retenues) | 3+5 | **Non fait** ; règles à fournir/valider. |
| Idempotence / traçabilité | 3 | **Livré** : `vorpal_source_ref` unique par société + adaptateur. |
| Intégration Ventes/DGI/Paie | 3 | **Livré côté adaptateur** (4 événements), payloads provisoires. |

Enterprise à fort intérêt pour Vorpal (reproduire seulement par code indépendant) : rapprochement bancaire, états OHADA/TAFIRE, immobilisations, balance âgée, budgets.

## 3. Architecture retenue
```
Vorpal (Python pur)                         Odoo Community
 EventBus ──► AccountingEventHandlers        addon vorpal_compta_ohada
              │  (file persistante SQLite)    ├ account.move : vorpal_source_ref (unique),
              ▼                               │   vorpal_dgi_code/qr, verrouillage
       OdooAccountingPoster ──► OdooClient ───┼ journaux USD/CDF, devises
        (idempotent, reprise)   (XML-RPC | Fake)└ vorpal.ohada.report (états déclaratifs)
```
Choix : Vorpal est maître des documents commerciaux ; Odoo reçoit des écritures `entry` équilibrées, référencées, avec cachet DGI posé à la création. Rien dans `vorpal/core`.

## 4. Points à valider (expert-comptable OHADA / DGI RDC)
Comptes par défaut (`account_mapping.json`), taux et taxes (16 %, autres), règle d'arrondi USD/CDF, présentation du bilan (classement par préfixe, pas par sens du solde), durées/coefficients d'amortissement, comptes paie (INSS, IPR), numérotation fiscale, payloads d'événements, hash d'inaltérabilité, obligations déclaratives RDC.
