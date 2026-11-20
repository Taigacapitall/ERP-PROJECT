# Vorpal Core : documentation technique de référence

> Document évolutif. Il décrit **ce qui existe dans le code**, vérifié sur le commit `8d470b7`
> (`origin/main`, 2026-11-20). Les blocs ```` ```python ```` sont exécutés automatiquement pour
> vérifier qu'ils fonctionnent. Voir [section 11](#11-maintenir-cette-documentation).

**Sommaire** : [1. Présentation](#1-présentation) · [2. Architecture](#2-architecture-et-organisation) ·
[3. Composants](#3-composants-techniques) · [4. Principes de conception](#4-principes-de-conception) ·
[5. Base de données](#5-base-de-données) · [6. Tests et qualité](#6-tests-et-qualité) ·
[7. Utilisation](#7-utilisation-du-core) · [8. État de maturité](#8-état-de-maturité) ·
[9. Guide développeur](#9-guide-pour-les-futurs-développeurs) · [10. Historique](#10-historique-et-décisions-darchitecture) ·
[11. Maintenance du document](#11-maintenir-cette-documentation)

---

## 1. Présentation

### 1.1 Rôle du Core
`vorpal/core` est le noyau de Vorpal Business Management. Il fournit les briques communes qui permettent à des
modules métier (DGI, compta Odoo, RH/paie…) de s'enregistrer, de communiquer et de partager des types, **sans se
connaître entre eux**. Spécification de référence : `01_MODULE_BASE_CORE.md`.

### 1.2 Principes
- **Modules découplés** : un module n'importe jamais un autre module, seulement `vorpal.core`. Il communique par
  **événements** (`EventBus`) ou par **services** (`ServiceRegistry`) dont les interfaces vivent dans `core/contracts`.
- **Montants en `Decimal` uniquement** (jamais de `float`), via `Money`.
- **Injection de dépendances** par constructeur et par `AppContext` ; aucun accès global métier.
- **Déclaratif pour la configuration, impératif pour la logique** (spec 00).

### 1.3 Ce qui appartient au Core, et ce qui n'y appartient pas
| Dans le Core | Hors Core (modules) |
|---|---|
| Money, devises, taux de change en mémoire | Règles fiscales DGI, groupes de TVA, client du dispositif fiscal |
| Cycle de vie des modules, bus d'événements, registre de services | Adaptateur Odoo, plan comptable SYSCOHADA |
| Persistance générique (Repository, UnitOfWork, versions de schéma) | Modèles de données métier, barèmes de paie |
| Audit chaîné, autorisation, configuration, erreurs | Implémentations des Protocols (`FiscalCertifier`, `AccountingPoster`) |
| DTO partagés, catalogue d'événements, Protocols | Ventes, achats, stocks, RH |

Aucune implémentation des Protocols n'existe dans le Core : ils décrivent ce que les modules fournissent.

---

## 2. Architecture et organisation

### 2.1 Arborescence
```text
vorpal/core/
  __init__.py        ré-exporte l'API publique (voir __all__)
  errors.py          hiérarchie d'erreurs VorpalError
  money.py           Currency, Money, ExchangeRateProvider, InMemoryRateProvider, convert
  module.py          ModuleState, ModuleManifest, BaseModule (ABC)
  registry.py        ModuleRegistry : tri topologique, démarrage, arrêt
  context.py         AppContext (injecté dans les modules), utc_clock
  events.py          Event, EventStore, InMemoryEventStore, EventBus
  services.py        ServiceRegistry
  db.py              Base, Repository, SqlAlchemyRepository, UnitOfWork, SchemaMigrationManager, create_db_engine
  audit.py           AuditEntry, AuditLog (chaîne SHA-256)
  config.py          AppConfig, parseur YAML minimal
  security.py        Permission, Role, User, AuthorizationService
  partners.py        Partner
  contracts/
    dto.py           DTO pydantic (facture, écriture, paie)
    events.py        constantes du catalogue d'événements
    protocols.py     Protocols entre modules
