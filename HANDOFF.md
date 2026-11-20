## [2026-11-20 02:02 CAT] Module: core (01)
- Fait : Implémentation complète de `vorpal/core` (money, module, registry, context, events, services, db, audit, config, security, errors, partners, contracts/{dto,protocols,events}) + suite de tests `tests/core/test_core.py`, alignée sur l'architecture mise à jour (`vorpal/modules/adapters/odoo` + `odoo_addons/vorpal_compta_ohada` à l'étape 3).
- Reste à faire (ordre de priorité) :
  1. Passer au module `dgi` (spec 02) : modèles, interfaces, services, adaptateurs, événements, tests.
  2. Passer à la comptabilité Odoo (`vorpal/modules/adapters/odoo` + `odoo_addons/vorpal_compta_ohada`, spec 03).
  3. Passer au module `rh_paie` (spec 04).
- Décisions prises :
  - `Money` interdit strictement les `float` et toute opération arithmétique/comparaison entre devises distinctes.
  - `EventBus` isole les exceptions de handlers, émet `event.handler_failed`, applique l'idempotence par `(handler, event.id)` et supporte le wildcard `*` ainsi que `EventStore` / `replay()`.
  - `AuditLog` chaîne chaque entrée avec `prev_hash` et `entry_hash` en SHA-256 canonique et vérifie l'intégrité via `verify_chain()`.
- Points A_VALIDER :
  - # TODO(A_VALIDER): Format exact de persistence SQLAlchemy pour `AuditLog` et `EventStore` en production (actuellement en mémoire dans `core`, persistance DB à confirmer).
  - # TODO(A_VALIDER): Règles d'arrondi spécifiques par devise/opération (actuellement `ROUND_HALF_UP` à 2 décimales pour `USD` et `CDF`).
- Commande de test : `pytest tests/core -q` -> 7 passed.
- Prochain fichier à écrire : `vorpal/modules/dgi/` (modèles et interfaces selon spec 02).

