from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
from dogido_server.llm import LeafGenerationRequest, StructuredGenerationRequest
from dogido_server.models import (
    Certainty,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    NearbyResource,
    PeacefulMob,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    Weather,
    WorldState,
)
from dogido_server.state_machine import DogidoStateMachine


def make_snapshot(
    observed_at: datetime,
    *,
    biome: str = "desert",
    time_phase: str = "day",
    time_of_day: int = 6000,
    user_text: str | None = None,
    peaceful_mobs: list[PeacefulMob] | None = None,
    nearby_resources: list[NearbyResource] | None = None,
    player_y: float = 64,
    danger_darkness_score: float = 0.0,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=observed_at,
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="player",
            position=Position(x=0, y=player_y, z=12),
            dimension="minecraft:overworld",
            held_item="minecraft:torch",
        ),
        world=WorldState(
            time_of_day=time_of_day,
            time_phase=time_phase,
            weather=Weather.CLEAR,
            biome=biome,
            local_light=15,
            sky_visible=True,
            danger_darkness_score=danger_darkness_score,
        ),
        peaceful_mobs=list(peaceful_mobs or []),
        inventory={"torch": 2, "oak_log": 4},
        nearby_resources=list(nearby_resources or []),
        meta=MetaState(user_text=user_text),
    )


class HaikuStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(llm_enabled=False, decision_policy="py_trees")
        self.machine = DogidoStateMachine(self.settings)
        self.base_time = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_haiku_emits_after_silence_and_resets_on_morning(self) -> None:
        self.assertEqual(self.machine.process(make_snapshot(self.base_time)).actions, [])
        self.assertEqual(
            self.machine.process(make_snapshot(self.base_time + timedelta(seconds=299))).actions,
            [],
        )

        emitted = self.machine.process(make_snapshot(self.base_time + timedelta(seconds=301))).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "砂集め　燃えろやハスク　ガラス吹き")

        self.assertEqual(
            self.machine.process(make_snapshot(self.base_time + timedelta(seconds=602))).actions,
            [],
        )

        self.assertEqual(
            self.machine.process(
                make_snapshot(
                    self.base_time + timedelta(seconds=302),
                    time_phase="morning",
                    time_of_day=1000,
                )
            ).actions,
            [],
        )

        morning_emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=603),
                time_phase="morning",
                time_of_day=1000,
            )
        ).actions
        self.assertEqual(len(morning_emitted), 1)
        self.assertEqual(morning_emitted[0].text, "砂集め　燃えろやハスク　ガラス吹き")

    def test_user_text_resets_silence_timer(self) -> None:
        self.machine.process(make_snapshot(self.base_time))

        self.assertEqual(
            self.machine.process(
                make_snapshot(
                    self.base_time + timedelta(seconds=301),
                    user_text="こんにちは",
                )
            ).actions,
            [],
        )

        self.assertEqual(
            self.machine.process(make_snapshot(self.base_time + timedelta(seconds=550))).actions,
            [],
        )

        emitted = self.machine.process(make_snapshot(self.base_time + timedelta(seconds=605))).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "砂集め　燃えろやハスク　ガラス吹き")

    def test_missing_biome_falls_back_to_under_construction_line(self) -> None:
        self.machine.process(make_snapshot(self.base_time, biome="savanna_plateau"))
        emitted = self.machine.process(
            make_snapshot(self.base_time + timedelta(seconds=301), biome="savanna_plateau")
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "今、考え中やねん…")

    def test_tropical_fish_rule_depends_on_mob_not_ocean_biome(self) -> None:
        tropical_fish = PeacefulMob(type="tropical_fish")
        self.machine.process(
            make_snapshot(
                self.base_time,
                biome="river",
                peaceful_mobs=[tropical_fish],
            )
        )
        emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="river",
                peaceful_mobs=[tropical_fish],
            )
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "おさかなさん　色とりどりの　水の花")

    def test_sheep_rule_is_mob_based_not_biome_limited(self) -> None:
        sheep = PeacefulMob(type="sheep")
        self.machine.process(
            make_snapshot(
                self.base_time,
                biome="savanna_plateau",
                peaceful_mobs=[sheep],
            )
        )
        emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="savanna_plateau",
                peaceful_mobs=[sheep],
            )
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "野にいでて　ひつじめえめえ　草がベリ！")

    def test_birch_rule_uses_nearby_resources_not_biome_name(self) -> None:
        birch_leaves = NearbyResource(type="block", name="minecraft:birch_leaves", distance=20.0)
        self.machine.process(
            make_snapshot(
                self.base_time,
                biome="forest",
                nearby_resources=[birch_leaves],
            )
        )
        emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="forest",
                nearby_resources=[birch_leaves],
            )
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "しらかばの　ふしめがちょっと　目にみえる")

    def test_diamond_rule_uses_depth_not_deep_dark_exact_match(self) -> None:
        self.machine.process(
            make_snapshot(
                self.base_time,
                biome="dripstone_caves",
                player_y=12,
                danger_darkness_score=0.0,
            )
        )
        emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="dripstone_caves",
                player_y=12,
                danger_darkness_score=0.0,
            )
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "しんそうや　ダイヤはどこや　怖いわぁ")

    def test_haiku_feature_candidates_include_biome_group_traits(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="taiga",
        )
        candidates = self.machine._haiku_context(event).feature_candidate_labels()
        self.assertIn("地帯 冷帯バイオーム", candidates)
        self.assertIn("地形 雪は Y153から", candidates)

    def test_haiku_uses_chat_route_for_irony_and_haiku_route_for_final_generation(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.leaf_requests: list[LeafGenerationRequest] = []
                self.structured_requests: list[StructuredGenerationRequest] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                self.leaf_requests.append(request)
                return "すなあつめ\nくりーぱーくる\nこわいわあ"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                self.structured_requests.append(request)
                return {
                    "found": True,
                    "kind": "contrast",
                    "description": "深い地下なのにのどか",
                    "elements": ["地下", "ヒツジ"],
                    "focus": ["地下", "ヒツジ"],
                    "confidence": 0.8,
                }

        fake_llm = FakeLLM()
        machine = DogidoStateMachine(self.settings, llm=fake_llm)
        sheep = PeacefulMob(type="sheep")
        machine.process(
            make_snapshot(
                self.base_time,
                biome="savanna_plateau",
                peaceful_mobs=[sheep],
                player_y=12,
            )
        )
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="savanna_plateau",
                peaceful_mobs=[sheep],
                player_y=12,
            )
        ).actions

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "すなあつめ\nくりーぱーくる\nこわいわあ")
        self.assertEqual(len(fake_llm.structured_requests), 1)
        self.assertEqual(fake_llm.structured_requests[0].route, "chat")
        self.assertEqual(len(fake_llm.leaf_requests), 1)
        self.assertEqual(fake_llm.leaf_requests[0].route, "haiku")
        self.assertEqual(fake_llm.leaf_requests[0].kind, "haiku")


if __name__ == "__main__":
    unittest.main()
