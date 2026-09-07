from __future__ import annotations

from experiments.composite_structure_recognition import (
    BlockCell,
    BlockGroupMeaningConverter,
    Direction,
    Medium,
    RecognitionLimits,
    VoxelSnapshot,
    split_face_connected_components,
)
from experiments.composite_structure_recognition.model import DIRECTIONS, Position, moved


def _solid_group_snapshot(
    group: set[Position],
    face_media: dict[Direction, Medium],
    *,
    face_overrides: dict[tuple[Position, Direction], Medium] | None = None,
) -> VoxelSnapshot:
    cells = {position: BlockCell(Medium.SOLID) for position in group}
    overrides = face_overrides or {}
    for position in group:
        for direction in DIRECTIONS:
            outside = moved(position, direction)
            if outside in group:
                continue
            medium = overrides.get((position, direction), face_media[direction])
            cells[outside] = BlockCell(medium)
    return VoxelSnapshot(cells=cells)


def _single_block_faces(**overrides: Medium) -> dict[Direction, Medium]:
    faces = {direction: Medium.AIR for direction in DIRECTIONS}
    for name, medium in overrides.items():
        faces[Direction(name)] = medium
    return faces


def _underwater_tunnel_snapshot(
    *,
    known_structure_id: str | None = None,
    include_outer_water: bool = True,
) -> tuple[set[Position], VoxelSnapshot]:
    air_group = {(x, y, 0) for x in range(5) for y in (0, 1)}
    cells = {position: BlockCell(Medium.AIR) for position in air_group}
    for x in range(5):
        # 天井と両側の壁。その外に水がある。
        cells[(x, 2, 0)] = BlockCell(Medium.SOLID)
        cells[(x, 0, -1)] = BlockCell(Medium.SOLID)
        cells[(x, 1, -1)] = BlockCell(Medium.SOLID)
        cells[(x, 0, 1)] = BlockCell(Medium.SOLID)
        cells[(x, 1, 1)] = BlockCell(Medium.SOLID)
        if include_outer_water:
            cells[(x, 3, 0)] = BlockCell(Medium.WATER)
            cells[(x, 0, -2)] = BlockCell(Medium.WATER)
            cells[(x, 1, -2)] = BlockCell(Medium.WATER)
            cells[(x, 0, 2)] = BlockCell(Medium.WATER)
            cells[(x, 1, 2)] = BlockCell(Medium.WATER)
    return air_group, VoxelSnapshot(
        cells=cells,
        biome_tags=frozenset({"ocean"}),
        known_structure_id=known_structure_id,
    )


def test_one_block_bridge_with_four_air_faces() -> None:
    group = {(0, 0, 0)}
    snapshot = _solid_group_snapshot(
        group,
        _single_block_faces(west=Medium.SOLID, east=Medium.SOLID),
    )

    result = BlockGroupMeaningConverter().recognize_solid_group(group, snapshot)

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "bridge"
    assert result.observation.attributes["free_face_count"] == 4
    assert result.observation.attributes["air_face_count"] == 4


def test_bridge_accepts_one_air_and_three_water_faces() -> None:
    group = {(0, 0, 0)}
    snapshot = _solid_group_snapshot(
        group,
        _single_block_faces(
            west=Medium.SOLID,
            east=Medium.SOLID,
            down=Medium.WATER,
            north=Medium.WATER,
            south=Medium.WATER,
        ),
    )

    result = BlockGroupMeaningConverter().recognize_solid_group(group, snapshot)

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "bridge"
    assert result.observation.attributes["air_face_count"] == 1
    assert result.observation.attributes["water_face_count"] == 3


def test_bridge_accepts_one_air_and_three_lava_faces() -> None:
    group = {(0, 0, 0)}
    snapshot = _solid_group_snapshot(
        group,
        _single_block_faces(
            north=Medium.SOLID,
            south=Medium.SOLID,
            down=Medium.LAVA,
            west=Medium.LAVA,
            east=Medium.LAVA,
        ),
    )

    result = BlockGroupMeaningConverter().recognize_solid_group(group, snapshot)

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "bridge"
    assert result.observation.attributes["air_face_count"] == 1
    assert result.observation.attributes["lava_face_count"] == 3


