from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PartnerType = Literal["client", "fournisseur", "employe"]


@dataclass(frozen=True)
class Partner:
    id: str
    name: str
    partner_type: PartnerType
    nif: str | None = None
    rccm: str | None = None
    address: str | None = None
