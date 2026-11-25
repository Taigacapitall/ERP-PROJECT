"""Fixtures partagees des tests Stock (memoire uniquement : aucune dependance SQLAlchemy)."""

from __future__ import annotations

import pytest

from tests.stock.helpers import Env, build_env
from vorpal.modules.stock.adapters.persistence.memory_repositories import InMemoryStockUnitOfWork


@pytest.fixture
def env() -> Env:
    shared = InMemoryStockUnitOfWork()
    return build_env(lambda: shared)
