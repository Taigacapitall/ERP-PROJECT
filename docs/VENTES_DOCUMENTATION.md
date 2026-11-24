# Vorpal Business Manager --- Documentation technique du module Ventes

**Document de référence --- version de préparation GitHub**\
**État des informations : 20 novembre 2026**\
**Périmètre :** `vorpal/modules/ventes/` et `tests/ventes/`

> **Note de fiabilité.** Cette documentation est rédigée à partir de
> l'architecture, des fichiers et des résultats explicitement établis
> pendant le travail sur le projet. Le contenu du dépôt local n'étant
> pas directement accessible dans cette conversation, les détails qui
> nécessitent une lecture du code source sont signalés comme « à
> confirmer dans le code ». Ne pas interpréter une description
> d'objectif ou de parcours métier comme la preuve que toutes les
> opérations correspondantes sont intégralement implémentées.

## 1. Présentation générale

Le module **Ventes** porte le périmètre métier de gestion commerciale de
Vorpal Business Manager. Il regroupe le domaine de vente, les services
applicatifs, les contrats (ports) et les adaptateurs nécessaires à la
persistance et aux échanges avec le reste du système.

Les objets métier identifiés dans le module couvrent notamment les
clients, le catalogue, les devis, les commandes, les factures, les
paiements, les relances et les calculs de totaux.

Le module doit rester responsable de ses règles commerciales. Les
préoccupations transversales et les mécanismes communs relèvent du Core
ou des modules spécialisés. Les obligations fiscales définitives doivent
être confirmées avec le module DGI et les sources réglementaires
compétentes ; les paramètres provisoires ne constituent pas une
validation réglementaire.

## 2. Architecture et organisation

L'organisation connue suit une séparation en couches :

-   **Domaine (`domain/`)** : objets métier, règles et calculs
    indépendants des mécanismes techniques.
-   **Services (`services/`)** : orchestration des cas d'utilisation et
    coordination des objets métier et des ports.
-   **Ports (`ports/`)** : contrats décrivant les dépendances attendues
    par le module.
-   **Adaptateurs (`adapters/`)** : implémentations techniques des
    contrats, en particulier pour la persistance.
-   **Configuration (`settings.py`, `data/`)** : chargement et
    validation des paramètres du module.
-   **Tests (`tests/ventes/`)** : tests du domaine, des services et des
    adaptateurs.

Principe attendu : le domaine ne doit pas dépendre de SQLAlchemy, d'une
base de données ou d'un service d'infrastructure. Les services dépendent
de contrats plutôt que d'implémentations concrètes lorsque les ports
correspondants existent. Les détails exacts des imports et des
dépendances doivent être confirmés dans le code courant avant toute
refonte.

## 3. Arborescence connue

Arborescence identifiée pendant les audits :

``` text
vorpal/modules/ventes/
├── __init__.py
├── data/
│   └── sales_settings.json
├── settings.py
├── domain/
│   ├── __init__.py
│   ├── catalog.py
│   ├── customers.py
│   ├── errors.py
│   ├── invoices.py
│   ├── orders.py
│   ├── payments.py
│   ├── quotes.py
│   ├── reminders.py
│   └── totals.py
├── ports/
│   ├── __init__.py
│   ├── channels.py
│   ├── publisher.py
│   ├── repositories.py
│   └── unit_of_work.py
├── services/
│   ├── __init__.py
│   ├── common.py
│   ├── customers.py
│   ├── orders.py
│   ├── pricing.py
│   └── quotes.py
└── adapters/
    └── persistence/
        ├── __init__.py
        ├── mappers.py
        ├── memory_repositories.py
        ├── migrations.py
        ├── models.py
        ├── numbering.py
        ├── sql_repositories.py
        ├── types.py
        └── unit_of_work.py
```

Cette liste reflète les chemins recensés dans le dépôt au moment de
l'audit. Avant d'ajouter ou de supprimer un composant, vérifier
l'arborescence réelle avec `find vorpal/modules/ventes -type f`.

## 4. Domaine métier

Les fichiers de domaine identifiés sont :

  -----------------------------------------------------------------------
  Fichier                             Responsabilité générale
  ----------------------------------- -----------------------------------
  `domain/customers.py`               Modèle et règles métier des clients

  `domain/catalog.py`                 Éléments du catalogue utilisés dans
                                      les ventes

  `domain/quotes.py`                  Devis et lignes de devis

  `domain/orders.py`                  Commandes et lignes de commande

  `domain/invoices.py`                Factures, lignes et données de
                                      certification/fiscalité

  `domain/payments.py`                Objets et règles métier liés aux
                                      paiements

  `domain/reminders.py`               Objets liés aux relances

  `domain/totals.py`                  Calculs de totaux et règles
                                      associées

  `domain/errors.py`                  Erreurs métier du module
  -----------------------------------------------------------------------

