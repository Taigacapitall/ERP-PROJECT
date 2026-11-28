"""Contrat partagé des listes paginées (Core v1.1, ajout seulement).

Utilisé par l'API de la Plateforme ; aucun module existant ne dépend de ce fichier.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class SortSpec(BaseModel):
    """Un critère de tri : un nom de champ (identifiant simple) et un sens."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    direction: Literal["asc", "desc"] = "asc"


class PageRequest(BaseModel):
    """Demande de page : numéro à partir de 1, taille bornée, tris ordonnés."""

    model_config = ConfigDict(frozen=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    sort: tuple[SortSpec, ...] = ()

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    """Page de résultats : éléments, total global, position."""

    model_config = ConfigDict(frozen=True)

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)

    @property
    def pages(self) -> int:
        return -(-self.total // self.page_size)

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @classmethod
    def of(cls, items: list[T], total: int, request: PageRequest) -> Page[T]:
        return cls(items=items, total=total, page=request.page, page_size=request.page_size)
