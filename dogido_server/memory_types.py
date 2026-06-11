from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class HaikuEmission:
    created_at: datetime
    text: str
    preface: str | None
    interpretation: str | None
    biome: str | None
    structure: str | None
    time_phase: str | None
    dimension: str | None
    event_sequence: int | None
    route: str | None = "haiku"
