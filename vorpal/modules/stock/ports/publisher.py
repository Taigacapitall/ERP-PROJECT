"""Port de publication d'evenements du module Stock."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StockEventPublisher(ABC):
    """Publie les evenements metier du module Stock vers le bus du Core."""

    @abstractmethod
    def publish_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        """Publie un evenement avec source_module='stock'."""
