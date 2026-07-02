from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

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
from dogido_server.service import DogidoService
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.fallback_catalog import fallback_text

BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)

CHAT_REPLY = fallback_text("general", "chat", "reply")


def make_event(*, sequence: int, at_sec: float = 0.0, user_text: str | None = None) -> GameEvent:
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
            time_phase=TimePhase.DAY,
            time_of_day=6000,
            weather=Weather.CLEAR,
            biome="plains",
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


class PlayerChatReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.text]

    def test_free_chat_gets_reply(self) -> None:
        texts = self.texts(make_event(sequence=1, user_text="今日もよろしくな"))
        self.assertEqual([CHAT_REPLY], texts)

    def test_romaji_chat_gets_reply(self) -> None:
        texts = self.texts(make_event(sequence=1, user_text="outouseyo"))
        self.assertEqual([CHAT_REPLY], texts)

    def test_consecutive_chats_each_get_reply(self) -> None:
        first = self.texts(make_event(sequence=1, at_sec=0, user_text="おーい"))
        second = self.texts(make_event(sequence=2, at_sec=5, user_text="聞こえとる？"))
        self.assertEqual([CHAT_REPLY], first)
        self.assertEqual([CHAT_REPLY], second)

    def test_hush_request_gets_no_reply(self) -> None:
        texts = self.texts(make_event(sequence=1, user_text="うるさい"))
        self.assertEqual([], texts)

    def test_slash_command_gets_no_reply(self) -> None:
        texts = self.texts(make_event(sequence=1, user_text="/tp @s 0 64 0"))
        self.assertEqual([], texts)

    def test_keyword_question_still_answered_not_chatted(self) -> None:
        texts = self.texts(make_event(sequence=1, user_text="ドラゴンどこ？"))
        self.assertEqual(1, len(texts))
        self.assertNotEqual(CHAT_REPLY, texts[0])

    def test_whisper_katakana_output_matches_keywords(self) -> None:
        # 音声認識（whisper）は「ドラゴンドコ」のようにカタカナで返すことがある
        texts = self.texts(make_event(sequence=1, user_text="ドラゴンドコ"))
        self.assertEqual(1, len(texts))
        self.assertNotEqual(CHAT_REPLY, texts[0])


class PlayerInputEndpointTests(unittest.TestCase):
    def make_service(self, tmp: str) -> DogidoService:
        return DogidoService(
            Settings(
                audio_enabled=False,
                llm_enabled=False,
                decision_policy="py_trees",
                memory_dir=Path(tmp) / "memory",
            )
        )

    def test_pushed_text_rides_next_event_and_gets_reply(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))

            result = service.push_player_input("おはようさん")
            self.assertTrue(result["accepted"])

            processed = service.process_event(make_event(sequence=2, at_sec=1.0))
            texts = [action.text for action in processed.actions if action.text]
            self.assertEqual([CHAT_REPLY], texts)

            # 記憶ログにも player_input として残る
            rows = service.memory._read_jsonl(service.memory.short_term_path)  # type: ignore[union-attr]
            self.assertTrue(
                any(row["type"] == "player_input" and row["text"] == "おはようさん" for row in rows)
            )

    def test_push_without_session_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            result = service.push_player_input("おーい")
            self.assertFalse(result["accepted"])
            self.assertEqual("no_active_session", result["reason"])

    def test_adapter_chat_wins_over_pending_voice_text(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))
            service.push_player_input("ボイス入力や")

            # 同じイベントにチャットが載っていたらチャット優先、ボイスは次イベントへ
            processed = service.process_event(
                make_event(sequence=2, at_sec=1.0, user_text="チャット入力や")
            )
            texts = [action.text for action in processed.actions if action.text]
            self.assertEqual([CHAT_REPLY], texts)

            rows = service.memory._read_jsonl(service.memory.short_term_path)  # type: ignore[union-attr]
            chat_rows = [row for row in rows if row["type"] == "player_input"]
            self.assertEqual(["チャット入力や"], [row["text"] for row in chat_rows])

            processed_next = service.process_event(make_event(sequence=3, at_sec=2.0))
            texts_next = [action.text for action in processed_next.actions if action.text]
            self.assertEqual([CHAT_REPLY], texts_next)
            rows = service.memory._read_jsonl(service.memory.short_term_path)  # type: ignore[union-attr]
            chat_rows = [row for row in rows if row["type"] == "player_input"]
            self.assertEqual(["チャット入力や", "ボイス入力や"], [row["text"] for row in chat_rows])


if __name__ == "__main__":
    unittest.main()
