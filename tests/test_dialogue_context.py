from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dogido_server.config import Settings
from dogido_server.dialogue_context import DialogueContext
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
    VisualThreat,
    Weather,
    WorldState,
)
from dogido_server.service import DogidoService


BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def make_event(
    *,
    sequence: int,
    at_sec: float = 0.0,
    user_text: str | None = None,
    inventory: dict[str, int] | None = None,
    visual_threats: list[VisualThreat] | None = None,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=BASE + timedelta(seconds=at_sec),
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
        inventory=dict(inventory or {}),
        visual_threats=list(visual_threats or []),
        combat=CombatState(),
        meta=MetaState(user_text=user_text),
    )


class HostileFreezeAdviceGuardTests(unittest.TestCase):
    def test_rejects_freeze_advice_for_any_hostile_context(self) -> None:
        from dogido_server.entry_catalog import mob_dogido_tactics
        from dogido_server.llm.sanitize import contains_forbidden_mob_advice, is_style_acceptable

        # クリーパー固有の forbidden に「じっと」を積まない（全 hostile 共通規則）
        tactics = mob_dogido_tactics("creeper")
        self.assertIsNotNone(tactics)
        self.assertFalse(any("じっと" in str(item) for item in tactics.get("forbidden_advice") or []))
        self.assertTrue(tactics.get("safe_hints"))

        bad_creeper = "前と後ろにクリーパーおるで！じっとしてろ！"
        bad_zombie = "ゾンビ来とる、じっとしてろ！"
        good = "前にクリーパーや！気いつけや！"
        creeper_details = {
            "nearby_hostile_types": ["creeper"],
            "has_visual_threats": True,
            "threat_summary": "視認 クリーパー が前 9マス",
        }
        zombie_details = {
            "nearby_hostile_types": ["zombie"],
            "has_visual_threats": True,
            "threat_summary": "視認 ゾンビ が前 5マス",
        }
        self.assertTrue(contains_forbidden_mob_advice(bad_creeper, creeper_details))
        self.assertTrue(contains_forbidden_mob_advice(bad_zombie, zombie_details))
        self.assertFalse(contains_forbidden_mob_advice(good, creeper_details))
        self.assertFalse(is_style_acceptable("player_chat", bad_zombie, zombie_details))
        self.assertTrue(is_style_acceptable("player_chat", good, creeper_details))

class DialogueContextUnitTests(unittest.TestCase):
    def test_keeps_five_exchanges(self) -> None:
        ctx = DialogueContext()
        for index in range(6):
            ctx.add_player(f"p{index}")
            ctx.add_dogido(f"d{index}")
        lines = ctx.conversation_lines()
        self.assertEqual(10, len(lines))
        self.assertTrue(lines[0].startswith("プレイヤー: p1"))
        self.assertTrue(lines[-1].startswith("ドギド: d5"))

    def test_prompt_includes_history_and_digest(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "さっきの話な",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "conversation_history": "プレイヤー: おはよう\nドギド: おはようさん",
                    "event_digest": "- 牛を見た\n- ゾンビを2体倒した",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("【直近の会話】", content)
        self.assertIn("プレイヤー: おはよう", content)
        self.assertIn("【直近の出来事メモ】", content)
        self.assertIn("ゾンビを2体倒した", content)


class DialogueContextServiceTests(unittest.TestCase):
    def test_service_accumulates_chat_history(self) -> None:
        with TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    decision_policy="py_trees",
                    memory_dir=Path(tmp) / "memory",
                )
            )
            service.process_event(make_event(sequence=1, at_sec=0.0))
            service.push_player_input("おはようさん")
            service.process_event(make_event(sequence=2, at_sec=1.0))
            service.push_player_input("元気？")
            service.process_event(make_event(sequence=3, at_sec=2.0))

            session = next(iter(service.sessions.values()))
            lines = session.dialogue.conversation_lines()
            self.assertGreaterEqual(len(lines), 2)
            self.assertTrue(any("おはようさん" in line for line in lines))
            self.assertTrue(any(line.startswith("ドギド:") for line in lines))

    def test_inventory_gain_and_ambient_become_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    decision_policy="py_trees",
                    memory_dir=Path(tmp) / "memory",
                    player_input_ambient_mute_ms=0,
                    player_input_priority_cooldown_ms=0,
                    ambient_mob_comment_cooldown_ms=0,
                )
            )
            service.process_event(make_event(sequence=1, at_sec=0.0, inventory={"stick": 1}))
            service.process_event(make_event(sequence=2, at_sec=1.0, inventory={"stick": 1, "beef": 2}))
            session = next(iter(service.sessions.values()))
            digest = "\n".join(session.dialogue.digest_lines())
            self.assertIn("入手", digest)

            ambient = make_event(sequence=3, at_sec=2.0, inventory={"stick": 1, "beef": 2})
            ambient.event = EventDescriptor(
                name=EventName.AMBIENT_MOB_DETECTED,
                source_kind=SourceKind.VISUAL,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            )
            ambient.passive_mobs = [
                PassiveMob(
                    type="cow",
                    distance=4.0,
                    direction=Direction(horizontal=HorizontalDirection.FRONT),
                )
            ]
            service.process_event(ambient)
            digest = "\n".join(session.dialogue.digest_lines())
            self.assertIn("を見た", digest)