tests/core/          7 fichiers, 74 tests
```

### 2.2 Dépendances internes (vérifiées par analyse des imports : aucun cycle)
```text
errors, security, partners, audit, contracts.events      aucune dépendance interne
money      -> errors
config     -> errors (+ pydantic)
services   -> errors
db         -> errors (+ SQLAlchemy)
events     -> contracts.events
contracts.dto       -> money
contracts.protocols -> contracts.dto, money
module     -> context (uniquement sous TYPE_CHECKING)
context    -> audit, config, events, security, services (+ SQLAlchemy Engine)
registry   -> context, errors, module
```
Le Core et `contracts` se référencent dans un seul sens à chaque fois : `events` importe `contracts.events`,
et `contracts.dto/protocols` importent `money`.

### 2.3 Flux d'exécution typique
1. L'application construit un `AppContext` (config, engine, bus, registre de services, audit, authz). **Il n'existe
   pas de fonction d'amorçage** : l'assemblage est manuel (section 7.1).
2. Les modules sont enregistrés dans un `ModuleRegistry`, puis `start_all(ctx)` : tri topologique selon
   `manifest.depends` ; pour chaque module, déclaration des permissions, `install(ctx)`, état `INSTALLED`,
   `start(ctx)`, état `RUNNING`. Une exception met le module en `FAILED` ; ses dépendants sont `FAILED` sans être
   démarrés ; les autres continuent.
3. Un module `start` s'abonne aux événements et/ou enregistre des services ; les autres modules les utilisent via
   `ctx.event_bus` et `ctx.services`.
4. `stop_all()` arrête les modules démarrés dans l'ordre inverse du démarrage.

---

## 3. Composants techniques

### 3.1 Erreurs (`errors.py`)
- **Objectif** : un type d'erreur commun, filtrable par les modules.
- **Classes** : `VorpalError(message, code=None)` avec attributs `message` et `code` (par défaut le nom de la
  classe). Sous-classes : `ValidationError`, `ConflictError`, `NotFoundError`, `ExternalServiceError`,
  `ComplianceError`.
- **Usage dans le Core** : `ValidationError` (valeur invalide), `ConflictError` (doublon, cycle),
  `NotFoundError` (module, service, taux introuvable).
- **Point d'attention** : les DTO pydantic lèvent `pydantic.ValidationError`, **pas** `vorpal.core.errors.ValidationError`
  (deux types distincts du même nom).

### 3.2 Configuration (`config.py`)
- **Classe** : `AppConfig` (pydantic `BaseModel`). Champs : `env` (`dev|test|staging|prod`, défaut `dev`),
  `db_url` (défaut `sqlite:///vorpal.db`), `log_level` (chaîne, non validée, défaut `INFO`), `default_currency`
  (`USD|CDF`, défaut `USD`), `secrets` (`dict[str, str]`, `repr=False`).
