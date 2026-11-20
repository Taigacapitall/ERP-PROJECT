# HANDOFF --- Vorpal Business Manager

**Dernière mise à jour documentaire : 20 novembre 2026**\
**Contexte :** préparation de la publication du dépôt GitHub et
transmission aux prochains agents.

> Ce document est le point d'entrée de reprise. Il résume l'état connu
> du projet et renvoie vers les documentations techniques. Les résultats
> de qualité datés sont des instantanés historiques : les commandes
> doivent être relancées avant toute affirmation sur l'état actuel.

## 1. Identité du projet

**Vorpal Business Manager** est un projet Python organisé autour d'un
Core partagé et de modules métier. Le dépôt contient notamment
`vorpal/core/`, le module `vorpal/modules/ventes/`, les tests sous
`tests/` et la documentation sous `docs/`.

Les versions et dépendances observées pendant les audits du 20 novembre
2026 incluaient Python 3.14.8, SQLAlchemy 2.1.9 et pytest 9.2.0.
Vérifier les versions réellement installées dans l'environnement de
travail avant de reproduire un résultat.

## 2. Architecture et chemins importants

``` text
vorpal erp/
├── docs/
│   ├── CORE_DOCUMENTATION.md
│   └── VENTES_DOCUMENTATION.md
├── HANDOFF.md
├── pyproject.toml
├── vorpal/
│   ├── core/
│   └── modules/
│       └── ventes/
└── tests/
    └── ventes/
```

-   `vorpal/core/` : fondations et mécanismes partagés.
-   `vorpal/modules/` : modules métier.
-   `tests/` : tests automatisés.
-   `docs/` : documentation technique.
-   `HANDOFF.md` : état du projet, règles de reprise et prochaines
    étapes.

Cette arborescence est un schéma de haut niveau ; consulter le dépôt
pour l'inventaire complet.

## 3. Documents de référence

1.  **`docs/CORE_DOCUMENTATION.md`** --- architecture, responsabilités,
    conventions et règles du Core. À lire avant de modifier une
    fondation partagée.
2.  **`docs/VENTES_DOCUMENTATION.md`** --- architecture, objets métier,
    services, ports, persistance, tests et points `A_VALIDER` du module
    Ventes.
3.  **`HANDOFF.md`** --- état général du projet, contrôles connus,
    restrictions et prochaine procédure de reprise.

Si `docs/VENTES_DOCUMENTATION.md` n'est pas encore présent dans le
dépôt, il s'agit du livrable documentaire préparé pour intégration :
vérifier sa présence avant de considérer la documentation publiée.

## 4. État du Core

Le Core V1 a été déclaré gelé dans le contexte du projet. Un audit
antérieur a confirmé son identité avec le tag `core-v1` (commit
`8d470b7`). La validation de production la plus récente rapportée le 20
novembre 2026 a donné :

-   Ruff sur `vorpal/` : `All checks passed!`
-   Mypy strict sur `vorpal/` :
    `Success: no issues found in 50 source files`.

Ne pas modifier le Core ou ses contrats sans autorisation explicite. Les
chemins protégés comprennent notamment : - `vorpal/core/` -
`tests/core/` - `pyproject.toml` (sauf autorisation explicite) -
`HANDOFF.md` (sauf autorisation explicite) - les contrats et paramètres
Ventes listés ci-dessous.

## 5. État du module Ventes

Le module se trouve sous `vorpal/modules/ventes/`. Les zones identifiées
comprennent : - `domain/` : catalogue, clients, erreurs, factures,
commandes, paiements, devis, relances et totaux ; - `services/` :
composants communs, clients, commandes, tarification et devis ; -
`ports/` : repositories, channels, publisher et Unit of Work ; -
`adapters/persistence/` : mappers, repositories mémoire et SQL,
migrations, modèles, numérotation, types et Unit of Work ; -
`settings.py` et `data/sales_settings.json`.

Les tests correspondants se trouvent dans `tests/ventes/` et couvrent le
domaine, les services, la configuration et la persistance.

