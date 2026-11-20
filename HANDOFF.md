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
