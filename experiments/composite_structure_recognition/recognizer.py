from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Iterable

from .model import (
    DIRECTIONS,
    HORIZONTAL_DIRECTIONS,
    OPPOSING_HORIZONTAL_PAIRS,
    ComponentExtraction,
    Direction,
    Medium,
    ObservedStructure,
    Position,
    RecognitionLimits,
    RecognitionResult,
    VoxelSnapshot,
    moved,
)


FREE_FACE_MEDIA = frozenset({Medium.AIR, Medium.WATER, Medium.LAVA})
OCEAN_MONUMENT_IDS = frozenset({"monument", "ocean_monument"})


def split_face_connected_components(
    positions: Iterable[Position],
    *,
    limits: RecognitionLimits | None = None,
) -> ComponentExtraction:
    """面で隣接するブロックを有界の塊へ分ける。

    入力候補自体が上限を超えた場合は、一部だけを構造物として
    判定せず全体を打ち切る。
    """

    resolved = limits or RecognitionLimits()
    remaining = set(positions)
    if len(remaining) > resolved.max_candidate_blocks:
        return ComponentExtraction(
            components=(),
            truncated=True,
            reason="candidate_block_limit_exceeded",
        )

    components: list[frozenset[Position]] = []
    oversized = 0
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        component = {start}
        while queue:
            current = queue.popleft()
            for direction in DIRECTIONS:
                neighbor = moved(current, direction)
                if neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                component.add(neighbor)
                queue.append(neighbor)
        if len(component) > resolved.max_group_blocks:
            oversized += 1
            continue
        components.append(frozenset(component))

    components.sort(key=lambda component: (min(component), len(component)))
    return ComponentExtraction(
        components=tuple(components),
        oversized_components=oversized,
        reason="oversized_component_omitted" if oversized else "ok",
    )


@dataclass(frozen=True, slots=True)
class _GroupGeometry:
    extent_x: int
    extent_y: int
    extent_z: int

    @property
    def max_extent(self) -> int:
        return max(self.extent_x, self.extent_y, self.extent_z)


