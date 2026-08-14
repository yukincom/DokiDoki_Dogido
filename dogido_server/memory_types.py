from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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
    # workshop pin 用。motifs / held / nearby / fragment_links 等（制御タグではない）
    materials: dict[str, Any] = field(default_factory=dict)