- **`AppConfig.load(config_path=None, env_prefix="VORPAL_")`** :
  1. lit le fichier s'il est fourni : `.json`, `.yaml`, `.yml` (autre extension : `ValidationError`) ; fichier
     absent, illisible, JSON malformé ou racine non objet : `ValidationError` ;
  2. applique les variables d'environnement `VORPAL_ENV`, `VORPAL_DB_URL`, `VORPAL_LOG_LEVEL`,
     `VORPAL_DEFAULT_CURRENCY` (une valeur vide est ignorée ; l'environnement prime sur le fichier) ;
  3. fusionne les secrets : `VORPAL_SECRET_<NOM>` devient `secrets["<nom en minuscules>"]`, prioritaire sur le fichier ;
  4. valide ; en cas d'erreur le message ne contient que le champ et la raison, **jamais la valeur saisie**.
- **Limites** : parseur YAML maison (`_parse_simple_yaml`) sans listes, sections imbriquées ni `#` dans une valeur ;
  les clés inconnues d'un fichier sont **ignorées silencieusement** ; les secrets sont masqués dans `repr()`/`str()`
  mais **présents dans `model_dump()`** (vérifié) ; aucune lecture d'un coffre-fort, aucun chiffrement.

### 3.3 Money et devises (`money.py`)
- **`Currency`** (`str, Enum`) : `USD`, `CDF`. `CURRENCY_DECIMALS` : 2 décimales pour les deux.
- **`Money(amount, currency)`** : dataclass **immuable** et hachable. `amount` est normalisé en `Decimal` :
  acceptés `Decimal`, `int`, `str` (espaces retirés) ; refusés (`ValidationError`) : `float`, `bool`, `NaN`,
  `Infinity`, texte non numérique, autre type. `currency` doit être un `Currency`.
- **Opérations** : `+` et `-` (même devise, sinon `ValidationError`) ; `*` par `Decimal`/`int`/`str` (float ou
  autre `Money` refusés) y compris `3 * money` ; négation ; `< <= > >=` (même devise). Pas de division, ni
  `abs`, ni `__radd__` (donc `sum(liste_de_money)` exige `sum(liste, Money(0, devise))`).
  Les résultats ne sont **pas arrondis automatiquement** : `round(decimals=None, rounding=ROUND_HALF_UP)`.
- **Taux de change** : `ExchangeRateProvider` (ABC, `get_rate(from, to, on_date)`) ;
  `InMemoryRateProvider` : `set_rate` (taux > 0 exigé), `get_rate` (même devise = 1, taux direct, sinon inverse
  `1/taux`, sinon `NotFoundError`). `convert(money, to, on_date, provider, round_result=True)`.

### 3.4 Modules et registre (`module.py`, `registry.py`)
- **`ModuleManifest`** (dataclass figée) : `name`, `version`, `depends`, `provides`, `subscribes`, `permissions`.
- **`BaseModule`** (ABC) : attribut `manifest` ; méthodes abstraites `install(ctx)`, `start(ctx)`, `stop()`,
  `healthcheck()`.
- **`ModuleRegistry`** : `register(module)` (nom vide : `ValidationError` ; doublon : `ConflictError`),
  `get_state(name)` (`NotFoundError` si inconnu), `resolve_order()` (dépendance manquante : `NotFoundError` ;
  cycle : `ConflictError`), `start_all(ctx)`, `stop_all()`. États : `REGISTERED, INSTALLED, RUNNING, STOPPED, FAILED`.
- **Limites** : le registre **n'appelle jamais `healthcheck()`** et n'utilise pas `provides` ni `subscribes`
  (informatifs). Il n'y a pas de chargement dynamique de modules : on enregistre des instances. Un module dont
  `start` échoue après `install` n'est pas arrêté.

### 3.5 AppContext (`context.py`)
Dataclass figée : `event_bus`, `services`, `config`, `db` (Engine SQLAlchemy), `audit`, `authz`,
`clock` (défaut `utc_clock`, datetime UTC), `logger` (défaut `logging.getLogger("vorpal")`).

### 3.6 Événements (`events.py`, `contracts/events.py`)
- **`Event`** (figé) : `type`, `payload`, `id` (uuid4), `occurred_at` (UTC), `correlation_id`, `source_module`.
- **`EventBus(store=None, logger=None)`** : synchrone. `subscribe(type, handler)` (le même handler n'est pas
  ajouté deux fois ; `"*"` reçoit tous les types) ; `publish(event)` (stocke dans le `EventStore` s'il existe,
  puis distribue) ; `replay(event_type=None) -> int`.
- **Garanties** : un handler en erreur n'arrête pas les autres ; l'erreur est journalisée et publiée sous
  `event.handler_failed` (payload : `failed_event_id`, `failed_event_type`, `handler_name`, `error`) sauf si
  l'événement en échec est lui-même `handler_failed` ; un handler **réussi** ne reçoit pas deux fois le même
  `event.id` ; un handler **en échec** n'est pas marqué traité et sera réessayé à la republication.
- **`replay`** rejoue les événements stockés vers **tous** les handlers, y compris ceux qui avaient réussi
  (il efface les marques de traitement) ; retourne 0 sans `EventStore`.
- **`EventStore`** (ABC : `append`, `list_events`) ; `InMemoryEventStore` ignore un `id` déjà stocké.
- **Catalogue** (`contracts/events.py`) : `OFFICIAL_EVENTS` = 7 événements métier (`invoice.ready_for_certification`,
  `invoice.certified`, `invoice.rejected`, `credit_note.certified`, `journal.entry.posted`, `payroll.validated`,
  `payment.received`). `EVENT_HANDLER_FAILED` est un événement **système**, hors de ce tuple.
- **Limites** : mémoire seulement ; non thread-safe ; l'ensemble des événements traités n'est jamais purgé.

### 3.7 Services (`services.py`)
`ServiceRegistry` : `register(interface, impl, replace=False)` (`interface` doit être un type ; doublon sans
`replace` : `ConflictError`), `get(interface)` (`NotFoundError`), `get_optional(interface)` (`None` si absent).
**Aucune vérification que `impl` respecte `interface`** : c'est au module de le garantir.

### 3.8 Audit (`audit.py`)
`AuditLog` : journal en mémoire, append-only, chaîné. `record(user, action, entity, entity_id, before=None,
after=None, timestamp=None) -> AuditEntry` ; `entries` (tuple) ; `verify_chain() -> bool`.
Chaque entrée porte `seq` (à partir de 1), `prev_hash` (64 zéros pour la première) et `entry_hash` = SHA-256
d'un JSON canonique (clés triées). `before`/`after` sont **copiés en profondeur** à l'enregistrement.
- **Limites** : pas de persistance (A_VALIDER) ; `entries` et la valeur retournée par `record()` exposent les
  dictionnaires internes (une modification par le lecteur casse la chaîne) ; non thread-safe.

### 3.9 Sécurité (`security.py`)
`Permission`, `Role(name, permissions)`, `User(id, username, roles, direct_permissions, is_active)` (figés) ;
`User.effective_permissions()` (vide si inactif). `AuthorizationService` : `register_module_permissions(module,
liste)`, `is_permission_declared(code)`, `check(user, permission) -> bool`.
**Hors périmètre actuel** : authentification, mots de passe, sessions, persistance des utilisateurs et rôles.
`check` ne vérifie pas que la permission est déclarée. La gestion des secrets se limite à `AppConfig.secrets`.

### 3.10 Partenaires (`partners.py`)
`Partner(id, name, partner_type, nif, rccm, address)` : dataclass figée, `partner_type` ∈ `"client" |
"fournisseur" | "employe"` (**sans accent**, alors que la spec écrit « employé »). Ni validation ni persistance.

### 3.11 DTO et contrats (`contracts/dto.py`, `contracts/protocols.py`)
Montants typés `StrictDecimal` : `float` et `bool` rejetés. Les DTO sont des modèles pydantic.
| DTO | Contrôles réels |
|---|---|
| `PartyInfoDTO` | `name` non vide ; `nif`, `rccm`, `address` optionnels |
| `InvoiceLineDTO` | `designation` non vide ; `quantity` > 0 ; `unit_price_ht` ≥ 0 ; `vat_rate` ≥ 0 (convention des tests : fraction, `0.16`) ; `tax_group` défaut `"A"` ; `item_code` optionnel non vide |
| `InvoiceDTO` | ≥ 1 ligne ; `total_ttc == total_ht + total_vat` exactement ; `total_ht` à 0,01 × nb de lignes près de la somme des lignes ; un avoir exige `original_invoice_ref` ; `non_taxable_amount` et `other_taxes` ≥ 0, défaut 0 (simples porteurs, sans lien avec les totaux) |
| `CertifiedInvoiceDTO` | `InvoiceDTO` + `device_id`, `auth_code`, `qr_code` (non vides), `certified_at`, `counters` |
| `JournalLineDTO` / `JournalEntryDTO` | ligne : débit **ou** crédit > 0 ; écriture : ≥ 2 lignes, équilibrée **par devise** |
| `PayrollResultDTO` | montants ≥ 0 ; **aucune cohérence imposée** entre brut, retenues et net |

`protocols.py` : `FiscalCertifier.certify(InvoiceDTO) -> CertifiedInvoiceDTO`, `AccountingPoster.post(JournalEntryDTO)
-> str`, `ExchangeRateProviderProtocol.get_rate(...)`, tous `runtime_checkable`.
Les `TODO(A_VALIDER)` du fichier signalent les règles fiscales non fixées (section 8).

---

## 4. Principes de conception
- **Abstraction** : ABC pour les points d'extension (`BaseModule`, `Repository`, `EventStore`,
  `ExchangeRateProvider`) ; `Protocol` pour les contrats entre modules.