if __name__ == "__main__":
    unittest.main()


class PlayerChatHearingBufferTests(unittest.TestCase):
    def test_hearing_uses_recent_buffer_when_current_frame_empty(self) -> None:
        """今フレームに音が無くても、直近バッファのカタログ解決名が渡る。"""
        from dogido_server.config import Settings
        from dogido_server.state_machine import DogidoStateMachine

        settings = Settings(
            llm_enabled=False,
            decision_policy="py_trees",
            player_chat_hearing_retention_ms=12000,
        )
        machine = DogidoStateMachine(settings)
        t0 = datetime(2026, 7, 15, 9, 17, 40, tzinfo=timezone.utc)

        def make_event(
            sequence: int,
            observed: datetime,
            *,
            auditory: list | None = None,
            user_text: str | None = None,
        ) -> GameEvent:
            return GameEvent.model_validate(
                {
                    "schema_version": "2026-05-24",
                    "adapter": "unit-test",
                    "observed_at": observed.isoformat(),
                    "sequence": sequence,
                    "event": {
                        "name": "status_snapshot",
                        "source_kind": "system",
                        "priority_hint": "background",
                        "certainty": "high",
                    },
                    "player": {
                        "name": "tester",
                        "position": {"x": 0, "y": 64, "z": 0},
                        "dimension": "minecraft:overworld",
                    },
                    "world": {
                        "time_phase": "day",
                        "weather": "clear",
                        "biome": "taiga",
                        "local_light": 15,
                        "sky_visible": True,
                    },
                    "auditory_threats": auditory or [],
                    "ambient_sounds": [],
                    "meta": {"user_text": user_text},
                }
            )

        # 8 秒前: sound_event から zombie を解決（spoken_name_allowed は false でも type は取れる）
        machine.process(
            make_event(
                1,
                t0,
                auditory=[
                    {
                        "label": "hostile_presence",
                        "spoken_name_allowed": False,
                        "sound_event": "entity.zombie.ambient",
                        "distance_band": "far",
                        "direction": {"horizontal": "front"},
                    }
                ],
            )
        )
        self.assertTrue(machine.state.recent_hearing_memos)
        self.assertEqual(machine.state.recent_hearing_memos[0].label_ja, "ゾンビ")

        later = t0 + timedelta(seconds=8)
        empty = make_event(2, later)
        summary = machine._player_chat_hearing_summary(empty)
        named = machine._player_chat_hearing_named_mobs(empty)
        self.assertIn("ゾンビ", summary)
        self.assertIn("ついさっき", summary)
        self.assertEqual(named, ["ゾンビ"])

        # プロンプトに allowlist が載る
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="おう",
                details={
                    "user_text": "まだなんか低い声が聞こえるような",
                    "player_name": "tester",
                    "biome": "タイガ",
                    "time_phase": "day",
                    "mode": "normal",
                    "character_mode": "peace",
                    "threat_summary": "とくになし",
                    "hearing_summary": summary,
                    "hearing_named_mobs": named,
                },
            )
        )
        user = messages[1]["content"]
        self.assertIn("ゾンビ", user)
        self.assertIn("音から使ってよい具体モブ名", user)