class BlockGroupMeaningConverter:
    """有界のブロック群を、共通の構造観測へ変換する独立試作。"""

    def __init__(self, limits: RecognitionLimits | None = None) -> None:
        self.limits = limits or RecognitionLimits()

    def recognize_solid_group(
        self,
        group: Iterable[Position],
        snapshot: VoxelSnapshot,
    ) -> RecognitionResult:
        positions = frozenset(group)
        invalid = self._validate_group(positions)
        if invalid:
            return invalid
        if any(snapshot.medium_at(position) is not Medium.SOLID for position in positions):
            return RecognitionResult(None, "group_contains_non_solid", len(positions))

        faces = self._face_media(positions, snapshot)
        face_counts = Counter(faces.values())
        attributes = self._surface_attributes(positions, faces)

        # プレイヤーの定義: 六面がすべて空気の有界ブロック群は飛び石。
        if face_counts[Medium.AIR] == 6:
            return RecognitionResult(
                ObservedStructure(
                    kind="stepping_stone",
                    label_ja="飛び石",
                    fact_ja="近くに飛び石がある",
                    question_terms=("飛び石", "とびいし"),
                    certainty="high",
                    attributes=attributes,
                    evidence=("all_six_group_faces_are_air",),
                ),
                "recognized_stepping_stone",
                len(positions),
            )

        free_face_count = sum(face_counts[medium] for medium in FREE_FACE_MEDIA)
        connection_pair, deck_y = self._opposing_horizontal_connection_pair(
            positions,
            snapshot,
        )
        if (
            connection_pair is not None
            and free_face_count >= 4
            and face_counts[Medium.AIR] >= 1
        ):
            bridge_attributes = dict(attributes)
            bridge_attributes["connection_axis"] = (
                "north_south"
                if Direction.NORTH in connection_pair
                else "west_east"
            )
            bridge_attributes["deck_y"] = deck_y
            return RecognitionResult(
                ObservedStructure(
                    kind="bridge",
                    label_ja="橋",
                    fact_ja="近くに橋がある",
                    question_terms=("橋", "はし", "渡り道"),
                    certainty="high",
                    attributes=bridge_attributes,
                    evidence=(
                        "two_opposing_horizontal_connections",
                        "at_least_four_air_or_fluid_faces",
                        "at_least_one_air_face",
                    ),
                ),
                "recognized_bridge",
                len(positions),
            )

        return RecognitionResult(
            None,
            "solid_group_does_not_match_supported_structure",
            len(positions),
        )

    def recognize_air_group(
        self,
        group: Iterable[Position],
        snapshot: VoxelSnapshot,
    ) -> RecognitionResult:
        """海バイオーム中の有界空気成分から海中トンネル候補を判定する。"""

        positions = frozenset(group)
        invalid = self._validate_group(positions)
        if invalid:
            return invalid
        if any(snapshot.medium_at(position) is not Medium.AIR for position in positions):
            return RecognitionResult(None, "group_contains_non_air", len(positions))
        if "ocean" not in snapshot.biome_tags:
            return RecognitionResult(None, "biome_is_not_ocean", len(positions))

        known_structure = (snapshot.known_structure_id or "").removeprefix("minecraft:")
        if known_structure in OCEAN_MONUMENT_IDS:
            return RecognitionResult(None, "inside_known_ocean_monument", len(positions))

        geometry = self._geometry(positions)
        horizontal_major = max(geometry.extent_x, geometry.extent_z)
        horizontal_minor = min(geometry.extent_x, geometry.extent_z)
        if horizontal_major < self.limits.min_tunnel_length:
            return RecognitionResult(None, "air_group_too_short_for_tunnel", len(positions))
        if horizontal_major / max(1, horizontal_minor) < self.limits.min_tunnel_aspect_ratio:
            return RecognitionResult(None, "air_group_not_horizontally_elongated", len(positions))

        top_boundary = [
            position
            for position in positions
            if moved(position, Direction.UP) not in positions
        ]
        ceiling_hits = sum(
            snapshot.medium_at(moved(position, Direction.UP)) is Medium.SOLID
            for position in top_boundary
        )
        ceiling_ratio = ceiling_hits / len(top_boundary) if top_boundary else 0.0
        if ceiling_ratio < self.limits.min_tunnel_ceiling_ratio:
            return RecognitionResult(None, "air_group_has_no_stable_ceiling", len(positions))

        water_directions = self._water_beyond_boundary_directions(positions, snapshot)
        if not water_directions:
            # 海+空気+天井だけで海底神殿や地下空間をトンネルにしない。
            return RecognitionResult(None, "no_bounded_water_outside_enclosure", len(positions))

        return RecognitionResult(
            ObservedStructure(
                kind="underwater_tunnel",
                label_ja="海中トンネル",
                fact_ja="海中トンネルの中にいる",
                question_terms=("海中トンネル", "海底トンネル", "トンネル"),
                certainty="high",
                attributes={
                    "block_count": len(positions),
                    "extent_x": geometry.extent_x,
                    "extent_y": geometry.extent_y,
                    "extent_z": geometry.extent_z,
                    "ceiling_ratio": round(ceiling_ratio, 3),
                    "water_beyond_directions": [
                        direction.value for direction in sorted(water_directions, key=str)
                    ],
                },
                evidence=(
                    "ocean_biome",
                    "bounded_air_component",
                    "stable_ceiling",
                    "water_beyond_enclosing_boundary",
                    "horizontally_elongated",
                ),
            ),
            "recognized_underwater_tunnel",
            len(positions),
        )

    def recognize_direct_item(
        self,
        item_id: str,
        *,
        presentation: str = "nearby",
    ) -> RecognitionResult:
        """Minecraftに実物がある品目は複合構造を作らず直接認識する。"""

        normalized = (item_id or "").strip().lower()
        if normalized in {"clock", "minecraft:clock"}:
            return RecognitionResult(
                ObservedStructure(
                    kind="clock",
                    label_ja="時計",
                    fact_ja="近くに時計がある",
                    question_terms=("時計", "とけい"),
                    certainty="high",
                    attributes={"item_id": "minecraft:clock", "presentation": presentation},
                    evidence=("direct_clock_item",),
                ),
                "recognized_direct_clock_item",
                1,
            )
        return RecognitionResult(None, "direct_item_not_supported", 1 if normalized else 0)

    def _validate_group(self, positions: frozenset[Position]) -> RecognitionResult | None:
        if not positions:
            return RecognitionResult(None, "empty_group", 0)
        if len(positions) > self.limits.max_group_blocks:
            return RecognitionResult(None, "group_block_limit_exceeded", len(positions))
        geometry = self._geometry(positions)
        if geometry.max_extent > self.limits.max_extent_blocks:
            return RecognitionResult(None, "group_extent_limit_exceeded", len(positions))
        extraction = split_face_connected_components(positions, limits=self.limits)
        if extraction.truncated or extraction.oversized_components:
            return RecognitionResult(None, extraction.reason, len(positions))
        if len(extraction.components) != 1:
            return RecognitionResult(None, "group_is_not_face_connected", len(positions))
        return None

    def _face_media(
        self,
        positions: frozenset[Position],
        snapshot: VoxelSnapshot,
    ) -> dict[Direction, Medium]:
        return {
            direction: self._dominant_face_medium(positions, snapshot, direction)
            for direction in DIRECTIONS
        }

    def _dominant_face_medium(
        self,
        positions: frozenset[Position],
        snapshot: VoxelSnapshot,
        direction: Direction,
    ) -> Medium:
        outside = [
            moved(position, direction)
            for position in positions
            if moved(position, direction) not in positions
        ]
        counts = Counter(snapshot.medium_at(position) for position in outside)
        if not counts:
            return Medium.UNKNOWN
        medium, count = counts.most_common(1)[0]
        if count / len(outside) < self.limits.face_dominance_ratio:
            return Medium.MIXED
        return medium

    def _surface_attributes(
        self,
        positions: frozenset[Position],
        faces: dict[Direction, Medium],
    ) -> dict[str, object]:
        geometry = self._geometry(positions)
        counts = Counter(faces.values())
        return {
            "block_count": len(positions),
            "extent_x": geometry.extent_x,
            "extent_y": geometry.extent_y,
            "extent_z": geometry.extent_z,
            "face_media": {
                direction.value: faces[direction].value for direction in DIRECTIONS
            },
            "air_face_count": counts[Medium.AIR],
            "water_face_count": counts[Medium.WATER],
            "lava_face_count": counts[Medium.LAVA],
            "free_face_count": sum(counts[medium] for medium in FREE_FACE_MEDIA),
        }

    def _opposing_horizontal_connection_pair(
        self,
        positions: frozenset[Position],
        snapshot: VoxelSnapshot,
    ) -> tuple[tuple[Direction, Direction] | None, int]:
        """手すりや支柱を端面へ混ぜず、主要な橋面から両岸との接続を見る。"""

        level_counts = Counter(position[1] for position in positions)
        # 同数なら上側を選ぶ。候補切り出し側が橋面を渡すまでの限定的な規則。
        deck_y = max(level_counts, key=lambda y: (level_counts[y], y))
        deck_positions = frozenset(
            position for position in positions if position[1] == deck_y
        )
        for first, second in OPPOSING_HORIZONTAL_PAIRS:
            if self._has_solid_connection(deck_positions, snapshot, first) and (
                self._has_solid_connection(deck_positions, snapshot, second)
            ):
                return (first, second), deck_y
        return None, deck_y

    @staticmethod
    def _has_solid_connection(
        deck_positions: frozenset[Position],
        snapshot: VoxelSnapshot,
        direction: Direction,
    ) -> bool:
        return any(
            moved(position, direction) not in deck_positions
            and snapshot.medium_at(moved(position, direction)) is Medium.SOLID
            for position in deck_positions
        )

    def _water_beyond_boundary_directions(
        self,
        positions: frozenset[Position],
        snapshot: VoxelSnapshot,
    ) -> set[Direction]:
        hits: set[Direction] = set()
        for direction in (*HORIZONTAL_DIRECTIONS, Direction.UP):
            for position in positions:
                if moved(position, direction) in positions:
                    continue
                saw_boundary = False
                for distance in range(1, self.limits.max_water_shell_depth + 1):
                    medium = snapshot.medium_at(moved(position, direction, distance))
                    if medium is Medium.SOLID:
                        saw_boundary = True
                        continue
                    if medium is Medium.WATER and saw_boundary:
                        hits.add(direction)
                    break
                if direction in hits:
                    break
        return hits

    @staticmethod
    def _geometry(positions: frozenset[Position]) -> _GroupGeometry:
        xs = [position[0] for position in positions]
        ys = [position[1] for position in positions]
        zs = [position[2] for position in positions]
        return _GroupGeometry(
            extent_x=max(xs) - min(xs) + 1,
            extent_y=max(ys) - min(ys) + 1,
            extent_z=max(zs) - min(zs) + 1,
        )