- **SOLID** : responsabilités séparées par fichier ; ouvert à l'extension (nouvelle implémentation de
  `Repository`/`EventStore`/`ExchangeRateProvider` sans modifier le Core) ; les modules dépendent d'abstractions.
- **Injection de dépendances** : `EventBus(store=…)`, `SqlAlchemyRepository(session, modèle)`, `UnitOfWork(factory)`,
  `AppContext` ; pas de singleton. Réserve : `Base` (SQLAlchemy) et `CURRENCY_DECIMALS` sont des objets de module
  partagés.
- **Erreurs** : exceptions typées ; isolation des échecs de handlers et de modules.
- **Extensibilité** : un module ajoute capacités et événements sans toucher au Core, sauf pour un nouvel événement
  officiel (catalogue + `contracts/events.py`, règle de la spec 01).

---

## 5. Base de données
- **Technologies** : SQLAlchemy 2.x ; SQLite par défaut (`create_db_engine`, défaut `sqlite:///:memory:` avec
  `check_same_thread=False` pour SQLite).
- **Modèles existants** : le Core ne définit **aucun modèle ORM métier**. Il fournit `Base` (`DeclarativeBase`) que
  les modules étendent. La seule table du Core est `vorpal_schema_versions` (`module_name` String(128) clé primaire,
  `version` Integer non nul), créée par `SchemaMigrationManager` à son instanciation. Pas de relations entre entités.
- **Sessions et transactions** : `UnitOfWork(session_factory)` est un gestionnaire de contexte : session ouverte à
  l'entrée, `commit` en cas de succès, `rollback` sur exception, `close` toujours. `uow.session` est `None` en dehors.
