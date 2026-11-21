"""Port de publication d'evenements du module Achats."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PurchaseEventPublisher(ABC):
    """Publie les evenements metier du module Achats vers le bus du Core."""

    @abstractmethod
    def publish_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        """Publie un evenement avec source_module='achats'."""
