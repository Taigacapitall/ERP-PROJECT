# HANDOFF — Vorpal Business Management

> **Document unique de reprise.** C'est le seul fichier à tenir à jour. L'historique des anciennes sessions est archivé dans
> `docs/archive/HANDOFF_HISTORIQUE_2026-11-24.md` (ne plus le modifier). Dernière mise à jour : **27 novembre 2026** (briques A1 et A2).

## 1. Vérifier l'état en 1 minute
```bash
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q                     # attendu : 829 passed (mesuré par Josh après A1, 28/11/2026)
python -m ruff check .                  # attendu : All checks passed
python -m mypy vorpal                   # attendu : Success, 0 erreur (175 fichiers)
git diff core-v1 -- vorpal/core         # attendu : vide (Core gelé)
```
Le chiffre fait foi seulement s'il est reproduit par ces commandes. Référence avant A1 (sortie fournie par Josh, 27/11/2026) : **886 passed, 2 skipped** ; ruff `.` 62 erreurs ; mypy 3 erreurs (toutes dans l'adaptateur Odoo). Les anciens chiffres 719 et 770 de ce fichier étaient périmés.

## 2. Carte du dépôt
| Chemin | Rôle |
|---|---|
| `vorpal/core/` | Core v1 **gelé** (tag `core-v1`) : événements, contrats DTO, permissions, audit, DB |
| `vorpal/modules/ventes/` | Clients, catalogue, devis, commandes, factures, paiements, avoirs, PDF, persistance SQL |
| `vorpal/modules/dgi/` | Circuit de certification fiscale (client **simulé** uniquement) |
| `vorpal/modules/achats/` | Fournisseurs, commandes d'achat, réceptions, factures fournisseurs |
| `vorpal/modules/comptabilite/` | **Comptabilité native** (décision E16) : plan OHADA, journaux, écritures, exercices, grand livre, balance, bilan, compte de résultat, clôture |
| `legacy/` | **Archive A1** : adaptateur Odoo (`vorpal/modules/adapters/odoo`), `odoo_addons` et leurs tests, déplacés sans modification. Hors paquet, hors CI, hors Ruff. Voir `legacy/README.md` |
| `tests/` | core, dgi, ventes, achats, stock, comptabilite, integration |
| `docs/` | Documentation de référence (voir §8) |
| `.github/workflows/ci.yml` | **CI (A2)** : Python 3.11 et 3.12, service PostgreSQL 16 ; ruff `.`, mypy strict, contrôle Core (aucune ligne supprimée par rapport à `core-v1`), pytest. Bloquant : aucune étape n'ignore une erreur |
| `docker-compose.yml` | PostgreSQL 16 de développement (`docker compose up -d db`) |

## 3. État par module (preuves : tests exécutés le 24/11/2026)
| Module | Statut | Remarque |
|---|---|---|
| Core | Vérifié, inchangé | Identique à `core-v1` |
| Ventes — devis, commandes, clients | Vérifié | Cycle complet exécuté sur base SQL |
| Ventes — **facturation classique (sans DGI)** | Vérifié (lot 1) | Voir §5 |
| DGI | Simulateur seulement | Pas de client réel, pas d'homologation (évolution distincte) |
| Achats | Présent, peu de tests dédiés (9) | Pas encore exercé par scénario complet ni relié à la comptabilité |
| Comptabilité native | Vérifié (51 tests) | Grand livre, états, clôture ; branchée aux événements Ventes (facture classique, avoir, encaissement, annulation) et DGI (facture/avoir certifié) ; **Achats et Paie non branchés** ; plan de base à valider ; voir `docs/COMPTABILITE.md` |
| Comptabilité (Odoo) | **Archivé** (`legacy/`, A1) | Voie abandonnée ; code conservé tel quel, non testé en CI |
| API / interface / authentification | **Absent** | Bloquant pour toute utilisation réelle (lots 2+) |

