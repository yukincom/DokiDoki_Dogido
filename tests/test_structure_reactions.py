from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
from dogido_server.models import (
    Certainty,
    CombatState,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    TimePhase,
    Weather,
    WorldState,
)
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.response_catalog import (
    response_lines,
    response_text,
    structure_entry_fallback_text,
)


def make_event(
    *,
    sequence: int,
    biome: str = "plains",
    structure: str | None = None,
    ender_eye_launch_recent_ms: int | None = None,
    nearby_end_portal_frame_distance: float | None = None,
    user_text: str | None = None,
    time_phase: TimePhase = TimePhase.DAY,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=sequence),
        sequence=sequence,
        event=EventDescriptor(
            name=event_name,
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
            held_item="minecraft:torch",
        ),
        world=WorldState(
            time_phase=time_phase,
            time_of_day=6000 if time_phase == TimePhase.DAY else 18000,
            weather=Weather.CLEAR,
            biome=biome,
            structure=structure,
            ender_eye_launch_recent_ms=ender_eye_launch_recent_ms,
            nearby_end_portal_frame_distance=nearby_end_portal_frame_distance,
            local_light=15,
            sky_visible=True,
            ceiling_height=20.0,
            enclosure_score=0.0,
            overhead_cover_type="none",
            is_submerged=False,
            safe_zone_with_door=False,
            danger_darkness_score=0.0,
        ),
        combat=CombatState(),
        meta=MetaState(user_text=user_text),
    )


class StructureReactionTests(unittest.TestCase):
    def make_machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def speech_texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.layer == "speech" and action.text]

    def test_structure_entry_emits_fallback_line_once(self) -> None:
        self.machine = self.make_machine()

        entry = self.speech_texts(make_event(sequence=1, structure="village_plains"))
        self.assertEqual([structure_entry_fallback_text("village_plains")], entry)

        staying = self.speech_texts(make_event(sequence=2, structure="village_plains"))
        self.assertEqual([], staying)

        # 構造物から出ても入場コメントは出ない
        exited = self.speech_texts(make_event(sequence=3, structure=None))
        self.assertEqual([], exited)

        # クールダウン内の再入場は黙る
        reentry = self.speech_texts(make_event(sequence=4, structure="village_plains"))
        self.assertEqual([], reentry)

    def test_structure_entry_suppresses_special_biome_line(self) -> None:
        self.machine = self.make_machine()

        # 特殊バイオーム変化と構造物入場が同時なら、構造物コメントだけが出る
        entry = self.speech_texts(
            make_event(sequence=1, biome="mushroom_fields", structure="village_plains")
        )
        self.assertEqual([structure_entry_fallback_text("village_plains")], entry)

        # 構造物の中にいる間はバイオームが変わってもバイオーム行は出ない
        inside = self.speech_texts(
            make_event(sequence=2, biome="dark_forest", structure="village_plains")
        )
        self.assertEqual([], inside)

    def test_biome_line_resumes_after_leaving_structure(self) -> None:
        self.machine = self.make_machine()

        self.speech_texts(make_event(sequence=1, structure="village_plains"))
        self.speech_texts(make_event(sequence=2, structure=None))

        biome_entry = self.speech_texts(
            make_event(sequence=3, biome="mushroom_fields", structure=None)
        )
        self.assertEqual(["ほんに、妙なところやなぁ。敵の気配がせえへんわ。"], biome_entry)

    def test_unknown_structure_stays_silent_but_still_suppresses_biome_line(self) -> None:
        self.machine = self.make_machine()

        entry = self.speech_texts(
            make_event(sequence=1, biome="mushroom_fields", structure="ruined_portal")
        )
        self.assertEqual([], entry)

    def test_different_structure_entry_speaks_again(self) -> None:
        self.machine = self.make_machine()

        first = self.speech_texts(make_event(sequence=1, structure="village_plains"))
        self.assertEqual([structure_entry_fallback_text("village_plains")], first)

        self.speech_texts(make_event(sequence=2, structure=None))

        second = self.speech_texts(make_event(sequence=3, structure="pillager_outpost"))
        self.assertEqual([structure_entry_fallback_text("pillager_outpost")], second)