### Avancement connu

-   Lots 1 à 3 : configuration/erreurs, domaine et ports documentés
    comme réalisés.
-   Lots 4 à 6 : services, adaptateurs/persistance et tests
    d'intégration/persistance ont été reconstruits et ont fait l'objet
    de validations successives.
-   Les audits ont ajouté des tests ciblés pour numérotation, Unit of
    Work, types, migrations, sérialisation des modèles et champs
    optionnels des mappers.
-   Les services applicatifs explicitement identifiés sont
    `CustomerService`, `PricingService`, `QuoteService` et
    `OrderService`.
-   Ne pas déduire que tous les workflows de facturation, paiement,
    relance ou toutes les intégrations intermodules sont achevés
    uniquement parce que les objets métier correspondants existent.

Pour les détails techniques, lire `docs/VENTES_DOCUMENTATION.md` et
vérifier les fichiers réels.

## 6. Qualité : derniers résultats historiques connus

Date de référence : **20 novembre 2026**.

  -----------------------------------------------------------------------
  Contrôle                            Résultat historique
  ----------------------------------- -----------------------------------
  Ruff sur `vorpal/`                  Réussi : `All checks passed!`

  Mypy strict sur `vorpal/`           Réussi : 50 fichiers source, aucune
                                      erreur

  Pytest / couverture Ventes          586 tests réussis, 99 % de
                                      couverture rapportée

  `mappers.py`                        100 % après ajout de tests de
                                      champs optionnels

  `settings.py`                       99 %, une ligne non couverte (ligne
                                      143)

  Ruff sur `tests/ventes/services/`   32 erreurs au dernier audit

  Mypy sur `tests/ventes/services/`   230 erreurs dans 5 fichiers au
                                      dernier audit

  Mypy global `vorpal tests`          327 erreurs dans 13 fichiers au
                                      dernier audit
  -----------------------------------------------------------------------

Ces chiffres ne sont pas des résultats garantis à l'instant présent. Ils
ne doivent pas être copiés dans un rapport comme validation actuelle
sans réexécution.

Commandes de référence depuis la racine, environnement activé :

``` bash
python -m pytest tests/ventes -q
python -m pytest -q
python -m ruff check vorpal
python -m ruff check tests/ventes/services
python -m mypy vorpal
python -m mypy tests/ventes/services --show-error-codes
python -m mypy vorpal tests
```

Pour la couverture, l'audit a utilisé `coverage run` directement à cause
d'un problème rencontré avec pytest-cov sur certains tests SQLAlchemy :

``` bash
python -m coverage erase
python -m coverage run --rcfile=/tmp/vorpal-coverage.ini -m pytest tests/ventes
python -m coverage report --rcfile=/tmp/vorpal-coverage.ini -m
```

Le fichier `/tmp/vorpal-coverage.ini` est un fichier temporaire local,
pas nécessairement versionné.

## 7. Règles impératives de travail

-   Ne pas modifier le Core sans autorisation.
-   Ne pas modifier les contrats protégés ou les paramètres protégés
    sans autorisation.
-   Ne pas relâcher Ruff ou Mypy pour faire disparaître les erreurs.
-   Ne pas ajouter des `type: ignore`, des `Any` ou des exclusions
    uniquement pour masquer des erreurs.
-   Ne pas supprimer de tests ni affaiblir les assertions pour faire
    passer la suite.
-   Préserver les sauvegardes et ne jamais écraser un backup existant.
-   Ne pas lancer de générateur qui écrase des tests sans inspection
    préalable.
-   Ne pas effectuer de commit ou push sans autorisation explicite.
-   Faire les changements par lots, avec résultats de tests et contrôles
    statiques rapportés exactement.

## 8. Fichiers et zones protégés

Les restrictions historiques du projet comprennent : - `vorpal/core/` -
`tests/core/` - `pyproject.toml` - `HANDOFF.md` -
`vorpal/modules/ventes/domain/` - `vorpal/modules/ventes/settings.py` -
`vorpal/modules/ventes/ports/repositories.py` -
`vorpal/modules/ventes/ports/channels.py` - tests Ventes des lots 1 à 3,
notamment `tests/ventes/adapters/persistence/test_unit_of_work.py`.