Les invariants détaillés, signatures, transitions d'état et validations
doivent être lus directement dans chaque classe et méthode. Ne pas
recopier des règles fiscales ou commerciales supposées sans preuve dans
le code et validation métier.

## 5. Services applicatifs

Les services identifiés dans le dépôt sont :

-   `services/customers.py` --- service de gestion des clients
    (`CustomerService`).
-   `services/pricing.py` --- service de tarification
    (`PricingService`).
-   `services/quotes.py` --- service relatif aux devis (`QuoteService`).
-   `services/orders.py` --- service relatif aux commandes
    (`OrderService`).
-   `services/common.py` --- composants communs aux services.

Les audits ont explicitement identifié les types `CustomerService`,
`PricingService`, `QuoteService` et `OrderService`. Les opérations
publiques, leurs signatures, les validations et les exceptions doivent
être décrites à partir du code exact. Ne pas présenter les services de
facturation, de paiement ou de relance comme implémentés simplement
parce que leurs objets de domaine existent.

## 6. Ports et contrats

Les fichiers recensés sont :

-   `ports/repositories.py` : contrats des repositories.
-   `ports/channels.py` : interfaces de canaux et points d'intégration.
-   `ports/publisher.py` : contrat relatif à la publication
    d'événements.
-   `ports/unit_of_work.py` : contrat de coordination transactionnelle.

Les ports définissent les capacités attendues par les services sans
imposer à ceux-ci un moteur de persistance particulier. Pour chaque
contrat, consulter les signatures, types et docstrings du fichier avant
de modifier une implémentation.

Les interfaces de publication et de canaux constituent des points de
raccordement aux mécanismes transversaux et aux futurs modules. Leur
présence ne prouve pas à elle seule qu'un transport externe ou un
consommateur distant est déjà opérationnel.

## 7. Persistance et adaptateurs

Les adaptateurs recensés se trouvent dans `adapters/persistence/` :

  -----------------------------------------------------------------------
  Fichier                             Rôle
  ----------------------------------- -----------------------------------
  `models.py`                         Modèles de persistance SQLAlchemy

  `mappers.py`                        Conversion entre modèles persistés
                                      et objets métier

  `sql_repositories.py`               Repositories adossés à SQL

  `memory_repositories.py`            Repositories en mémoire utilisés
                                      notamment pour les tests

  `types.py`                          Types et conversions liés à la
                                      persistance

  `migrations.py`                     Mécanismes de migration

  `numbering.py`                      Numérotation

  `unit_of_work.py`                   Implémentation de l'unité de
                                      travail
  -----------------------------------------------------------------------

Les audits ont confirmé l'existence de tests ciblés pour les
repositories SQL et mémoire, les mappers, les modèles, les types, les
migrations, la numérotation et le Unit of Work.

Les détails du schéma, les contraintes SQL, la stratégie de migration et
la gestion des erreurs de base de données doivent être documentés à
partir des définitions actuelles, sans supposer un moteur ou une
politique de déploiement non confirmé.

## 8. Transactions et Unit of Work

Le module possède un port `Unit of Work` et une implémentation de
persistance correspondante. Le principe est de coordonner les
repositories et le cycle transactionnel d'une opération applicative.

Les tests identifiés comprennent `test_unit_of_work.py`. Les audits
antérieurs ont rapporté 13 tests pour le Unit of Work et une couverture
de 100 % de ce composant à ce moment-là.

Avant de modifier ce mécanisme, vérifier dans `ports/unit_of_work.py` et
`adapters/persistence/unit_of_work.py` : - le moment où la transaction
est ouverte ; - les règles de commit et de rollback ; - la gestion des
exceptions ; - le cycle de vie de la session ; - les repositories
exposés ; - les garanties effectivement offertes.

Ne pas extrapoler ces garanties au-delà de ce que l'implémentation et
ses tests démontrent.

## 9. Configuration

Le module contient : - `settings.py` - `data/sales_settings.json`

Le chemin de chargement du fichier JSON a été conçu pour être relatif à
l'emplacement du module
(`Path(__file__).resolve().parent / "data" / "sales_settings.json"`),
afin de ne pas dépendre du répertoire courant d'exécution. Le chargement
et la validation des paramètres sont assurés par `SalesSettings`.

Le contenu de configuration comporte des valeurs explicitement marquées
`A_VALIDER`. Ces valeurs doivent rester considérées comme provisoires.

Une déclaration `setuptools.package-data` a été ajoutée dans
`pyproject.toml` pour inclure `data/*.json` dans le paquet
`vorpal.modules.ventes`. Cette modification doit être conservée si le
packaging utilise cette configuration, et validée par un build du
paquet.

## 10. Parcours métier --- portée et prudence

