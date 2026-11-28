"""Tests de la base de contrôle (brique B1) : schéma, unicités, migrations, sur SQLite et PostgreSQL.

Les tests PostgreSQL utilisent VORPAL_TEST_DATABASE_URL (définie par la CI) ; ils sont ignorés
si la variable est absente. Chaque test PostgreSQL travaille dans un schéma temporaire.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vorpal.plateforme.control import migrate
from vorpal.plateforme.control.models import (
    Abonnement,
    Appareil,
    Appartenance,
    CleIdempotence,
    ControlBase,
    Entreprise,
    OctroiSupport,
    Role,
    Utilisateur,
)

EXPECTED_TABLES = {
    "entreprises", "utilisateurs", "roles", "appartenances", "appareils", "sessions",
    "tentatives_connexion", "octrois_support", "taches", "sauvegardes", "journal_controle",
    "cles_idempotence", "abonnements", "journal_licences",
}


def _pg_url() -> str | None:
    url = os.environ.get("VORPAL_TEST_DATABASE_URL")
    return url.replace("postgresql://", "postgresql+psycopg://", 1) if url else None


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = create_engine(f"sqlite:///{tmp_path / 'control.db'}")

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        yield eng
        eng.dispose()
        return
    url = _pg_url()
    if url is None:
        pytest.skip("VORPAL_TEST_DATABASE_URL non définie (tests PostgreSQL ignorés)")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    yield eng
    eng.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    migrate.upgrade(engine)
    return engine


def _entreprise(slug: str = "acme") -> Entreprise:
    return Entreprise(slug=slug, nom=slug.upper())


def _user(email: str = "a@example.com") -> Utilisateur:
    return Utilisateur(email=email, nom="A")


def test_migration_creates_expected_tables_and_version(engine: Engine) -> None:
    migrate.upgrade(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    assert migrate.current_revision(engine) == "0001"


def test_migration_down_then_up(engine: Engine) -> None:
    migrate.upgrade(engine)
    migrate.downgrade(engine)
    assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
    assert migrate.current_revision(engine) is None
    migrate.upgrade(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_migration_matches_models(migrated: Engine) -> None:
    """Le schéma issu de la migration doit être identique à celui des modèles
    (tables, colonnes, valeurs par défaut serveur, et types sur PostgreSQL)."""
    opts: dict[str, Any] = {
        "compare_server_default": True,
        "compare_type": migrated.dialect.name == "postgresql",
    }
    with migrated.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn, opts=opts), ControlBase.metadata)
    assert diffs == [], diffs


def test_create_all_matches_expected_tables(engine: Engine) -> None:
    ControlBase.metadata.create_all(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_reserved_and_subscription_columns_exist(migrated: Engine) -> None:
    insp = inspect(migrated)
    assert "site_id" in {c["name"] for c in insp.get_columns("appartenances")}
    assert "site_id" in {c["name"] for c in insp.get_columns("appareils")}
    assert {"statut", "echeance_le", "delai_grace_jours"} <= {
        c["name"] for c in insp.get_columns("abonnements")
    }
    assert "device_id" in {c["name"] for c in insp.get_columns("appareils")}
    assert "journal_licences" in insp.get_table_names()


def _seed(session: Session) -> tuple[Entreprise, Utilisateur, Role]:
    e, u = _entreprise(), _user()
    r = Role(entreprise_id=None, code="lecteur", nom="Lecteur", permissions=["ventes.view"])
    session.add_all([e, u, r])
    session.flush()
    return e, u, r


def _duplicate_pair(name: str, e: Entreprise, u: Utilisateur, r: Role) -> tuple[list[Any], Any]:
    """(éléments valides à insérer d'abord, doublon qui doit être refusé)."""
    soon = datetime.now(UTC) + timedelta(days=1)
    if name == "slug":
        return [], _entreprise("acme")
    if name == "email":
        return [], _user("a@example.com")
    if name == "appartenance":
        return (
            [Appartenance(utilisateur_id=u.id, entreprise_id=e.id, role_id=r.id)],
            Appartenance(utilisateur_id=u.id, entreprise_id=e.id, role_id=r.id),
        )
    if name == "appareil":
        return (
            [Appareil(utilisateur_id=u.id, device_id="D1")],
            Appareil(utilisateur_id=u.id, device_id="D1"),
        )
    if name == "abonnement":
        return [Abonnement(entreprise_id=e.id)], Abonnement(entreprise_id=e.id)
    return (  # cle_idempotence
        [CleIdempotence(entreprise_id=e.id, cle="K1", empreinte_requete="x", expire_le=soon)],
        CleIdempotence(entreprise_id=e.id, cle="K1", empreinte_requete="y", expire_le=soon),
    )


@pytest.mark.parametrize(
    "name", ["slug", "email", "appartenance", "appareil", "abonnement", "cle_idempotence"]
)
def test_unique_constraints(migrated: Engine, name: str) -> None:
    with Session(migrated) as session:
        e, u, r = _seed(session)
        first, duplicate = _duplicate_pair(name, e, u, r)
        session.add_all(first)
        session.flush()
        session.expunge_all()  # le doublon ne doit pas entrer en conflit avec l'identity map
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.flush()


def test_check_constraints(migrated: Engine) -> None:
    now = datetime.now(UTC)
    with Session(migrated) as session:
        e, u, _ = _seed(session)
        bad: list[Any] = [
            Entreprise(slug="x", nom="X", statut="inconnu"),
            Utilisateur(email="Maj@Example.com", nom="M"),
            Abonnement(entreprise_id=e.id, statut="gratuit"),
            OctroiSupport(entreprise_id=e.id, accorde_par=u.id, beneficiaire_id=u.id,
                          debut_le=now, fin_le=now - timedelta(hours=1)),
        ]
        for item in bad:
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(item)
                session.flush()


def test_foreign_keys_are_enforced(migrated: Engine) -> None:
    with Session(migrated) as session:
        _seed(session)
        session.add(Appareil(utilisateur_id="inexistant", device_id="D9"))
        with pytest.raises(IntegrityError):
            session.flush()