class PlayerChatVisualBufferTests(unittest.TestCase):
    def test_visual_buffer_survives_empty_chat_frame(self) -> None:
        """今フレーム visual 0 でも、直近の種名が threat_summary / stance / 白リストに残る。"""
        from dogido_server.config import Settings
        from dogido_server.player_chat_policy import resolve_reply_stance
        from dogido_server.state_machine import DogidoStateMachine

        settings = Settings(
            llm_enabled=False,
            decision_policy="py_trees",
            player_chat_visual_retention_ms=12000,
        )
        machine = DogidoStateMachine(settings)
        t0 = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)

        def make_event(
            sequence: int,
            observed: datetime,
            *,
            visual: list | None = None,
            user_text: str | None = None,
        ) -> GameEvent:
            return GameEvent.model_validate(
                {
                    "schema_version": "2026-05-24",
                    "adapter": "unit-test",
                    "observed_at": observed.isoformat(),
                    "sequence": sequence,
                    "event": {
                        "name": "status_snapshot",
                        "source_kind": "system",
                        "priority_hint": "background",
                        "certainty": "high",
                    },
                    "player": {
                        "name": "tester",
                        "position": {"x": 0, "y": 64, "z": 0},
                        "dimension": "minecraft:overworld",
                    },
                    "world": {
                        "time_phase": "day",
                        "weather": "clear",
                        "biome": "taiga",
                        "local_light": 15,
                        "sky_visible": True,
                    },
                    "visual_threats": visual or [],
                    "meta": {"user_text": user_text},
                }
            )

        machine.process(
            make_event(
                1,
                t0,
                visual=[
                    {
                        "type": "pillager",
                        "entity_id": "p1",
                        "distance": 12.0,
                        "direction": {"horizontal": "front"},
                        "certainty": "high",
                    }
                ],
            )
        )
        self.assertTrue(machine.state.recent_visual_memos)
        self.assertEqual(machine.state.recent_visual_memos[0].mob_type, "pillager")

        later = t0 + timedelta(seconds=8)
        empty = make_event(2, later, user_text="あいつらどこやった？")
        summary = machine._player_chat_threat_summary(empty)
        recent_types = machine._player_chat_recent_visual_types(empty)
        self.assertIn("ついさっき", summary)
        self.assertIn("視認", summary)
        self.assertIn("ピリジャー", summary)
        self.assertEqual(recent_types, ["pillager"])
        self.assertEqual(
            resolve_reply_stance(
                has_visual_threats=bool(recent_types),
                topic_hits=[],
                threat_summary=summary,
                user_text="あいつらどこやった？",
            ),
            "saw",
        )

        # 期限切れ
        expired = make_event(3, t0 + timedelta(seconds=20))
        self.assertEqual(machine._player_chat_recent_visual_types(expired), [])
        self.assertNotIn("ついさっき", machine._player_chat_threat_summary(expired) or "")

        # 白リストにバッファ種が載る
        from dogido_server.player_chat_policy import build_allowed_speech_labels

        labels = build_allowed_speech_labels(
            topic_hits=[],
            visual_types=recent_types,
            hearing_named_mobs=[],
        )
        self.assertIn("ピリジャー", labels)


class PlayerChatPlaceContextTests(unittest.TestCase):
    def test_underground_context_despite_surface_biome(self) -> None:
        """地表バイオームでも sky_visible=false なら地下っぽい空間として渡す。"""
        from dogido_server.config import Settings
        from dogido_server.llm.prompts import build_messages
        from dogido_server.llm.types import LeafGenerationRequest
        from dogido_server.state_machine import DogidoStateMachine

        machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))
        event = GameEvent.model_validate(
            {
                "schema_version": "2026-05-24",
                "adapter": "unit-test",
                "observed_at": "2026-07-15T12:00:00+09:00",
                "event": {
                    "name": "status_snapshot",
                    "source_kind": "system",
                    "priority_hint": "background",
                    "certainty": "high",
                },
                "player": {
                    "name": "tester",
                    "position": {"x": 0, "y": 32, "z": 0},
                    "dimension": "minecraft:overworld",
                },
                "world": {
                    "time_phase": "day",
                    "weather": "clear",
                    "biome": "birch_forest",
                    "local_light": 4,
                    "sky_visible": False,
                    "ceiling_height": 4.0,
                    "enclosure_score": 0.6,
                    "overhead_cover_type": "solid",
                    "danger_darkness_score": 0.5,
                },
            }
        )
        place = machine._player_chat_place_context(event)
        self.assertEqual(place["space_kind"], "underground_or_roofed")
        self.assertFalse(place["sky_visible"])
        self.assertIn("シラカバ", place["place_line"])  # 地表バイオームは残る
        self.assertIn("空は見えない", place["place_line"])
        self.assertIn("地下", place["place_line"])

        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="おう",
                details={
                    "user_text": "ここどこや？",
                    "player_name": "tester",
                    "biome": place["biome_label"],
                    "place_context": place["place_line"],
                    "space_kind": place["space_kind"],
                    "time_phase": "day",
                    "mode": "normal",
                    "character_mode": "peace",
                    "threat_summary": "とくになし",
                    "hearing_summary": "",
                    "hearing_named_mobs": [],
                },
            )
        )
        user = messages[1]["content"]
        self.assertIn("場所メモ:", user)
        self.assertIn("地下", user)
        # S1: 場所は place_line を正とし、長文ルールは載せない
        self.assertNotIn("バイオーム名だけ見て地上", user)

    def test_open_surface_day(self) -> None:
        from dogido_server.config import Settings
        from dogido_server.state_machine import DogidoStateMachine

        machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))
        event = GameEvent.model_validate(
            {
                "schema_version": "2026-05-24",
                "adapter": "unit-test",
                "observed_at": "2026-07-15T12:00:00+09:00",
                "event": {
                    "name": "status_snapshot",
                    "source_kind": "system",
                    "priority_hint": "background",
                    "certainty": "high",
                },
                "player": {
                    "name": "tester",
                    "position": {"x": 0, "y": 72, "z": 0},
                    "dimension": "minecraft:overworld",
                },
                "world": {
                    "time_phase": "day",
                    "weather": "clear",
                    "biome": "birch_forest",
                    "local_light": 15,
                    "sky_visible": True,
                    "ceiling_height": 64.0,
                    "enclosure_score": 0.05,
                    "overhead_cover_type": "none",
                },
            }
        )
        place = machine._player_chat_place_context(event)
        self.assertEqual(place["space_kind"], "open_surface")
        self.assertIn("空が見える", place["place_line"])
