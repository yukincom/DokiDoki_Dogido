from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
from dogido_server.dialogue.player_plan import (
    conflicts_with_player_travel_guidance,
    extract_player_turn_plan,
)
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.models import (
    Certainty,
    CombatState,
    Direction,
    EventDescriptor,
    EventName,
    GameEvent,
    HorizontalDirection,
    MetaState,
    PassiveMob,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    TimePhase,
    Weather,
    WorldState,
)
from dogido_server.player_input import route_player_input
from dogido_server.state_machine import DogidoStateMachine


BASE = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)


def make_event(
    *,
    sequence: int = 1,
    at_sec: float = 0.0,
    user_text: str | None = None,
    time_phase: TimePhase = TimePhase.DAY,
    weather: Weather = Weather.CLEAR,
    biome: str = "plains",
    sky_visible: bool = True,
    safe_zone_with_door: bool = False,
    respawn_distance: float | None = None,
    respawn_point_set: bool = False,
    nearby_bed_count: int = 0,
    passive_mobs: list[PassiveMob] | None = None,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=BASE + timedelta(seconds=at_sec),
        sequence=sequence,
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="player",
            position=Position(x=0.0, y=64.0, z=0.0),
            dimension="minecraft:overworld",
            health=20.0,
            hunger=20,
        ),
        world=WorldState(
            time_phase=time_phase,
            time_of_day=12000 if time_phase == TimePhase.EVENING else 6000,
            weather=weather,
            biome=biome,
            local_light=15,
            sky_visible=sky_visible,
            ceiling_height=20.0,
            enclosure_score=0.0,
            overhead_cover_type="none",
            is_submerged=False,
            safe_zone_with_door=safe_zone_with_door,
            danger_darkness_score=0.0,
            respawn_distance=respawn_distance,
            respawn_point_set=respawn_point_set,
            nearby_bed_count=nearby_bed_count,
        ),
        passive_mobs=list(passive_mobs or []),
        combat=CombatState(),
        meta=MetaState(user_text=user_text),
    )


class PlayerTurnPlanTests(unittest.TestCase):
    def test_extracts_explicit_return_home_without_storing_a_goal(self) -> None:
        plan = extract_player_turn_plan("日が沈んできた早くお家に帰らなくちゃ")
        self.assertEqual(plan.action, "return_home")
        self.assertEqual(plan.evidence, "帰らなくちゃ")

        self.assertEqual(extract_player_turn_plan("そろそろ拠点に戻ろう").action, "return_home")
        self.assertEqual(extract_player_turn_plan("家には帰らないよ").action, "none")
        self.assertEqual(extract_player_turn_plan("今日は帰らないと思う").action, "none")
        self.assertEqual(extract_player_turn_plan("洞窟を探検しよう").action, "none")

    def test_rejects_only_outward_suggestions_that_conflict_with_guidance(self) -> None:
        self.assertTrue(
            conflicts_with_player_travel_guidance(
                "日が暮れる前に、ちょっと遠くまで行けへんか？",
                player_turn_plan="return_home",
                safety_priority="seek_safe_place",
            )
        )
        self.assertTrue(
            conflicts_with_player_travel_guidance(
                "帰る前にもう少し遠くまで行こうや。",
                player_turn_plan="return_home",
                safety_priority="seek_safe_place",
            )
        )
        self.assertFalse(
            conflicts_with_player_travel_guidance(
                "遠出はやめて、そろそろ帰ろか。",
                player_turn_plan="return_home",
                safety_priority="seek_safe_place",
            )
        )


class PlayerChatSafetyContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=False)
        )

    def test_safety_priority_is_derived_and_clears_on_day(self) -> None:
        evening = make_event(time_phase=TimePhase.EVENING)
        self.assertTrue(self.machine._is_surface_evening_warning_context(evening))
        self.assertEqual(self.machine._player_chat_safety_priority(evening), "seek_safe_place")

        day = make_event(sequence=2, time_phase=TimePhase.DAY)
        self.assertEqual(self.machine._player_chat_safety_priority(day), "none")

        thunder = make_event(sequence=3, time_phase=TimePhase.DAY, weather=Weather.THUNDER)
        self.assertEqual(self.machine._player_chat_safety_priority(thunder), "seek_safe_place")

    def test_cave_suppresses_both_evening_warning_and_chat_safety(self) -> None:
        cave = make_event(
            time_phase=TimePhase.EVENING,
            weather=Weather.THUNDER,
            biome="dripstone_caves",
        )
        self.assertFalse(self.machine._is_surface_evening_warning_context(cave))
        self.assertEqual(self.machine._player_chat_safety_priority(cave), "none")

    def test_home_progress_requires_multiple_distance_samples(self) -> None:
        for sequence, at_sec, distance in (
            (1, 0.0, 40.0),
            (2, 1.0, 38.8),
            (3, 2.0, 37.5),
        ):
            event = make_event(
                sequence=sequence,
                at_sec=at_sec,
                respawn_point_set=True,
                respawn_distance=distance,
            )
            self.machine._update_respawn_distance_samples(event, event.observed_at)
        self.assertEqual(self.machine._player_chat_home_progress(event), "approaching")

        one_sample_machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=False)
        )
        one = make_event(respawn_point_set=True, respawn_distance=20.0)
        one_sample_machine._update_respawn_distance_samples(one, one.observed_at)
        self.assertEqual(one_sample_machine._player_chat_home_progress(one), "unknown")


class PlayerChatSafetyPromptTests(unittest.TestCase):
    def test_time_and_safety_are_separate_prompt_facts(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "早くお家に帰らなくちゃ",
                    "time_phase": "evening",
                    "weather_label": "晴れ",
                    "reply_stance": "none",
                    "reply_policy": "雑談として自然に返す。",
                    "player_turn_plan": "return_home",
                    "player_turn_plan_evidence": "帰らなくちゃ",
                    "safety_priority": "seek_safe_place",
                    "home_progress": "approaching",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("時間: evening", content)
        self.assertIn("現在の安全方針: 帰宅・避難を優先", content)
        self.assertIn("プレイヤーの今回の予定:", content)
        self.assertIn("家への移動状況: 家へ接近中", content)
        self.assertNotIn("夕方なので", content)
        self.assertIn("反対方向の行動を提案しない", content)

    def test_return_home_turn_suppresses_unrelated_passive_material_and_bad_reply(self) -> None:
        class CaptureLLM:
            def __init__(self) -> None:
                self.details: dict[str, object] = {}

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                self.details = dict(request.details)
                return "ロバさんも仲間入りか。日が暮れる前に、ちょっと遠くまで行けへんか？"

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                return {}

        llm = CaptureLLM()
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=llm,
        )
        event = make_event(
            user_text="日が沈んできた早くお家に帰らなくちゃ",
            time_phase=TimePhase.EVENING,
            respawn_point_set=True,
            respawn_distance=30.0,
            passive_mobs=[
                PassiveMob(
                    type="donkey",
                    distance=4.0,
                    direction=Direction(horizontal=HorizontalDirection.FRONT),
                ),
                PassiveMob(
                    type="sheep",
                    distance=6.0,
                    direction=Direction(horizontal=HorizontalDirection.LEFT),
                ),
            ],
        )
        machine.player_input = route_player_input(event.meta.user_text)
        reply = machine._render_player_chat_reply(event)

        self.assertEqual(llm.details.get("player_turn_plan"), "return_home")
        self.assertEqual(llm.details.get("safety_priority"), "seek_safe_place")
        self.assertNotIn("近くの生き物", str(llm.details.get("observation_summary") or ""))
        self.assertNotIn("遠くまで", reply)
        self.assertIn("帰ろか", reply)


if __name__ == "__main__":
    unittest.main()