## [2026-11-20] Module: core (01) : couverture config/money + lint (patch Claude)
- Fait : tests config (test_config.py) et money (test_money_extra.py) ; config.py : secrets masqués (repr=False), JSON malformé / fichier illisible -> ValidationError, message d'erreur sans valeurs saisies ; corrections lint manuelles ; engine.dispose() dans le test DB.
- Résultats (état avant le lot A du 2026-11-20 ci-dessous) : 60 passed, couverture 96 %, ruff 2 × B015, mypy 1 erreur (config.py:63).
- Limite connue : _parse_simple_yaml ne gère ni listes ni sections imbriquées ni '#' dans une valeur (ex. URL avec #). Si besoin : passer à PyYAML.

## [2026-11-20] Module: core (01) : lot A (corrections de stabilisation)
- Fait :
  - `tests/core/test_money_extra.py` : B015 corrigés (`operator.lt` / `operator.ge`).
  - `vorpal/core/config.py` : erreur mypy ligne 63 corrigée (narrowing `isinstance` sur `secrets`), comportement inchangé.
  - `tests/core/test_core.py` : fixture `app_ctx` en `yield` + `engine.dispose()` (cause suspectée du ResourceWarning SQLite, non reproduit en Python 3.12.3 : à reconfirmer sur la machine d'origine).
  - Nouveau test d'ordre : `resolve_order()` == ["a", "b", "c"] et arrêt dans l'ordre inverse.
- Reste à faire (ordre de priorité) :
  1. (fait) Lot B applique : voir l'entree suivante. Puis module `dgi` (spec 02).
  2. Module compta Odoo (spec 03), puis `rh_paie` (spec 04).
- Décisions prises :
  - `AppContext` contient `authz` (absent de la liste de la spec 01, requis par `AuthorizationService` et le registry).
- Points à trancher (lot B : tranches et appliques, voir l'entree suivante) :
  - Contrats DTO vs spec 02 : `InvoiceLineDTO` n'a pas de code article (exigé pour les lignes « hors champ »), `InvoiceDTO` n'a ni « montant non taxable » ni « autres impôts et taxes » (mentions obligatoires). Enrichir maintenant (champs optionnels) ou au début du module dgi ?
  - `PyYAML` est déclaré dans `pyproject.toml` mais non utilisé (parseur maison) et hors stack autorisée par la spec 00 : le retirer ou le documenter.
  - Chemin de l'adaptateur Odoo : `vorpal/modules/adapters/odoo` (spec 00, ce fichier) vs `vorpal/adapters/odoo` (spec 03).
  - `AuditLog.record` ne copie pas `before` / `after` : une mutation par l'appelant casse `verify_chain()`.
  - `event.handler_failed` absent du catalogue de la spec 01 et de `OFFICIAL_EVENTS`.
  - `InvoiceDTO` : `total_vat` non recoupé avec `vat_rate` des lignes (à confirmer avec la spec DGI).
  - Tests dupliqués entre `test_hardening.py` et `test_core_hardening.py` ; aucune section `[tool.ruff.lint]` configurée.
  - `JournalLineDTO` sans montant en devise société ni taux (à revoir à l'étape 3, critère « facture CDF dans société USD »).
- Points A_VALIDER (inchangés) : persistance DB de `AuditLog` / `EventStore` (en mémoire) ; règles d'arrondi par devise (`ROUND_HALF_UP`, 2 décimales USD et CDF).
- Commande de test : `pytest --cov=vorpal -q` -> 61 passed, couverture 96 % ; `ruff check .` -> All checks passed ; `mypy --strict vorpal` -> Success: no issues found in 18 source files.
- Prochain fichier à écrire : voir l'entree lot B ci-dessous.

## [2026-11-20] Module: core (01) : lot B (decisions B1 a B8 appliquees)
- Fait :
  - B1 `contracts/dto.py` : `InvoiceLineDTO.item_code` (optionnel, non vide), `InvoiceDTO.non_taxable_amount` et `InvoiceDTO.other_taxes` (Decimal >= 0, defaut 0, float et bool refuses). Simples porteurs pour le module dgi : aucune regle de totaux modifiee. Herites par `CertifiedInvoiceDTO`.
  - B2 `pyproject.toml` : `PyYAML` retire (non utilise, hors stack de la spec 00). `_parse_simple_yaml` reste le parseur YAML du core.
  - B4 `core/audit.py` : `AuditLog.record` copie `before` et `after` (deepcopy). Une mutation ulterieure par l'appelant ne casse plus `verify_chain()`.
  - B5 `contracts/events.py` : `event.handler_failed` documente comme evenement systeme, volontairement hors `OFFICIAL_EVENTS` (evenements metier).
  - B7 `pyproject.toml` : section `[tool.ruff.lint]` explicite (E4, E7, E9, F, I, B, UP, RUF, TRY, ISC ; ignore TRY003 et UP042). Le resultat de `ruff check .` ne depend plus de la version de ruff installee.
  - Tests ajoutes : `tests/core/test_dto_contracts.py`, `tests/core/test_audit_copy.py`.
- Decisions prises :
  - B3 : l'adaptateur Odoo vit dans `vorpal/modules/adapters/odoo` (spec 00). La spec 03 ecrit `vorpal/adapters/odoo` : a corriger dans la spec (hors depot).
  - B5 : `event.handler_failed` est une ligne du catalogue de la spec 01 (emis par core/EventBus, ecoute par alertes et audit), signalee comme evenement systeme.
  - Corrections a reporter dans les specs (hors depot) : spec 01 (champs de `InvoiceDTO` et `InvoiceLineDTO`, `permissions` dans `ModuleManifest`, `authz` dans `AppContext`, ligne `event.handler_failed`) et spec 03 (chemin de l'adaptateur).
- Reste a faire (ordre de priorite) :
  1. Module `dgi` (spec 02) : `vorpal/modules/dgi/` (modeles et interfaces d'abord). Le controle "ligne hors champ => item_code obligatoire" se fait dans ce module.
  2. Etape 3 (compta Odoo, spec 03), puis `rh_paie` (spec 04).
  3. Nettoyage facultatif : tests dupliques entre `test_hardening.py` et `test_core_hardening.py` (non touches).
- Points A_VALIDER :
  - B6 (module dgi) : recouper `total_vat` avec la somme des (HT ligne x `vat_rate`) selon la regle d'arrondi de la DGI. TODO(A_VALIDER) pose dans `InvoiceDTO.validate_totals`.
  - Lien exact entre `non_taxable_amount` / `other_taxes` et `total_ht` / `total_ttc` (regle fiscale). TODO(A_VALIDER) pose dans `InvoiceDTO`.
  - B8 (etape 3) : contre-valeur en devise societe / taux sur `JournalLineDTO`. TODO(A_VALIDER) pose dans `JournalLineDTO`.
  - Inchange : persistance DB de `AuditLog` / `EventStore` (en memoire) ; regles d'arrondi par devise (`ROUND_HALF_UP`, 2 decimales USD et CDF).
- Limites connues :
  - `AuditLog.entries` et la valeur retournee par `record()` exposent encore les dicts internes en lecture : a proteger lors de la persistance DB.
  - E501 (longueur de ligne) non applique et `ruff format --check` non propre (8 fichiers) : le formatage n'est pas impose par les specs.
- Commande de test : `pytest --cov=vorpal -q` -> 74 passed, couverture 97 % ; `ruff check .` -> All checks passed ; `mypy --strict vorpal` -> Success: no issues found in 18 source files.
- Prochain fichier a ecrire : `vorpal/modules/dgi/` (modeles et interfaces, spec 02).