- **Repository** : `Repository[T]` (ABC : `get`, `add`, `list_all`) ; `SqlAlchemyRepository(session, modèle,
  id_attr="id")` : `add` utilise `session.merge` (insertion ou mise à jour).
- **Migrations** : `SchemaMigrationManager(engine)` : `get_version(module)` (0 si inconnu) ;
  `apply(module, {version: fonction(engine)})` applique les versions manquantes **dans l'ordre**, sans saut
  (`ValidationError` sinon) et retourne la version atteinte. **Limite** : l'étape et l'enregistrement de la version
  ne sont pas dans une même transaction ; aucun outil de génération ni de retour arrière.
- **Contraintes** : seules celles que les modèles des modules déclarent. Toutes les tables des modules partagent
  `Base.metadata` : leurs noms de tables doivent être uniques.
- **Non fait** : `AuditLog` et `EventStore` ne sont pas persistés en base (A_VALIDER).

---

## 6. Tests et qualité
- **Organisation** (`tests/core/`) : `test_core.py` (8, intégration : registre, bus, deux modules, audit, DB),
  `test_config.py` (13), `test_money_extra.py` (15), `test_hardening.py` (15) et `test_core_hardening.py` (10)
  (durcissement ; **tests partiellement dupliqués**), `test_dto_contracts.py` (11), `test_audit_copy.py` (2).
- **Commandes** :
```bash
pip install -e ".[dev]"
pytest --cov=vorpal --cov-report=term-missing -q   # tests + couverture
ruff check .                                       # lint
mypy --strict vorpal                               # typage (le package, pas les tests)
```
- **Configuration ruff** (`pyproject.toml`) : règles `E4 E7 E9 F I B UP RUF TRY ISC`, `TRY003` et `UP042`
  ignorés ; E501 et `ruff format` non appliqués.
- **Résultats vérifiés sur `8d470b7`** (Python 3.12.3) : **74 tests réussis**, couverture **97 %** (743
  instructions, 25 non couvertes), `ruff check .` sans erreur, `mypy --strict vorpal` sans erreur (18 fichiers).
- **Lignes non couvertes** : `audit.py` 111 ; `context.py` 20 ; `dto.py` 71, 94, 96 ; `db.py` 41-42, 69, 105, 107 ;
  `events.py` 50, 128 ; `money.py` 65, 105, 111 ; `registry.py` 21, 23, 29, 99-101 ; `security.py` 28, 47 ;
  `services.py` 16, 26.
- **Reste à tester** : `SqlAlchemyRepository.get`, saut de version de migration, échec d'un `stop`,
  utilisateur inactif, `replay` sans store, validations de `JournalLineDTO` et d'avoir sans référence.

---

## 7. Utilisation du Core
Chaque bloc est autonome (à exécuter depuis la racine du dépôt, paquet installé).

### 7.1 Initialiser le Core
```python
from vorpal.core import (
    AppConfig, AppContext, AuditLog, AuthorizationService, Base, EventBus,
    InMemoryEventStore, ServiceRegistry, create_db_engine,
)

config = AppConfig(env="test", db_url="sqlite:///:memory:")
engine = create_db_engine(config.db_url)
Base.metadata.create_all(engine)  # crée les tables des modèles déclarés sur Base
ctx = AppContext(
    event_bus=EventBus(store=InMemoryEventStore()),
    services=ServiceRegistry(),
    config=config,
    db=engine,
    audit=AuditLog(),
    authz=AuthorizationService(),
)
assert ctx.config.env == "test"
assert ctx.clock().tzinfo is not None
engine.dispose()
```

### 7.2 Charger la configuration
```python
import json
import os
import tempfile
from pathlib import Path

from vorpal.core import AppConfig

os.environ["VORPAL_SECRET_DGI_TOKEN"] = "tok-123"
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "vorpal.json"
    path.write_text(json.dumps({"env": "staging", "default_currency": "CDF"}), encoding="utf-8")
    cfg = AppConfig.load(path)  # le fichier, puis les variables d'environnement VORPAL_*
assert cfg.env == "staging" and cfg.default_currency == "CDF"
assert cfg.secrets["dgi_token"] == "tok-123"
assert "tok-123" not in repr(cfg)               # masqué dans repr/str
assert "tok-123" in str(cfg.model_dump())       # attention : présent dans model_dump()
```

