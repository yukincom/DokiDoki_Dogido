from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, TypeAlias


Position: TypeAlias = tuple[int, int, int]


class Medium(StrEnum):
    AIR = "air"
    WATER = "water"
    LAVA = "lava"
    SOLID = "solid"
    UNKNOWN = "unknown"
    MIXED = "mixed"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    NORTH = "north"
    SOUTH = "south"
    WEST = "west"
    EAST = "east"

    @property
    def offset(self) -> Position:
        return {
            Direction.UP: (0, 1, 0),
            Direction.DOWN: (0, -1, 0),
            Direction.NORTH: (0, 0, -1),
            Direction.SOUTH: (0, 0, 1),
            Direction.WEST: (-1, 0, 0),
            Direction.EAST: (1, 0, 0),
        }[self]


DIRECTIONS = tuple(Direction)
HORIZONTAL_DIRECTIONS = (
    Direction.NORTH,
    Direction.SOUTH,
    Direction.WEST,
    Direction.EAST,
)
OPPOSING_HORIZONTAL_PAIRS = (
    (Direction.NORTH, Direction.SOUTH),
    (Direction.WEST, Direction.EAST),
)


@dataclass(frozen=True, slots=True)
class BlockCell:
    medium: Medium
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class VoxelSnapshot:
    """認識器へ渡す有界のボクセル観測。

    観測に含まれない座標は空気と推測せず UNKNOWN とする。
    """

    cells: Mapping[Position, BlockCell]
    biome_tags: frozenset[str] = frozenset()
    known_structure_id: str | None = None

    def cell_at(self, position: Position) -> BlockCell:
        return self.cells.get(position, BlockCell(Medium.UNKNOWN))

    def medium_at(self, position: Position) -> Medium:
        return self.cell_at(position).medium


@dataclass(frozen=True, slots=True)
class RecognitionLimits:
    """複雑な装置や巨大建築を無限に追わないための上限。"""

    max_candidate_blocks: int = 4096
    max_group_blocks: int = 256
    max_extent_blocks: int = 32
    face_dominance_ratio: float = 0.75
    max_water_shell_depth: int = 4
    min_tunnel_ceiling_ratio: float = 0.75
    min_tunnel_length: int = 3
    min_tunnel_aspect_ratio: float = 1.5


@dataclass(frozen=True, slots=True)
class ObservedStructure:
    """会話と川柳が共有できる、用途未分化の観測結果。"""

    kind: str
    label_ja: str
    fact_ja: str
    question_terms: tuple[str, ...]
    certainty: str
    attributes: Mapping[str, object] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "label_ja": self.label_ja,
            "fact_ja": self.fact_ja,
            "question_terms": list(self.question_terms),
            "certainty": self.certainty,
            "attributes": dict(self.attributes),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    observation: ObservedStructure | None
    reason: str
    examined_blocks: int

    @property
    def recognized(self) -> bool:
        return self.observation is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "recognized": self.recognized,
            "reason": self.reason,
            "examined_blocks": self.examined_blocks,
            "observation": self.observation.to_dict() if self.observation else None,
        }


@dataclass(frozen=True, slots=True)
class ComponentExtraction:
    components: tuple[frozenset[Position], ...]
    oversized_components: int = 0
    truncated: bool = False
    reason: str = "ok"


def moved(position: Position, direction: Direction, distance: int = 1) -> Position:
    dx, dy, dz = direction.offset
    x, y, z = position
    return x + dx * distance, y + dy * distance, z + dz * distance