Une autorisation explicite ponctuelle peut lever une restriction pour
une opération donnée ; ne pas généraliser cette autorisation à d'autres
travaux.

### Scripts à manier avec prudence

-   `scripts/create_service_tests.py` est un générateur qui peut écraser
    `tests/ventes/services/test_customers.py`, `test_orders.py`,
    `test_quotes.py` et crée des sauvegardes dans `_backups_services`.
    Ne pas l'exécuter sans autorisation et inspection.
-   `reconstruire_ventes_lots_4_5_6.py` est un script de reconstruction
    : ne pas le relancer sans comprendre ses effets.
-   Les sauvegardes sous `_sauvegardes_reconstruction/` et les fichiers
    `.backup*` doivent être préservés et ne pas être ajoutés au dépôt
    par inadvertance.

## 9. Points restant à traiter

### Priorité 1 --- qualité des tests

-   Reprendre les erreurs Ruff et Mypy des tests de services.
-   Corriger les annotations et doubles de test sans modifier le
    comportement des tests.
-   Étendre ensuite l'audit aux autres tests concernés par Mypy global.
-   Réexécuter la suite complète et la couverture.

### Priorité 2 --- règles métier et conformité

Les points suivants restent `A_VALIDER` : - format officiel du NIF DGI
RDC ; - numérotation fiscale continue par exercice ; - groupes et taux
de TVA officiels ; - règle d'arrondi USD/CDF ; - représentation des
remises dans les DTO fiscaux ; - traitement du numéro après rejet d'une
facture ; - payloads définitifs des événements fiscaux ; - émetteur
définitif de `payment.received` ; - délais et paliers de relance.

Ne pas transformer les valeurs provisoires en règles officielles sans
validation.

### Priorité 3 --- intégrations intermodules

-   confirmer et implémenter les contrats avec DGI, Stocks et Trésorerie
    ;
-   vérifier les événements et les payloads ;
-   ajouter des tests contractuels et d'intégration.

### Priorité 4 --- packaging et publication

-   vérifier l'inclusion de `sales_settings.json` dans un build
    installable ;
-   vérifier la documentation et les chemins ;
-   contrôler les fichiers suivis/non suivis et exclure les sauvegardes
    ;
-   exécuter les validations finales avant publication.

## 10. Procédure de reprise pour le prochain agent

1.  Lire ce `HANDOFF.md`.
2.  Lire `docs/CORE_DOCUMENTATION.md`.
3.  Lire `docs/VENTES_DOCUMENTATION.md`.
4.  Examiner `git status --short`, la branche et les différences.
5.  Activer l'environnement virtuel.
6.  Lancer les tests avant modification.
7.  Identifier précisément les fichiers autorisés.
8.  Inspecter les interfaces et tests existants avant de modifier un
    service.
9.  Préserver les sauvegardes et éviter les scripts générateurs
    destructifs.
10. Modifier par petits lots et relancer les tests ciblés, Ruff et Mypy.
11. Rapporter les commandes exactes et leurs sorties, sans
    extrapolation.

## 11. Dernière étape connue

Au moment de cette mise à jour, le projet est en préparation de
publication sur GitHub (`origin` pointant vers
`https://github.com/Taigacapitall/ERP-PROJECT.git`, branche `main`). La
préparation de l'index Git avait été commencée, mais le push n'était pas
encore confirmé.

Avant de poursuivre, vérifier l'état réel :

``` bash
git status --short
git diff --cached --stat
git diff --cached --check
```

Ne pas supposer que l'index Git est encore dans le même état qu'au
dernier échange. Exclure les sauvegardes, fichiers temporaires et
environnements virtuels. Le commit et le push nécessitent l'autorisation
explicite de l'utilisateur.

------------------------------------------------------------------------

**Fin du document de transmission.**