## 4. Qualité mesurée
- Tests : avant A1, 886 réussis, 2 ignorés. Après A1 (mesuré par Josh le 28/11/2026) : **829 réussis, 0 ignoré**, ruff `.` sans erreur, mypy sans erreur (175 fichiers), `git diff core-v1 -- vorpal/core` vide. La différence (57 réussis + 2 ignorés) correspond aux tests Odoo archivés. Couverture `vorpal/` : 93 % (mesurée avant le lot 1).
- Avant A1 : `ruff check .` 62 erreurs (6 `adapters/odoo`, 9 `odoo_addons`, 47 `tests`) ; mypy 3 erreurs (`odoo/handlers.py`, `odoo/inbox.py`).
- Après A1 : Odoo archivé dans `legacy/` (exclu de Ruff via `extend-exclude`, hors paquet donc hors Mypy) ; 34 erreurs Ruff de `tests/ventes` corrigées (style seul). Résultat confirmé : Ruff 0 erreur, Mypy 0 erreur.
- Les 17 fichiers `*.backup*` / `*.bak` restent en place, ignorés par `.gitignore`, **non supprimés** (décision Josh en attente).
- Ne jamais relâcher Ruff/Mypy ni ajouter `type: ignore` pour masquer une erreur.

## 4 bis. CI (A2)
- La CI expose `VORPAL_TEST_DATABASE_URL` (PostgreSQL 16) ; **aucun test actuel ne l'utilise** et aucun pilote PostgreSQL (psycopg) n'est déclaré : il sera ajouté, version épinglée, avec les premiers tests PostgreSQL (brique B1).
- Le contrôle Core de la CI sera remplacé par `scripts/check_additive_changes.py` (A4). Pré-requis : le tag `core-v1` doit exister sur GitHub (`git push origin core-v1` si absent).
- À faire par Josh : activer la protection de branche `main` (exiger le contrôle « Contrôles (Python 3.11) » et « (3.12) » avant fusion) ; vérifier qu'une erreur volontaire fait échouer la CI (voir livraison A2).

## 5. Facturation commerciale classique (lot 1, sans DGI)
Services (exposés par `VentesModule`) : `classic_service` (`services/classic_invoicing.py`), `query_service` (`services/invoice_queries.py`), `documents.py` (PDF, CSV), plus `customer_service`, `pricing_service`, `quote_service`, `order_service`, `invoice_service` (mode DGI).
- **Émission** : numérote, fige les totaux, **n'émet aucun événement DGI**. Statut `VALIDATED` = « Émise » (pas de nouveau statut ni de changement de schéma). Client sans NIF accepté.
- **Facture depuis commande** (totale ou partielle, suivi des reliquats) ; devis → commande → facture.
- **Paiements** : partiels, acomptes lettrés plus tard, annulation, refus des doublons de référence et des dépassements de solde ; événement `payment.received` émis après commit.
- **Avoirs** (totaux ou partiels, motif obligatoire, cumul ≤ facture) ; **annulation = avoir total**, refusée si encaissée ou déjà créditée. Le motif est conservé dans le journal d'audit (pas de champ dédié dans le domaine).
- **Consultation** : liste filtrée, impayés échus, relevé client par devise (USD/CDF).
- **Documents** : PDF facture / avoir / devis (reportlab, extra `pdf`), mention « non certifié par la DGI » ; export CSV.
- **Numérotation** : allocation atomique `UPDATE … RETURNING` (corrige un défaut constaté : 3 échecs sur 8 émissions simultanées ; désormais 8/8 sans doublon).
- Permissions : émission `ventes.confirm` · création/paiement `ventes.create` · avoir, annulation, annulation de paiement `ventes.admin` · lecture `ventes.view`.
- Fichiers existants modifiés (autorisés explicitement) : `ventes/domain/invoices.py` (+ paramètre `allow_uncertified_original`, défaut inchangé), `ventes/module.py`, `ventes/adapters/persistence/numbering.py`, `pyproject.toml` (extra `pdf`).
- Tests : `tests/integration/test_ventes_classic_flow.py` (10 tests : flux complet, garde-fous, permissions, redémarrage sur base fichier, concurrence, PDF/CSV).

