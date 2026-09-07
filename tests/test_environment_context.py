from datetime import datetime

from dogido_server.environment_context import project_environment
from dogido_server.models import GameEvent


def make_event(
    *,
    held_item: str = "minecraft:stone_pickaxe",
    block_breaking_active: bool = False,
    overhead_cover_type: str = "stone",
    depth_below_surface: int | None = 12,
    nearby_door_count: int = 0,
    recent_block_breaks: list[dict[str, object]] | None = None,
    dropped_items: list[dict[str, object]] | None = None,
) -> GameEvent:
    return GameEvent.model_validate(
        {
            "schema_version": "2026-05-24",
            "adapter": "unit-test",
            "observed_at": datetime.fromisoformat("2026-09-04T21:00:00+09:00"),
            "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high",
            },
            "player": {
                "position": {"x": 0, "y": 24, "z": 0},
                "held_item": held_item,
                "block_breaking_active": block_breaking_active,
            },
            "world": {
                "biome": "plains",
                "weather": "rain",
                "sky_visible": False,
                "overhead_cover_type": overhead_cover_type,
                "depth_below_surface": depth_below_surface,
                "nearby_door_count": nearby_door_count,
            },
            "recent_block_breaks": recent_block_breaks or [],
            "dropped_items": dropped_items or [],
        }
    )


def test_active_mining_needs_recent_break_evidence() -> None:
    event = make_event(
        block_breaking_active=True,
        recent_block_breaks=[
            {"name": "stone", "material": "stone", "age_ms": 900},
        ],
    )

    projection = project_environment(event)

    assert projection.mining_state == "active"
    assert projection.mining_label == "プレイヤーは地下で採掘している"
    assert not projection.include_sky_context
    assert not projection.include_biome_context


def test_fresh_dropped_stone_reinforces_one_recent_break() -> None:
    event = make_event(
        recent_block_breaks=[
            {"name": "stone", "material": "stone", "age_ms": 1_200},
        ],
        dropped_items=[
            {
                "name": "cobblestone",
                "count": 2,
                "distance": 1.5,
                "age_ms": 700,
                "block_item": True,
                "mining_related": True,
            }
        ],
    )

    assert project_environment(event).mining_state == "active"


def test_static_terrain_can_only_identify_a_mine_like_place() -> None:
    projection = project_environment(make_event())

    assert projection.mining_state == "likely_place"
    assert projection.mining_label == "坑道らしい地下空間にいる"


def test_interior_fixture_prevents_static_mine_like_inference() -> None:
    projection = project_environment(make_event(nearby_door_count=1))

    assert projection.mining_state == "none"
    assert "interior_fixture_observed" in projection.evidence


def test_dropped_items_alone_do_not_claim_active_mining() -> None:
    event = make_event(
        dropped_items=[
            {
                "name": "cobblestone",
                "count": 12,
                "distance": 1.0,
                "age_ms": 500,
                "block_item": True,
                "mining_related": True,
            }
        ]
    )

    assert project_environment(event).mining_state == "likely_place"


def test_cave_biome_remains_visible_when_sky_is_hidden() -> None:
    event = make_event(overhead_cover_type="solid", depth_below_surface=None)
    event = event.model_copy(
        update={"world": event.world.model_copy(update={"biome": "lush_caves"})}
    )

    projection = project_environment(event)

    assert projection.include_biome_context
    assert not projection.include_sky_context
