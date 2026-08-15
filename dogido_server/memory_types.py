from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class HaikuLine:
    """表示と確定読みを同じ句行として保持する正本レコード。"""

    line_id: str
    line_index: int
    position: str
    canonical_name: str
    surface_text: str
    reading_text: str
    source_atom_ids: tuple[str, ...] = ()
    source_atoms: tuple[dict[str, Any], ...] = ()
    provenance: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_id": self.line_id,
            "line_index": self.line_index,
            "position": self.position,
            "canonical_name": self.canonical_name,
            "surface_text": self.surface_text,
            "reading_text": self.reading_text,
            "source_atom_ids": list(self.source_atom_ids),
            "source_atoms": [dict(source) for source in self.source_atoms],
            "provenance": self.provenance,
        }


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
    # text は従来の発話本文。表示表記・確定読み・出典は三つの行レコードを正本にする。
    surface_text: str | None = None
    reading_text: str | None = None
    lines: tuple[HaikuLine, ...] = ()

    def __post_init__(self) -> None:
        from dogido_server.haiku.verse import (
            build_haiku_lines,
            line_source_records,
            verse_reading_text,
            verse_surface_text,
        )

        if not self.lines:
            raw_sources = self.materials.get("line_sources")
            self.lines = build_haiku_lines(
                self.surface_text or self.text,
                line_sources=raw_sources if isinstance(raw_sources, list) else None,
            )
        if len(self.lines) == 3:
            self.surface_text = verse_surface_text(self.lines)
            self.reading_text = verse_reading_text(self.lines)
            synced_sources = line_source_records(self.lines)
            if synced_sources:
                self.materials["line_sources"] = synced_sources
        else:
            if self.surface_text is None:
                self.surface_text = self.text
            if self.reading_text is None:
                self.reading_text = self.text