def test_six_air_faces_are_stepping_stone_before_bridge() -> None:
    group = {(0, 0, 0)}
    snapshot = _solid_group_snapshot(group, _single_block_faces())

    result = BlockGroupMeaningConverter().recognize_solid_group(group, snapshot)

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "stepping_stone"


def test_one_support_is_tolerated_by_face_dominance() -> None:
    group = {(x, 0, 0) for x in range(5)}
    faces = _single_block_faces(west=Medium.SOLID, east=Medium.SOLID)
    overrides = {((2, 0, 0), Direction.DOWN): Medium.SOLID}
    snapshot = _solid_group_snapshot(group, faces, face_overrides=overrides)

    result = BlockGroupMeaningConverter().recognize_solid_group(group, snapshot)

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "bridge"
    assert result.observation.attributes["face_media"]["down"] == "air"


def test_group_without_opposing_connections_is_not_bridge() -> None:
    group = {(0, 0, 0)}
    snapshot = _solid_group_snapshot(
        group,
        _single_block_faces(west=Medium.SOLID),
    )

    result = BlockGroupMeaningConverter().recognize_solid_group(group, snapshot)

    assert not result.recognized
    assert result.reason == "solid_group_does_not_match_supported_structure"


def test_oversized_group_fails_closed() -> None:
    limits = RecognitionLimits(max_group_blocks=4)
    group = {(x, 0, 0) for x in range(5)}
    snapshot = VoxelSnapshot(
        cells={position: BlockCell(Medium.SOLID) for position in group}
    )

    result = BlockGroupMeaningConverter(limits).recognize_solid_group(group, snapshot)

    assert not result.recognized
    assert result.reason == "group_block_limit_exceeded"


def test_connected_component_split_is_bounded_and_face_based() -> None:
    extraction = split_face_connected_components(
        {(0, 0, 0), (1, 0, 0), (10, 0, 0)},
        limits=RecognitionLimits(max_group_blocks=4),
    )

    assert extraction.reason == "ok"
    assert {len(component) for component in extraction.components} == {1, 2}


def test_candidate_set_limit_fails_without_partial_components() -> None:
    extraction = split_face_connected_components(
        {(x, 0, 0) for x in range(5)},
        limits=RecognitionLimits(max_candidate_blocks=4),
    )

    assert extraction.truncated
    assert extraction.components == ()
    assert extraction.reason == "candidate_block_limit_exceeded"


def test_underwater_tunnel_requires_ocean_air_ceiling_and_outer_water() -> None:
    group, snapshot = _underwater_tunnel_snapshot()

    result = BlockGroupMeaningConverter().recognize_air_group(group, snapshot)

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "underwater_tunnel"
    assert result.observation.attributes["ceiling_ratio"] == 1.0


def test_ocean_air_and_ceiling_without_outer_water_is_not_enough() -> None:
    group, snapshot = _underwater_tunnel_snapshot(include_outer_water=False)

    result = BlockGroupMeaningConverter().recognize_air_group(group, snapshot)

    assert not result.recognized
    assert result.reason == "no_bounded_water_outside_enclosure"


def test_known_ocean_monument_is_not_called_underwater_tunnel() -> None:
    # 現行カタログのIDは monument。旧称相当も入力境界で安全側に扱う。
    for structure_id in ("minecraft:monument", "minecraft:ocean_monument"):
        group, snapshot = _underwater_tunnel_snapshot(
            known_structure_id=structure_id
        )

        result = BlockGroupMeaningConverter().recognize_air_group(group, snapshot)

        assert not result.recognized
        assert result.reason == "inside_known_ocean_monument"


def test_clock_item_is_recognized_directly_without_composite_analysis() -> None:
    result = BlockGroupMeaningConverter().recognize_direct_item(
        "minecraft:clock",
        presentation="item_frame",
    )

    assert result.recognized
    assert result.observation is not None
    assert result.observation.kind == "clock"
    assert result.observation.attributes["presentation"] == "item_frame"
