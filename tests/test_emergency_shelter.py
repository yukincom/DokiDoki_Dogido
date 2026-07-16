from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from dogido_server.config import Settings
from dogido_server.llm import DogidoLLM
from dogido_server.models import GameEvent
from dogido_server.state_machine import (
    DogidoStateMachine,
    EMERGENCY_SHELTER_CALL,
    EMERGENCY_SHELTER_MORNING_CALL,
)
from dogido_server.state_machine.response_catalog import response_text

EMERGENCY_SHELTER_CALL_WITH_BED = response_text("darkness", "emergency_shelter", "advice_with_bed")
EMERGENCY_SHELTER_CALL_NEARBY_BED = response_text("darkness", "emergency_shelter", "advice_nearby_bed")


def build_event(
    observed_at: datetime,
    *,
    sequence: int,
    time_phase: str = "night",
    time_of_day: int = 14000,
    biome: str = "plains",
    dimension: str = "minecraft:overworld",
    local_light: int = 0,
    danger_darkness_score: float = 0.2,
    sky_visible: bool = True,
    ceiling_height: float = 24.0,
    cardinal_wall_count: int = 0,
    double_height_open_side_count: int = 0,
    respawn_point_set: bool = True,
    respawn_distance: float | None = 80.0,
    inventory: dict[str, int] | None = None,
    held_item: str = "minecraft:air",
    nearby_bed_count: int = 0,
) -> GameEvent:
    return GameEvent.model_validate(
        {
            "schema_version": "2026-05-24",
            "adapter": "unit-test",
            "observed_at": observed_at.isoformat(),
            "sequence": sequence,
            "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high",
            },
            "player": {
                "name": "tester",
                "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                "held_item": held_item,
                "dimension": dimension,
            },
            "world": {
                "time_of_day": time_of_day,
                "time_phase": time_phase,
                "weather": "clear",
                "biome": biome,
                "local_light": local_light,
                "sky_visible": sky_visible,
                "ceiling_height": ceiling_height,
                "overhead_cover_type": "none",
                "is_submerged": False,
                "submerged_depth_blocks": 0,
                "air_supply": 300,
                "nearby_door_count": 0,
                "open_door_count": 0,
                "nearby_bed_count": nearby_bed_count,
                "drafty_opening_count": 0,
                "respawn_point_set": respawn_point_set,
                "respawn_distance": respawn_distance,
                "cardinal_wall_count": cardinal_wall_count,
                "double_height_open_side_count": double_height_open_side_count,
                "safe_zone_with_door": False,
                "enclosure_score": 0.05,
                "connected_dark_volume": 0,
                "nearest_dark_spawn_distance": 99.0,
                "danger_darkness_score": danger_darkness_score,
                "nearby_firefly_bush_count": 0,
            },
            "visual_threats": [],
            "auditory_threats": [],
            "passive_mobs": [],
            "inventory": inventory or {},
            "nearby_resources": [],
            "combat": {
                "recent_damage_ms": None,
                "recent_hostile_visual_ms": None,
                "recent_hostile_audio_ms": None,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": False,
            },
            "meta": {"debug": False},
        }
    )


class EmergencyShelterTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(decision_policy="py_trees")
        self.machine = DogidoStateMachine(settings=settings, llm=None)
        self.started_at = datetime(2026, 5, 31, 0, 0, tzinfo=UTC)

    def test_emergency_shelter_advice_resets_next_night(self) -> None:
        first_night = self.machine.process(build_event(self.started_at, sequence=1))
        self.assertEqual([action.text for action in first_night.actions], [EMERGENCY_SHELTER_CALL])

        same_night = self.machine.process(
            build_event(self.started_at + timedelta(seconds=5), sequence=2)
        )
        self.assertEqual(same_night.actions, [])

        morning_in_shelter = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=10),
                sequence=3,
                time_phase="morning",
                time_of_day=1000,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
            )
        )
        self.assertEqual(
            [action.text for action in morning_in_shelter.actions if action.text],
            [EMERGENCY_SHELTER_MORNING_CALL],
        )

        next_night = self.machine.process(
            build_event(self.started_at + timedelta(seconds=15), sequence=4)
        )
        self.assertEqual([action.text for action in next_night.actions], [EMERGENCY_SHELTER_CALL])

    def test_high_cost_materials_do_not_block_emergency_shelter_after_surface_hostile_spawn_time(self) -> None:
        coal_event = self.machine.process(
            build_event(self.started_at, sequence=1, inventory={"coal": 1})
        )
        self.assertIn(EMERGENCY_SHELTER_CALL, [action.text for action in coal_event.actions])

        wool_machine = DogidoStateMachine(settings=Settings(decision_policy="py_trees"), llm=None)
        wool_event = wool_machine.process(
            build_event(self.started_at, sequence=1, inventory={"red_wool": 1})
        )
        self.assertIn(EMERGENCY_SHELTER_CALL, [action.text for action in wool_event.actions])

    def test_emergency_shelter_prefers_held_bed_over_dig_down(self) -> None:
        result = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                held_item="minecraft:red_bed",
                inventory={},
            )
        )
        texts = [action.text for action in result.actions]
        self.assertIn(EMERGENCY_SHELTER_CALL_WITH_BED, texts)
        self.assertNotIn(EMERGENCY_SHELTER_CALL, texts)
        self.assertNotIn("掘って", EMERGENCY_SHELTER_CALL_WITH_BED)

    def test_emergency_shelter_prefers_inventory_bed_over_dig_down(self) -> None:
        result = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                inventory={"white_bed": 1},
            )
        )
        texts = [action.text for action in result.actions]
        self.assertIn(EMERGENCY_SHELTER_CALL_WITH_BED, texts)
        self.assertNotIn(EMERGENCY_SHELTER_CALL, texts)

    def test_emergency_shelter_prefers_nearby_bed_over_dig_down(self) -> None:
        result = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                nearby_bed_count=1,
            )
        )
        texts = [action.text for action in result.actions]
        self.assertIn(EMERGENCY_SHELTER_CALL_NEARBY_BED, texts)
        self.assertNotIn(EMERGENCY_SHELTER_CALL, texts)

    def test_emergency_shelter_waits_until_surface_hostile_spawn_time(self) -> None:
        before_spawn = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                time_phase="evening",
                time_of_day=12999,
            )
        )
        after_spawn = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=5),
                sequence=2,
                time_phase="night",
                time_of_day=13000,
            )
        )

        self.assertNotIn(EMERGENCY_SHELTER_CALL, [action.text for action in before_spawn.actions])
        self.assertEqual([action.text for action in after_spawn.actions], [EMERGENCY_SHELTER_CALL])

    def test_emergency_shelter_is_suppressed_in_cave_and_other_realms(self) -> None:
        cave = self.machine.process(
            build_event(
                self.started_at,
                sequence=20,
                biome="dripstone_caves",
                sky_visible=False,
                danger_darkness_score=1.0,
            )
        )
        deep_dark = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=1),
                sequence=21,
                biome="deep_dark",
                sky_visible=False,
                danger_darkness_score=1.0,
            )
        )
        nether = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=2),
                sequence=22,
                biome="soul_sand_valley",
                dimension="minecraft:the_nether",
                sky_visible=False,
                danger_darkness_score=1.0,
            )
        )
        end = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=3),
                sequence=23,
                biome="the_end",
                dimension="minecraft:the_end",
                sky_visible=False,
                danger_darkness_score=1.0,
            )
        )

        for result in (cave, deep_dark, nether, end):
            self.assertNotIn(EMERGENCY_SHELTER_CALL, [action.text for action in result.actions])

    def test_emergency_shelter_suppresses_dark_push_voice(self) -> None:
        self.machine.state.dark_push_active = True
        self.machine.state.dark_push_stage = 2

        result = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                danger_darkness_score=0.9,
            )
        )

        self.assertEqual([action.layer for action in result.actions], ["control", "speech"])
        self.assertEqual(
            [action.text for action in result.actions if action.text],
            ["よし！シャルターやな！これで安心や！"],
        )

    def test_first_snapshot_in_shelter_emits_relief_line(self) -> None:
        result = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                danger_darkness_score=0.9,
            )
        )

        self.assertEqual(
            [action.text for action in result.actions if action.text],
            ["よし！シャルターやな！これで安心や！"],
        )

    def test_emergency_shelter_entry_uses_llm_leaf(self) -> None:
        class FakeLLM(DogidoLLM):
            def __init__(self) -> None:
                super().__init__(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))
                self.kinds: list[str] = []

            def generate_leaf_text(self, request):  # type: ignore[override]
                self.kinds.append(request.kind)
                return f"LLM:{request.kind}"

        fake_llm = FakeLLM()
        machine = DogidoStateMachine(
            settings=Settings(decision_policy="py_trees"),
            llm=fake_llm,
        )

        result = machine.process(
            build_event(
                self.started_at,
                sequence=1,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                danger_darkness_score=0.9,
            )
        )

        self.assertEqual(fake_llm.kinds, ["emergency_shelter_relief"])
        self.assertEqual(
            [action.text for action in result.actions if action.text],
            ["LLM:emergency_shelter_relief"],
        )

    def test_double_height_open_side_prevents_emergency_shelter_relief(self) -> None:
        result = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                double_height_open_side_count=1,
                danger_darkness_score=0.9,
            )
        )

        self.assertFalse(any(action.text == "よし！シャルターやな！これで安心や！" for action in result.actions))

    def test_morning_release_emits_after_spending_night_in_shelter_without_prior_advice(self) -> None:
        night_in_shelter = self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                respawn_point_set=False,
                respawn_distance=None,
            )
        )
        morning_in_shelter = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=10),
                sequence=2,
                time_phase="morning",
                time_of_day=1000,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                respawn_point_set=False,
                respawn_distance=None,
            )
        )

        self.assertEqual(
            [action.text for action in night_in_shelter.actions if action.text],
            ["よし！シャルターやな！これで安心や！"],
        )
        self.assertEqual(
            [action.text for action in morning_in_shelter.actions if action.text],
            [EMERGENCY_SHELTER_MORNING_CALL],
        )

    def test_daytime_release_still_emits_if_player_stayed_in_shelter_past_morning(self) -> None:
        self.machine.process(
            build_event(
                self.started_at,
                sequence=1,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                respawn_point_set=False,
                respawn_distance=None,
            )
        )
        daytime_in_shelter = self.machine.process(
            build_event(
                self.started_at + timedelta(seconds=10),
                sequence=2,
                time_phase="day",
                time_of_day=3000,
                sky_visible=False,
                ceiling_height=2.0,
                cardinal_wall_count=4,
                respawn_point_set=False,
                respawn_distance=None,
            )
        )

        self.assertEqual(
            [action.text for action in daytime_in_shelter.actions if action.text],
            [EMERGENCY_SHELTER_MORNING_CALL],
        )


if __name__ == "__main__":
    unittest.main()