Les concepts du domaine permettent de structurer des parcours autour des
clients, du catalogue, des devis, des commandes, de la tarification, des
factures, des paiements et des relances.

À ce stade, les services applicatifs explicitement identifiés sont
Customer, Pricing, Quote et Order. La présence des entités Invoice,
Payment et Reminder ne suffit pas à conclure que les workflows complets
de facturation, encaissement et relance sont disponibles.

Pour chaque parcours, un agent doit tracer le flux réel : 1. point
d'entrée du service ; 2. validation et règles métier appelées ; 3. ports
sollicités ; 4. persistance via Unit of Work ; 5. événements ou canaux
éventuels ; 6. résultat et erreurs.

La transformation devis-commande, l'émission de facture, le paiement et
les relances doivent être décrits comme opérationnels uniquement si le
code actuel et les tests correspondants le démontrent.

## 11. Intégration avec le Core et les autres modules

Le dépôt place le Core sous `vorpal/core/` et les modules sous
`vorpal/modules/`. Le module Ventes possède ses propres ports
d'intégration.

Les dépendances futures ou à confirmer comprennent notamment : - **DGI**
: certification et règles fiscales ; - **Stocks** : disponibilité ou
mouvements de stock ; - **Trésorerie** : encaissements et rapprochements
; - mécanismes transversaux du Core : événements, configuration ou
autres services partagés.

Ne pas considérer ces intégrations comme achevées sans code
d'intégration et tests correspondants. Les événements
`invoice.ready_for_certification`, `invoice.certified`,
`credit_note.certified`, `invoice.rejected` et `payment.received` ont
été identifiés comme payloads/contrats à figer ou à confirmer dans les
travaux antérieurs.

## 12. Tests et commandes

Les tests du module se trouvent sous `tests/ventes/`. Les fichiers
recensés couvrent notamment :

-   domaine : catalogue, clients, devis/commandes,
    factures/paiements/relances, totaux ;
-   services : commun, clients, commandes, tarification, devis ;
-   persistance : repositories SQL et mémoire, mappers, modèles, types ;
-   infrastructure : migrations, numérotation et Unit of Work ;
-   configuration : paramètres du module.

Commandes depuis la racine du dépôt, après activation de l'environnement
virtuel :

``` bash
python -m pytest tests/ventes -q
python -m pytest -q
python -m coverage run --rcfile=/tmp/vorpal-coverage.ini -m pytest tests/ventes
python -m coverage report --rcfile=/tmp/vorpal-coverage.ini -m
python -m ruff check vorpal/modules/ventes tests/ventes
python -m mypy vorpal/modules/ventes tests/ventes --show-error-codes
```

La configuration `/tmp/vorpal-coverage.ini` est un fichier temporaire
local utilisé lors de l'audit, et ne fait pas partie du dépôt par
défaut. Elle contient :

``` ini
[run]
source = vorpal.modules.ventes

[report]
include_namespace_packages = True
```

Résultats historiques explicitement rapportés le 20 novembre 2026 : -
586 tests réussis dans l'audit de couverture du périmètre concerné ; -
couverture globale rapportée : 99 % ; - `mappers.py` : 100 % après ajout
de deux tests de reconstruction de champs optionnels ; - `settings.py` :
99 %, avec une ligne non couverte (ligne 143) ; - Ruff et Mypy strict
sur le code de production `vorpal/` : réussis, respectivement « All
checks passed! » et « Success: no issues found in 50 source files » ; -
audit Ruff sur `tests/ventes/services/` : 32 erreurs ; - audit Mypy sur
`tests/ventes/services/` : 230 erreurs dans cinq fichiers.

Ces chiffres sont des instantanés historiques, pas une garantie sur
l'état du dépôt après d'autres modifications. Réexécuter les commandes
avant de déclarer une validation actuelle.

Pour Coverage, le projet a rencontré un problème de compatibilité entre
pytest-cov et certains tests SQLAlchemy. La méthode
`coverage run -m pytest` ci-dessus était le contournement utilisé
pendant l'audit. Ne pas réintroduire une option de cache SQL sans
comprendre l'échec antérieur.

## 13. Points métier et réglementaires `A_VALIDER`

Les éléments suivants ont été explicitement laissés à confirmer :

1.  Format officiel du NIF DGI RDC.
2.  Format de numérotation fiscale continue par exercice : préfixe,
    longueur et réinitialisation annuelle.
3.  Groupes de taxe et taux officiels : les valeurs `A` à 16 % et `E` à
    0 % hors champ ont été utilisées comme placeholders.
4.  Règle officielle d'arrondi : prix unitaire net, ligne ou total
    global, précision USD/CDF.
5.  Représentation des remises dans le DTO fiscal `InvoiceLineDTO`.
6.  Conservation ou réattribution du numéro de facture après
    `invoice.rejected`.