### 7.3 Base de données : modèle, transaction, migrations
```python
from sqlalchemy import Column, String
from sqlalchemy.orm import sessionmaker

from vorpal.core import Base, SchemaMigrationManager, SqlAlchemyRepository, UnitOfWork, create_db_engine


class Product(Base):
    __tablename__ = "doc_products"
    id = Column(String(32), primary_key=True)
    label = Column(String(128), nullable=False)


engine = create_db_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine)

with UnitOfWork(factory) as uow:  # commit à la sortie
    SqlAlchemyRepository(uow.session, Product).add(Product(id="P1", label="Sac"))

try:
    with UnitOfWork(factory) as uow:  # rollback sur exception
        SqlAlchemyRepository(uow.session, Product).add(Product(id="P2", label="X"))
        raise RuntimeError("boom")
except RuntimeError:
    pass

with UnitOfWork(factory) as uow:
    repo = SqlAlchemyRepository(uow.session, Product)
    assert repo.get("P1").label == "Sac"
    assert repo.get("P2") is None

mgr = SchemaMigrationManager(engine)
assert mgr.get_version("products") == 0
assert mgr.apply("products", {1: lambda eng: None, 2: lambda eng: None}) == 2
engine.dispose()
```

### 7.4 Enregistrer et récupérer un service
```python
from typing import Protocol, runtime_checkable

from vorpal.core import NotFoundError, ServiceRegistry


@runtime_checkable
class Greeter(Protocol):
    def greet(self, name: str) -> str: ...


class FrenchGreeter:
    def greet(self, name: str) -> str:
        return f"Bonjour {name}"


services = ServiceRegistry()
services.register(Greeter, FrenchGreeter())
assert services.get(Greeter).greet("Vorpal") == "Bonjour Vorpal"
assert services.get_optional(dict) is None  # service absent : comportement dégradé à prévoir
try:
    services.get(dict)
except NotFoundError:
    pass
```

### 7.5 Publier et écouter un événement
```python
import logging

from vorpal.core import Event, EventBus, InMemoryEventStore
from vorpal.core.contracts.events import EVENT_HANDLER_FAILED, INVOICE_CERTIFIED

logging.disable(logging.CRITICAL)  # évite le bruit des erreurs volontaires de l'exemple
bus = EventBus(store=InMemoryEventStore())
seen, failures = [], []


def broken(ev: Event) -> None:
    raise RuntimeError("boom")


bus.subscribe(INVOICE_CERTIFIED, lambda ev: seen.append(ev.payload["number"]))
bus.subscribe(INVOICE_CERTIFIED, broken)
bus.subscribe(EVENT_HANDLER_FAILED, lambda ev: failures.append(ev.payload["failed_event_type"]))

ev = Event(type=INVOICE_CERTIFIED, payload={"number": "F-1"}, source_module="dgi")
bus.publish(ev)
bus.publish(ev)
assert seen == ["F-1"]                           # idempotence : traité une seule fois
assert failures == [INVOICE_CERTIFIED] * 2       # le handler en échec est réessayé, chaque échec signalé
```

### 7.6 Montants et devises
```python
from datetime import date
from decimal import Decimal

from vorpal.core import Currency, InMemoryRateProvider, Money, ValidationError, convert

price = Money(Decimal("100.00"), Currency.USD)
total = price * Decimal("1.16")
assert total.amount == Decimal("116.0000") and total.round().amount == Decimal("116.00")

for bad in (lambda: Money(10.5, Currency.USD), lambda: price + Money(Decimal(1), Currency.CDF)):
    try:
        bad()
    except ValidationError:
        pass
    else:
        raise AssertionError("aurait dû être refusé")

provider = InMemoryRateProvider()
provider.set_rate(Currency.USD, Currency.CDF, date(2026, 11, 20), Decimal("2850.50"))
assert convert(Money(Decimal("2.00"), Currency.USD), Currency.CDF, date(2026, 11, 20), provider) == Money(
    Decimal("5701.00"), Currency.CDF
)
```

