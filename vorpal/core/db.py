from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Generic, Self, TypeVar

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from vorpal.core.errors import ValidationError

TEntity = TypeVar("TEntity")


class Base(DeclarativeBase):
    pass


class Repository(ABC, Generic[TEntity]):
    @abstractmethod
    def get(self, entity_id: str) -> TEntity | None:
        """Récupère une entité par identifiant."""

    @abstractmethod
    def add(self, entity: TEntity) -> None:
        """Ajoute ou met à jour une entité."""

    @abstractmethod
    def list_all(self) -> list[TEntity]:
        """Liste toutes les entités."""


class SqlAlchemyRepository(Repository[TEntity]):
    def __init__(self, session: Session, model_cls: type[TEntity], id_attr: str = "id") -> None:
        self._session = session
        self._model_cls = model_cls
        self._id_attr = id_attr

    def get(self, entity_id: str) -> TEntity | None:
        col = getattr(self._model_cls, self._id_attr)
        return self._session.scalars(select(self._model_cls).where(col == entity_id)).first()

    def add(self, entity: TEntity) -> None:
        self._session.merge(entity)

    def list_all(self) -> list[TEntity]:
        return list(self._session.scalars(select(self._model_cls)).all())


class UnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> Self:
        self.session = self._session_factory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is not None:
                self.session.rollback()
            else:
                self.session.commit()
        finally:
            self.session.close()
            self.session = None


MigrationStep = Callable[[Engine], None]


class SchemaMigrationManager:
    """Migrations versionnées simples par module dans la table `vorpal_schema_versions`."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._metadata = MetaData()
        self._table = Table(
            "vorpal_schema_versions",
            self._metadata,
            Column("module_name", String(128), primary_key=True),
            Column("version", Integer, nullable=False),
        )
        self._metadata.create_all(self._engine)

    def get_version(self, module_name: str) -> int:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(self._table.c.version).where(self._table.c.module_name == module_name)
            ).first()
            return int(row[0]) if row is not None else 0

    def apply(self, module_name: str, steps: dict[int, MigrationStep]) -> int:
        current = self.get_version(module_name)
        for target_version in sorted(steps.keys()):
            if target_version <= current:
                continue
            if target_version != current + 1:
                raise ValidationError(
                    f"Saut de version de migration détecté pour {module_name}: {current} -> {target_version}."
                )
            steps[target_version](self._engine)
            with self._engine.begin() as conn:
                if current == 0:
                    conn.execute(
                        self._table.insert().values(module_name=module_name, version=target_version)
                    )
                else:
                    conn.execute(
                        self._table.update()
                        .where(self._table.c.module_name == module_name)
                        .values(version=target_version)
                    )
            current = target_version
        return current


def create_db_engine(db_url: str = "sqlite:///:memory:") -> Engine:
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args)