class EnderEyeThrowTests(unittest.TestCase):
    def make_machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def speech_texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.layer == "speech" and action.text]

    def test_ender_eye_throw_emits_one_of_catalog_lines(self) -> None:
        self.machine = self.make_machine()

        texts = self.speech_texts(make_event(sequence=1, ender_eye_launch_recent_ms=200))
        self.assertEqual(1, len(texts))
        self.assertIn(texts[0], response_lines("exploration", "ender_eye", "throw", "lines"))

    def test_ender_eye_throw_respects_cooldown(self) -> None:
        self.machine = self.make_machine()

        first = self.speech_texts(make_event(sequence=1, ender_eye_launch_recent_ms=200))
        self.assertEqual(1, len(first))

        # 1秒後の次スナップショット（同じ投擲の残響）は黙る
        echo = self.speech_texts(make_event(sequence=2, ender_eye_launch_recent_ms=1200))
        self.assertEqual([], echo)

        # クールダウン経過後の新しい投擲には反応する
        again = self.speech_texts(make_event(sequence=10, ender_eye_launch_recent_ms=200))
        self.assertEqual(1, len(again))
        self.assertIn(again[0], response_lines("exploration", "ender_eye", "throw", "lines"))

    def test_stale_ender_eye_observation_stays_silent(self) -> None:
        self.machine = self.make_machine()

        texts = self.speech_texts(make_event(sequence=1, ender_eye_launch_recent_ms=5000))
        self.assertEqual([], texts)


class EndPortalFrameTests(unittest.TestCase):
    FRAME_LINE = response_text("exploration", "portal", "frame_nearby")

    def make_machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def speech_texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.layer == "speech" and action.text]

    def test_frame_proximity_emits_line_immediately(self) -> None:
        # 他のトリガーが無い素のスナップショットでも即座に出ること（py_tree ゲートの回帰テスト）
        self.machine = self.make_machine()

        texts = self.speech_texts(
            make_event(sequence=1, nearby_end_portal_frame_distance=3.5)
        )
        self.assertEqual([self.FRAME_LINE], texts)

    def test_frame_line_works_outside_stronghold_structure(self) -> None:
        # 手置きのフレーム（構造物情報なし）でも反応する
        self.machine = self.make_machine()

        texts = self.speech_texts(
            make_event(sequence=1, structure=None, nearby_end_portal_frame_distance=5.0)
        )
        self.assertEqual([self.FRAME_LINE], texts)

    def test_frame_beyond_five_blocks_stays_silent(self) -> None:
        self.machine = self.make_machine()

        texts = self.speech_texts(
            make_event(sequence=1, nearby_end_portal_frame_distance=6.0)
        )
        self.assertEqual([], texts)

    def test_frame_line_respects_cooldown(self) -> None:
        self.machine = self.make_machine()

        first = self.speech_texts(make_event(sequence=1, nearby_end_portal_frame_distance=3.0))
        self.assertEqual([self.FRAME_LINE], first)

        again = self.speech_texts(make_event(sequence=5, nearby_end_portal_frame_distance=3.0))
        self.assertEqual([], again)

    def test_frame_line_fires_inside_stronghold_with_structure_comment_first(self) -> None:
        # 要塞入場コメントの次のスナップショットでフレーム行が出る
        self.machine = self.make_machine()

        entry = self.speech_texts(
            make_event(sequence=1, structure="stronghold", nearby_end_portal_frame_distance=4.0)
        )
        self.assertEqual([structure_entry_fallback_text("stronghold")], entry)

        frame = self.speech_texts(
            make_event(sequence=2, structure="stronghold", nearby_end_portal_frame_distance=4.0)
        )
        self.assertEqual([self.FRAME_LINE], frame)


class CommandInputTests(unittest.TestCase):
    """スラッシュコマンドは「話しかけ」扱いせず、アンビエント発話をミュートしない。"""

    def make_machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def speech_texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.layer == "speech" and action.text]

    def test_slash_command_does_not_mute_ambient_lines(self) -> None:
        self.machine = self.make_machine()

        command_event = self.speech_texts(
            make_event(sequence=1, user_text="/locate structure stronghold")
        )
        self.assertEqual([], command_event)

        frame = self.speech_texts(
            make_event(sequence=2, nearby_end_portal_frame_distance=3.0)
        )
        self.assertEqual([response_text("exploration", "portal", "frame_nearby")], frame)

    def test_chat_text_still_mutes_ambient_lines(self) -> None:
        self.machine = self.make_machine()

        self.speech_texts(make_event(sequence=1, user_text="こんにちは"))

        frame = self.speech_texts(
            make_event(sequence=2, nearby_end_portal_frame_distance=3.0)
        )
        self.assertEqual([], frame)


if __name__ == "__main__":
    unittest.main()
