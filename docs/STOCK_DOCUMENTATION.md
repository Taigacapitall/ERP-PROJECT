# Module Stock - documentation

Module `stock` (v0.1.0) : articles, entrepôts, mouvements valorisés, inventaire physique, liaison
avec Achats. Il suit l'architecture hexagonale d'Achats (domaine / ports / services / adapters) et
**ne dépend d'aucun autre module** : il n'importe que `vorpal.core`.

## 1. Ce que fait le module

| Fonction | Détail |
|---|---|
| Articles | code (= `item_code` d'Achats/Ventes), unité libre (sac, carton, tonne...), devise de valorisation, précision décimale par article (0 à 6), seuil de réapprovisionnement, activation. Unité, devise et précision sont figées après création. |
| Entrepôts | Plats : un « emplacement » est simplement un autre code d'entrepôt. Désactivation refusée tant qu'il reste du stock. |
| Mouvements | Entrée, sortie, transfert. Journal **append-only**, chaque ligne porte le solde après mouvement. Pas de stock négatif. |
| Valorisation | **Coût moyen pondéré (CUMP)** par article et entrepôt, en `Decimal`, arrondi à 2 décimales. Sortir tout le stock remet la valeur à exactement 0. |
| Inventaire physique | Fiche (ouverture, comptage, validation, annulation). La validation compare au solde **courant** et crée un mouvement `ADJUSTMENT` par écart. |
| Alertes | Événement `stock.level.low` quand le total d'un article **franchit** son seuil à la baisse ; requête `low_stock_items`. |
| Liaison Achats | Réception validée -> entrée en stock au coût unitaire HT. Frais d'approche -> revalorisation du stock restant. |

## 2. Intégration (aucun changement dans les autres modules)

Stock écoute deux événements publiés par Achats et ne l'importe jamais.

**`achats.goods_receipt.validated`** : lit `goods_receipt_id`, `warehouse_code`, `receipt_date`,
`currency`, et par ligne `line_id`, `item_code`, `quantity_received`, `unit_cost_ht`. Un mouvement
`RECEIPT` est créé par ligne, clé d'idempotence `("goods_receipt", goods_receipt_id, line_id)`.

**`achats.landed_cost.allocated`** : lit `landed_cost_id`, `goods_receipt_id`, `currency`,
`allocated_at`, et par allocation `receipt_line_id`, `allocated_amount`. Un mouvement `REVALUATION`
(quantité 0, valeur +montant) est créé dans l'entrepôt de la réception d'origine.

Garanties :

- **Idempotent** : republier ou rejouer (`bus.replay`) ne crée jamais de second mouvement et ne
  republie pas d'événement.
- **Tout ou rien** par événement (une ligne en erreur annule toute la réception côté Stock).
- **Échec visible, jamais silencieux** : article/entrepôt inconnu, devise différente, article
  désactivé -> le bus publie `event.handler_failed` ; Achats n'est pas bloqué. Après correction
  (par exemple création de l'article), `bus.replay("achats.goods_receipt.validated")` rattrape.
- Un frais d'approche reçu **avant** sa réception échoue de la même façon, puis passe au rejeu.
- Option `auto_create_missing_masters` (défaut `false`) : crée l'article et l'entrepôt manquants.

Événements publiés (`source_module="stock"`, après commit) :
`stock.movement.recorded`, `stock.level.low`, `stock.inventory.validated`,
`stock.landed_cost.not_capitalized`.

Sorties vers les ventes : Ventes n'émet aucun événement en facturation classique, donc **aucune
sortie automatique** n'est branchée. Une sortie se saisit avec `MovementService.issue_stock(...)`
(`source_type="sale"`, `reference="FAC-..."`). Brancher l'automatique demandera un événement côté
Ventes, décision à prendre séparément (le Core et les autres modules restent intouchés ici).

## 3. Règles de valorisation (CUMP)

- Entrée : valeur += `round(quantité x coût unitaire, 2)`.
- Sortie : valeur sortie = `round(valeur x quantité sortie / quantité en stock, 2)`.
- Transfert : valeur sortie au coût moyen de la source, reprise **à l'identique** à destination.
- Frais d'approche : valeur += montant, quantité inchangée. Chaque document s'ajoute (cumul).
  Si le stock restant est nul, rien n'est capitalisé : mouvement de valeur 0 avec la mention
  `NON_CAPITALISE` et événement `stock.landed_cost.not_capitalized` (la perte reste visible).
- Inventaire : écart négatif = sortie au coût moyen ; écart positif = entrée au coût moyen, ou au
  coût unitaire saisi si le stock théorique est nul (sinon erreur : aucune valeur n'est inventée).

## 4. Permissions

`stock.view` (consultation), `stock.create` (articles, entrées/sorties/transferts),
`stock.inventory` (fiches d'inventaire), `stock.admin` (entrepôts, désactivation d'articles).

## 5. Persistance

Tables `stock_items`, `stock_warehouses`, `stock_levels`, `stock_movements`,
`stock_inventory_counts`, `stock_inventory_lines`, `stock_sequences` (migration v1 via
`SchemaMigrationManager`, uniquement les tables `stock_*`). `Decimal` stocké en texte
(`DecimalText`). Un solde est toujours verrouillé par un `UPDATE` atomique avant modification
(même principe que la numérotation de Ventes) ; contrainte d'unicité sur la source d'un mouvement.
Numérotation sans trou : `MVT-AAAA-000001` (mouvements), `INV-AAAA-00001` (inventaires).

Configuration : valeurs par défaut en code (aucun fichier de données, donc aucune modification de
`pyproject.toml`). `StockModule(settings_path=...)` accepte un JSON de surcharge ; les clés inconnues
sont refusées.

## 6. Limites connues et points A_VALIDER

- Pas de stock négatif, pas de réservation, pas de lots/numéros de série, pas de conversion d'unités.
- Pas d'écritures comptables : la valorisation est exposée (soldes et mouvements), la
  comptabilisation OHADA reste à brancher via les événements `stock.*`.
- Pas de FIFO : méthode de valorisation (CUMP vs FIFO) et règle d'arrondi à valider avec un comptable.
- Frais d'approche : capitalisés sur le stock **restant** (pas de suivi par lot).
- Pas de hiérarchie d'emplacements (entrepôts plats).
- Une devise par article : une réception dans une autre devise est refusée (pas de conversion).
- Observation sur Achats (non modifié) : un second document de frais d'approche **écrase** l'allocation
  affichée sur la ligne de réception au lieu de s'y ajouter ; Stock, lui, cumule chaque document.

## 7. Tests

`tests/stock/` : domaine, services, inventaire, événements Achats (charges utiles identiques à
celles d'Achats), garde-fous d'architecture, scénario de référence chiffré à la main, puis
`test_stock_sql.py` (schéma, parité mémoire/SQL, redémarrage, concurrence) et
`test_stock_integration.py` (ModuleRegistry + vrai module Achats).