### 7.7 DTO de facture et d'écriture
```python
from datetime import date
from decimal import Decimal

from pydantic import ValidationError as PydanticValidationError

from vorpal.core import Currency
from vorpal.core.contracts import (
    InvoiceDTO, InvoiceLineDTO, JournalEntryDTO, JournalLineDTO, PartyInfoDTO,
)

line = InvoiceLineDTO(
    designation="Service", quantity=Decimal(1), unit_price_ht=Decimal("100.00"),
    vat_rate=Decimal("0.16"), item_code="ART-001",
)
invoice = InvoiceDTO(
    number="FAC-001", invoice_date=date(2026, 11, 20),
    seller=PartyInfoDTO(name="Vendeur", nif="NIF-V", rccm="CD/LSH/1", address="Lubumbashi"),
    customer=PartyInfoDTO(name="Client", nif="NIF-C"),
    lines=[line], currency=Currency.USD,
    total_ht=Decimal("100.00"), total_vat=Decimal("16.00"), total_ttc=Decimal("116.00"),
)
try:
    InvoiceDTO.model_validate({**invoice.model_dump(), "total_ttc": Decimal("120.00")})
    raise AssertionError("aurait dû être refusé")
except PydanticValidationError:
    pass  # totaux incohérents : erreur pydantic, pas vorpal.core.ValidationError

entry = JournalEntryDTO(
    entry_date=date(2026, 11, 20), journal="VT", label="Vente FAC-001", source_ref="FAC-001",
    lines=[
        JournalLineDTO(account="411100", debit=Decimal("116.00"), currency=Currency.USD),
        JournalLineDTO(account="701100", credit=Decimal("100.00"), currency=Currency.USD),
        JournalLineDTO(account="443100", credit=Decimal("16.00"), currency=Currency.USD),
    ],
)
assert len(entry.lines) == 3
```

### 7.8 Journal d'audit
```python
from vorpal.core import AuditLog

audit = AuditLog()
after = {"status": "CERTIFIED"}
audit.record("admin", "CERTIFY", "invoice", "INV-1", before={"status": "DRAFT"}, after=after)
after["status"] = "HACKED"  # modification par l'appelant : sans effet sur le journal
assert audit.verify_chain() is True
assert audit.entries[0].after == {"status": "CERTIFIED"}
```

### 7.9 Un module complet : deux modules qui communiquent sans s'importer
```python
from vorpal.core import (
    AppConfig, AppContext, AuditLog, AuthorizationService, BaseModule, Event, EventBus,
    InMemoryEventStore, ModuleManifest, ModuleRegistry, ModuleState, ServiceRegistry, create_db_engine,
)


class PingService:
    name = "service-ping"


class Ping(BaseModule):
    manifest = ModuleManifest(name="ping", version="1.0", permissions=["ping.use"])

    def install(self, ctx: AppContext) -> None:
        ctx.services.register(PingService, PingService())

    def start(self, ctx: AppContext) -> None:
        ctx.event_bus.subscribe("ping.requested", lambda ev: ctx.event_bus.publish(Event(type="ping.done")))

    def stop(self) -> None: ...
    def healthcheck(self) -> bool:
        return True


class Listener(BaseModule):
    manifest = ModuleManifest(name="listener", version="1.0", depends=["ping"])
    got: list[str] = []

    def install(self, ctx: AppContext) -> None: ...
    def start(self, ctx: AppContext) -> None:
        ctx.event_bus.subscribe("ping.done", lambda ev: self.got.append(ctx.services.get(PingService).name))

    def stop(self) -> None: ...
    def healthcheck(self) -> bool:
        return True


engine = create_db_engine()
ctx = AppContext(
    event_bus=EventBus(store=InMemoryEventStore()), services=ServiceRegistry(), config=AppConfig(),
    db=engine, audit=AuditLog(), authz=AuthorizationService(),
)
registry = ModuleRegistry()
registry.register(Listener())  # ordre d'enregistrement sans importance
registry.register(Ping())
assert registry.resolve_order() == ["ping", "listener"]
registry.start_all(ctx)
assert registry.get_state("listener") == ModuleState.RUNNING
ctx.event_bus.publish(Event(type="ping.requested"))
assert Listener.got == ["service-ping"]
assert ctx.authz.is_permission_declared("ping.use")
registry.stop_all()
assert registry.get_state("ping") == ModuleState.STOPPED
engine.dispose()
```

---

## 8. État de maturité

| Composant | Implémenté | Testé | Remarques |
|---|---|---|---|
| Money, Currency, taux en mémoire, `convert` | oui | oui | arrondi `ROUND_HALF_UP`, 2 décimales : règles par devise A_VALIDER |
| ModuleRegistry, BaseModule | oui | oui | `healthcheck`, `provides`, `subscribes` non exploités ; pas de chargement dynamique |
| EventBus, EventStore mémoire | oui | oui | non thread-safe, ensemble des événements traités jamais purgé |
| ServiceRegistry | oui | oui | pas de vérification de conformité à l'interface |
| AuditLog | oui, en mémoire | oui | persistance en base absente (A_VALIDER) |
| Config | oui | oui | YAML minimal ; secrets visibles via `model_dump()` |
| DB : Repository, UnitOfWork, migrations | oui | en grande partie | `get`, saut de version non testés ; étape de migration non atomique |
| Sécurité | partiel (autorisation seule) | oui | pas d'authentification, de persistance, de secrets chiffrés |
| DTO, Protocols, catalogue d'événements | oui | en grande partie | voir TODO ci-dessous |
| Partner | oui | minimal | `employe` sans accent, pas de persistance |
| Bootstrap / assemblage du Core | **absent** | – | `AppContext` assemblé à la main |
| Persistance de `AuditLog` / `EventStore` | **absente** | – | A_VALIDER |
| Authentification, bus asynchrone, thread-safety | **absents** | – | non prévus à ce jour |