## 6. Feuille de route MVP (issue de l'audit du 23/11/2026)
| # | Élément | État |
|---|---|---|
| E1–E7 | Facturation classique, clients sans NIF, paiements, consultation/impayés, avoirs/annulation, commande → facture, PDF/CSV | **Livré (lot 1)** |
| E11 | Services Ventes tous exposés par le module | **Livré (lot 1)** |
| E13 | Concurrence et base fichier | **Partiel** : numérotation et redémarrage testés ; PostgreSQL non testé |
| E8 | API + authentification + rôles | À faire (**critique**) |
| E9 | Interface web | À faire (**critique**) |
| E10 | Installation, base persistante, sauvegarde/restauration, profil entreprise éditable, audit persistant | À faire (**critique**) |
| E12 | Règles métier `A_VALIDER` (TVA, arrondis, numérotation, relances) | À valider par un comptable |
| E14 | Relances automatiques | À faire |
| E15 | Achats exploitable (paiement fournisseur, écrans) | À faire |
| E16 | Comptabilité native (grand livre, balance, bilan, résultat, clôture) | **Socle et branchement Ventes/DGI livrés** ; reste : Achats/Paie, états PDF/CSV, balance âgée, validation comptable |
| E17 | Corriger Ruff/Mypy Odoo, nettoyer le dépôt | A1 : Odoo archivé, Ruff nettoyé ; 17 sauvegardes à traiter |
| E18 | Tableaux de bord, e-mail, import CSV, taux de change | À faire |
| E19 | DGI réelle et facture normalisée | Plus tard, évolution distincte |

**Verdict actuel** : bibliothèque métier solide, **pas encore un produit installable** (pas d'API, d'interface ni d'authentification). Le prochain lot est E8–E10.

## 7. Règles de travail
- Ne pas modifier `vorpal/core/` ni les contrats sans autorisation explicite. Zones sensibles : `vorpal/modules/ventes/domain/`, `settings.py`, `ports/`.
- Ne pas supprimer ni affaiblir de tests ; ne pas relâcher Ruff/Mypy.
- Commit et push **uniquement avec autorisation explicite**, par lots, avec résultats de tests rapportés exactement.
- Ne jamais ajouter au dépôt de sauvegardes (`*.backup*`, `*.bak`, `_backups*`, `_sauvegardes_*`) : elles sont ignorées par `.gitignore`.
- **Un seul HANDOFF** : toute mise à jour se fait dans ce fichier (section §3, §4, §6) ; ne pas créer de nouvelles copies datées.

## 8. Documents de référence
| Document | Contenu |
|---|---|
| `HANDOFF.md` | État, procédure de reprise, feuille de route (ce fichier) |
| `docs/CORE_DOCUMENTATION.md` | Architecture et règles du Core |
| `docs/VENTES_DOCUMENTATION.md` | Module Ventes (§16 : facturation classique) |
| `docs/COMPTABILITE.md` | État de la comptabilité Odoo/OHADA |
| `docs/AUDIT_COMPTA_OHADA.md`, `docs/README_ADAPTATEUR_ODOO.md` | Audit OHADA ; installation de l'adaptateur Odoo (archivé) |
| `docs/archive/` | Historique (lecture seule) |

## 9. Points ouverts
- Valider avec un comptable les points `A_VALIDER` (taux de TVA, arrondi par ligne ou global, formats de numérotation, délais de relance).
- Avoirs avec remboursement client : le solde net négatif est affiché mais aucun remboursement n'est géré.
- Le journal d'audit (`AuditLog` du Core) est en mémoire : à rendre persistant (E10) sans toucher au Core.
- DGI réelle : client HTTP, mapping des groupes de taxe, homologation (hors périmètre MVP).