7.  Payloads définitifs des événements fiscaux.
8.  Émetteur officiel de `payment.received` ; Ventes était indiqué comme
    émetteur provisoire en attendant Trésorerie.
9.  Délais et paliers de relance : `REL_1` 7 jours, `REL_2` 15 jours,
    `REL_3` 30 jours ; délai de paiement par défaut de 30 jours.

Aucune de ces valeurs ne doit être présentée comme règle réglementaire
définitive avant confirmation par les responsables métier et les sources
officielles compétentes.

## 14. État d'avancement et évolutions

Les lots 1 à 3 ont été décrits comme couvrant la configuration et les
erreurs, le domaine pur et les ports. Les lots 4 à 6 ont fait l'objet
d'une reconstruction et de travaux de tests/persistance. Les audits ont
ensuite porté sur les services, les repositories, les mappers et les
mécanismes de persistance.

L'état exact de chaque fonctionnalité doit être déterminé en lisant le
code courant. En particulier : - vérifier les services réellement
présents ; - vérifier les intégrations intermodules ; - vérifier les
migrations et leur applicabilité ; - vérifier le packaging ; -
réexécuter les tests et les contrôles statiques.

Prochaines étapes recommandées : 1. relire les contrats et les règles
métier avec les responsables du domaine ; 2. résoudre les points
réglementaires `A_VALIDER` ; 3. réduire les erreurs Ruff/Mypy des tests
sans relâcher les règles ni supprimer de scénarios ; 4. maintenir les
validations de production ; 5. ajouter les intégrations intermodules
avec tests contractuels ; 6. valider un build installable et la présence
du JSON de configuration.

## 15. Guide de reprise pour un agent

Depuis la racine du dépôt :

``` bash
source .venv/bin/activate
git status --short
python -m pytest tests/ventes -q
python -m ruff check vorpal
python -m mypy vorpal
```

Avant toute modification : 1. lire `HANDOFF.md` (document unique de reprise) ; 2. lire
`docs/CORE_DOCUMENTATION.md` pour les règles du Core ; 3. lire ce
document pour le module Ventes ; 4. examiner `git status` et les
différences existantes ; 5. identifier les fichiers autorisés à changer
; 6. consulter les tests avant de modifier un comportement ; 7.
sauvegarder les fichiers concernés sans écraser les sauvegardes
antérieures ; 8. effectuer les changements par lot et relancer les
validations ciblées puis globales.

Ne pas modifier le Core, les contrats protégés ou la configuration
globale sans autorisation explicite. Ne pas faire de commit ou de push
sans autorisation explicite.

## 16. Facturation commerciale classique (sans DGI) — lot 1, 24 novembre 2026

Cette section complète le module sans modifier les services DGI existants (`InvoiceService.validate_and_submit` reste le chemin « certification »).

**Services** (exposés par `VentesModule`) : `classic_service` (`ClassicInvoicingService`), `query_service` (`InvoiceQueryService`), fonctions de `services/documents.py`.

| Besoin | Méthode |
|---|---|
| Facture depuis une commande confirmée (totale ou partielle) | `invoice_from_order` |
| Émettre (numéroter, figer, sans événement DGI) | `issue_invoice` |
| Encaisser, lettrer, acomptes, annuler un paiement | `record_payment`, `allocate_payment`, `cancel_payment` |
| Avoir total ou partiel / annuler une facture | `credit_invoice`, `cancel_invoice` |
| Consulter, impayés échus, relevé client | `get_invoice`, `list_invoices`, `list_overdue`, `customer_statement` |
| PDF et CSV | `invoice_model` + `render_pdf`, `quote_model`, `export_invoices_csv` |

**Règles** : en mode classique, `VALIDATED` signifie « Émise » ; une facture entièrement créditée est « Annulée » (calcul, pas de statut stocké) ; le solde net est TTC − avoirs − encaissements ; un client sans NIF est accepté ; les PDF portent la mention « non certifié par la DGI ». Permissions : `ventes.confirm` (émission), `ventes.create` (facture depuis commande, paiement), `ventes.admin` (avoir, annulation, annulation de paiement), `ventes.view` (lecture).

**Modifications associées** : `domain/invoices.py` — paramètre `allow_uncertified_original` (défaut `False`) de `SalesInvoice.validate`, qui permet un avoir sur une facture émise non certifiée ; `adapters/persistence/numbering.py` — allocation atomique des numéros (`UPDATE … RETURNING`).

**Limites** : pas de remboursement client en cas de solde net négatif ; motif d'avoir conservé uniquement dans le journal d'audit ; règles d'arrondi et de numérotation toujours `A_VALIDER`.

------------------------------------------------------------------------

**Fin de la documentation du module Ventes.**