**Points A_VALIDER ouverts** : persistance DB de `AuditLog`/`EventStore` ; règles d'arrondi par devise ; recoupement de
`total_vat` avec les lignes (module DGI) ; lien entre `non_taxable_amount`/`other_taxes` et les totaux ; contre-valeur
en devise société sur `JournalLineDTO` (étape 3, Odoo).

**Décisions à réexaminer** : deux `ValidationError` homonymes ; `employe` vs `employé` ; cohérence net/brut de
`PayrollResultDTO` ; `replay` qui rejoue aussi les handlers déjà réussis ; protection en lecture de l'audit ;
tests dupliqués.

---

## 9. Guide pour les futurs développeurs
1. **Lire** `HANDOFF.md` et la spec du module avant de coder.
2. **Dépendances** : un module n'importe que `vorpal.core` (jamais un autre module). Pour une donnée d'un autre
   module : définir un `Protocol` dans `core/contracts/protocols.py`, l'enregistrer dans `ServiceRegistry`, le lire avec
   `get_optional` et prévoir un comportement dégradé. Pour informer : publier un événement.
3. **Nouvel événement officiel** : l'ajouter au catalogue de la spec 01 **et** en constante dans
   `core/contracts/events.py` ; l'inclure dans `OFFICIAL_EVENTS` s'il est métier.
4. **Montants** : toujours `Money` / `Decimal`. Nouveaux DTO : utiliser `StrictDecimal`.
5. **Persistance** : étendre `Base`, noms de tables uniques, versionner via `SchemaMigrationManager`.
6. **Valeurs légales inconnues** : ne pas les inventer ; point d'extension configurable + `# TODO(A_VALIDER)` +
   ligne dans `HANDOFF.md`.
7. **Tests** : pytest, un fichier par composant dans `tests/<module>/`, couverture ≥ 85 % par module ; modules
   factices définis dans le test ; `engine.dispose()` pour tout moteur créé ; vérifier aussi les cas d'erreur.
8. **Avant de livrer** : `pytest --cov=vorpal -q`, `ruff check .`, `mypy --strict vorpal` propres, puis mettre à jour
   `HANDOFF.md` et ce document.

---

## 10. Historique et décisions d'architecture

### Versions
| Version | Référence | Contenu |
|---|---|---|
| Core V1 | commit `8d470b7` (tag `core-v1` : voir `HANDOFF.md`) | Core complet (spec 01), 74 tests, 97 %, ruff et mypy propres |

Commits : `f3fec7f` (démarrage), `d57e3d8` (stabilisation, tests), `8d470b7` (lots A et B : correctifs lint et typage,
fermeture des moteurs de test, copie défensive de l'audit, enrichissement des DTO, configuration ruff explicite).

### Décisions
- `Money` interdit `float` et le mélange de devises (spec 01).
- `EventBus` : isolation des handlers, idempotence par `(handler, event.id)`, réessai des échecs.
- `AuditLog` chaîné SHA-256 ; copie défensive de `before`/`after` (lot B).
- `AppContext` contient `authz` (absent de la liste de la spec 01, requis par la sécurité et le registre).
- `event.handler_failed` : événement système, hors `OFFICIAL_EVENTS`.
- `PyYAML` retiré des dépendances : parseur YAML minimal conservé (stack de la spec 00).
- DTO facture : `item_code`, `non_taxable_amount`, `other_taxes` ajoutés comme champs optionnels (besoin du module DGI).
- Ruff : jeu de règles explicite, car le jeu par défaut varie selon la version installée.
- Adaptateur Odoo : `vorpal/modules/adapters/odoo` (spec 00).

---

## 11. Maintenir cette documentation
- Mettre à jour ce document **dans le même commit** que toute modification importante du Core.
- Reporter chaque nouvelle décision en section 10 et chaque limite connue en section 8.
- N'affirmer que ce qui est vérifié : relancer les contrôles de la section 6 et mettre à jour les chiffres.
- Vérifier les exemples : `python docs/check_examples.py` exécute chaque bloc ```` ```python ```` de la section 7.
- Quand les modules DGI, RH/paie et Odoo existeront, ajouter une documentation par module qui renvoie à celle-ci.
