from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dogido_server.config import Settings
from dogido_server.models import (
    AmbientSound,
    Certainty,
    CombatState,
    Direction,
    DistanceBand,
    EventDescriptor,
    EventName,
    GameEvent,
    HorizontalDirection,
    MetaState,
    NearbyResource,
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
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.player_input import route_player_input
from dogido_server.player_input.guardrails import asks_inventory
from dogido_server.service import DogidoService
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.fallback_catalog import fallback_text

BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)

CHAT_REPLY = fallback_text("general", "chat", "reply")


def make_event(
    *,
    sequence: int,
    at_sec: float = 0.0,
    user_text: str | None = None,
    inventory: dict[str, int] | None = None,
    held_item: str | None = None,
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
            held_item=held_item,
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
        combat=CombatState(),
        meta=MetaState(user_text=user_text),
    )


class PlayerChatReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.text]

    def test_chat_fallback_is_neutral_not_hearing_ack(self) -> None:
        """PR-A: unusable 時などに載る固定文は話題を横取りしない中立文。"""
        self.assertNotIn("聞こえとる", CHAT_REPLY)
        self.assertIn("教えて", CHAT_REPLY)
        texts = self.texts(make_event(sequence=1, user_text="今日もよろしくな"))
        self.assertEqual([CHAT_REPLY], texts)

    def test_player_chat_logs_visual_count_and_types(self) -> None:
        """PR-A: player_chat 時に visual 件数・types をログへ。"""
        event = make_event(sequence=1, user_text="あいつら何？")
        event.visual_threats = [
            VisualThreat(
                type="pillager",
                entity_id="pillager-1",
                distance=12.0,
                direction=Direction(horizontal=HorizontalDirection.FRONT),
                certainty=Certainty.HIGH,
            ),
            VisualThreat(
                type="pillager",
                entity_id="pillager-2",
                distance=14.0,
                direction=Direction(horizontal=HorizontalDirection.LEFT),
                certainty=Certainty.HIGH,
            ),
        ]
        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            self.machine._render_player_chat_reply(event)  # type: ignore[attr-defined]
        joined = "\n".join(captured.output)
        self.assertIn("player_chat_visual count=2", joined)
        self.assertIn("types=pillager,pillager", joined)

    def test_free_chat_gets_reply(self) -> None:
        texts = self.texts(make_event(sequence=1, user_text="今日もよろしくな"))
        self.assertEqual([CHAT_REPLY], texts)

    def test_identify_skeleton_when_llm_off(self) -> None:
        """S3: LLM オフでもババア→ウィッチ骨子が返る。"""
        texts = self.texts(make_event(sequence=1, user_text="なんだあのババア"))
        self.assertEqual(1, len(texts))
        self.assertIn("ウィッチ", texts[0])
        self.assertIn("見えん", texts[0])

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


class InventoryOnDemandTests(unittest.TestCase):
    def test_asks_inventory_detects_possession_and_light_typo(self) -> None:
        self.assertTrue(asks_inventory("どんな明るを持ってたかな"))
        self.assertTrue(asks_inventory("インベントリに何ある？"))
        self.assertTrue(asks_inventory("松明ある？"))
        self.assertTrue(asks_inventory("何持ってる？"))
        self.assertFalse(asks_inventory("おはようさん"))
        self.assertFalse(asks_inventory("もっと明るくして"))  # 「もっと」だけでは所持問いにしない

    def test_route_sets_asks_inventory_flag(self) -> None:
        ctx = route_player_input("どんな明るを持ってたかな")
        self.assertTrue(ctx.asks_inventory)
        ctx2 = route_player_input("今日もよろしく")
        self.assertFalse(ctx2.asks_inventory)

    def test_player_chat_prompt_includes_inventory_only_when_asked(self) -> None:
        with_inventory = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "どんな明るを持ってたかな",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "asks_inventory": True,
                    "inventory_summary": "松明×12、石炭×8",
                    "held_item_label": "石の剣",
                },
            )
        )
        self.assertIn("所持品（インベントリ要約）: 松明×12、石炭×8", with_inventory[1]["content"])
        self.assertIn("手持ち: 石の剣", with_inventory[1]["content"])

        without = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "おはようさん",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "asks_inventory": False,
                    "inventory_summary": "",
                },
            )
        )
        self.assertNotIn("所持品（インベントリ要約）", without[1]["content"])
        # S1: 未提示時は inventory 節・長文規則ごと省略
        self.assertNotIn("所持品リストが与えられていない", without[1]["content"])

    def test_inventory_summary_from_event(self) -> None:
        machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))
        event = make_event(
            sequence=1,
            user_text="インベントリ見して",
            inventory={"torch": 12, "coal": 8, "stick": 3},
            held_item="stone_sword",
        )
        machine.player_input = route_player_input(event.meta.user_text)
        summary = machine._player_chat_inventory_summary(event)  # type: ignore[attr-defined]
        self.assertIn("松明×12", summary)
        self.assertIn("石炭×8", summary)
        self.assertIn("棒×3", summary)


class AmbientMobPriorityTests(unittest.TestCase):
    def _ambient_event(self, *, sequence: int, at_sec: float) -> GameEvent:
        event = make_event(sequence=sequence, at_sec=at_sec)
        event.event = EventDescriptor(
            name=EventName.AMBIENT_MOB_DETECTED,
            source_kind=SourceKind.VISUAL,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        )
        event.passive_mobs = [
            PassiveMob(type="cow", distance=4.0, direction=Direction(horizontal=HorizontalDirection.FRONT))
        ]
        return event

    def test_ambient_mob_uses_same_mute_as_player_priority(self) -> None:
        """ambient も priority と同じ mute（既定 20s）。短い 12s 専用 mute は使わない。"""
        settings = Settings(
            decision_policy="py_trees",
            llm_enabled=False,
            player_input_priority_cooldown_ms=20000,
            ambient_mob_comment_cooldown_ms=1000,
        )
        machine = DogidoStateMachine(settings)
        # 話しかけ
        machine.process(make_event(sequence=1, at_sec=0.0, user_text="やあ"))
        self.assertIsNotNone(machine.state.last_player_input_at)

        # 15秒後: まだ priority mute 中（旧 ambient 12s では復帰していた）
        mid = self._ambient_event(sequence=2, at_sec=15.0)
        mid_result = machine.process(mid)
        mid_texts = [action.text for action in mid_result.actions if action.text]
        self.assertEqual([], mid_texts)

        # 21秒後: mute 明け
        later = self._ambient_event(sequence=3, at_sec=21.0)
        later_result = machine.process(later)
        later_texts = [action.text for action in later_result.actions if action.text]
        self.assertTrue(later_texts, "friendly mob reaction should return after priority mute")

    def test_queued_player_input_blocks_ambient(self) -> None:
        """pending 相乗り待ち（player_input_queued）中は ambient を出さない。"""
        settings = Settings(
            decision_policy="py_trees",
            llm_enabled=False,
            player_input_priority_cooldown_ms=0,
            ambient_mob_comment_cooldown_ms=1000,
        )
        machine = DogidoStateMachine(settings)
        machine.player_input_queued = True
        blocked = machine.process(self._ambient_event(sequence=1, at_sec=0.0))
        self.assertEqual([a.text for a in blocked.actions if a.text], [])

        machine.player_input_queued = False
        ok = machine.process(self._ambient_event(sequence=2, at_sec=2.0))
        self.assertTrue([a.text for a in ok.actions if a.text])


class HearingContextTests(unittest.TestCase):
    def test_asks_about_sound_detects_explicit_sound_queries(self) -> None:
        from dogido_server.player_input.guardrails import asks_about_sound
        from dogido_server.player_input.routing import route_player_input

        self.assertTrue(asks_about_sound("なんか音がしなかった?"))
        self.assertTrue(asks_about_sound("今の音なに？"))
        self.assertTrue(asks_about_sound("聞こえる？"))
        self.assertTrue(asks_about_sound("音楽好き"))
        self.assertTrue(asks_about_sound("この曲なに？"))
        self.assertTrue(asks_about_sound("レコード流そう"))
        self.assertFalse(asks_about_sound("あっ溶岩だ"))
        self.assertFalse(asks_about_sound("おはよう"))
        self.assertTrue(route_player_input("今の音なに？").asks_about_sound)
        self.assertFalse(route_player_input("あっ溶岩だ").asks_about_sound)

    def test_hearing_summary_includes_ambient_villager(self) -> None:
        machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))
        event = make_event(sequence=1, user_text="なんか音がしなかった?")
        event.ambient_sounds = [
            AmbientSound(
                type="villager",
                sound_event="entity.villager.ambient",
                direction=Direction(horizontal=HorizontalDirection.LEFT),
                distance_band=DistanceBand.CLOSE,
                certainty=Certainty.MEDIUM,
            )
        ]
        summary = machine._player_chat_hearing_summary(event)  # type: ignore[attr-defined]
        self.assertIn("村人", summary)
        self.assertIn("左", summary)

    def test_hearing_summary_includes_observed_campfire_sound(self) -> None:
        machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))
        event = make_event(sequence=1, user_text="今の音なに？")
        event.ambient_sounds = [
            AmbientSound(
                type="block:campfire",
                sound_event="block.campfire.crackle",
                direction=Direction(horizontal=HorizontalDirection.RIGHT),
                distance_band=DistanceBand.CLOSE,
                certainty=Certainty.MEDIUM,
            )
        ]

        machine._remember_hearing_for_chat(event, event.observed_at)  # type: ignore[attr-defined]
        summary = machine._player_chat_hearing_summary(event)  # type: ignore[attr-defined]
        sources = machine._player_chat_hearing_source_labels(event)  # type: ignore[attr-defined]

        self.assertIn("焚き火の音", summary)
        self.assertEqual(1, summary.count("焚き火の音"))
        self.assertIn("右", summary)
        self.assertEqual(["焚き火"], sources)

    def test_environment_sound_name_requires_observed_sound_type(self) -> None:
        """近くのブロック名だけから『音がした』とは推測しない。"""
        machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))
        self.assertIsNone(machine._resolve_hearing_environment_label("campfire"))  # type: ignore[attr-defined]
        self.assertEqual(
            "アメジストブロック",
            machine._resolve_hearing_environment_label(  # type: ignore[attr-defined]
                "block:amethyst_block", "block.amethyst_block.chime"
            ),
        )
        self.assertEqual(
            "ホタルの茂み",
            machine._resolve_hearing_environment_label(  # type: ignore[attr-defined]
                "block:firefly_bush", "block.firefly_bush.idle"
            ),
        )

    def test_recent_thunder_sound_is_remembered_for_sound_question(self) -> None:
        """落雷位置が遠く ambient_sounds に無くても、実雷鳴は短期記憶へ残す。"""
        machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))
        heard = make_event(sequence=1)
        heard.world.thunder_sound_recent_ms = 300
        machine._remember_hearing_for_chat(heard, heard.observed_at)  # type: ignore[attr-defined]

        asked = make_event(sequence=2, at_sec=5.0, user_text="今の音なに？")
        summary = machine._player_chat_hearing_summary(asked)  # type: ignore[attr-defined]
        sources = machine._player_chat_hearing_source_labels(asked)  # type: ignore[attr-defined]

        self.assertIn("雷鳴の音", summary)
        self.assertEqual(["雷鳴"], sources)

    def test_hearing_not_injected_unless_player_asks_about_sound(self) -> None:
        """『あっ溶岩だ』など音以外の話題では hearing を details に載せない。"""
        from dogido_server.player_input.routing import route_player_input

        class CaptureLLM:
            def __init__(self) -> None:
                self.details: dict | None = None

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request) -> str:  # type: ignore[no-untyped-def]
                self.details = dict(request.details or {})
                return "溶岩か、気をつけや。"

            def generate_structured_json(self, request) -> dict:  # type: ignore[no-untyped-def]
                return {"found": False}

        llm = CaptureLLM()
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=llm,
        )
        event = make_event(sequence=1, user_text="あっ溶岩だ")
        event.ambient_sounds = [
            AmbientSound(
                type="pig",
                sound_event="entity.pig.ambient",
                direction=Direction(horizontal=HorizontalDirection.FRONT),
                distance_band=DistanceBand.CLOSE,
                certainty=Certainty.HIGH,
            )
        ]
        event.passive_mobs = [
            PassiveMob(type="pig", distance=3.0, direction=Direction(horizontal=HorizontalDirection.FRONT))
        ]
        machine.player_input = route_player_input("あっ溶岩だ")
        # render 経路を直接叩く
        text = machine._render_player_chat_reply(event)  # type: ignore[attr-defined]
        self.assertTrue(text)
        assert llm.details is not None
        self.assertFalse(llm.details.get("asks_about_sound"))
        self.assertEqual(llm.details.get("hearing_summary") or "", "")
        self.assertEqual(list(llm.details.get("hearing_named_mobs") or []), [])

        # 明示の音クエリなら載る
        llm2 = CaptureLLM()
        machine2 = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=llm2,
        )
        event2 = make_event(sequence=2, user_text="なんか音した？")
        event2.ambient_sounds = event.ambient_sounds
        machine2.player_input = route_player_input("なんか音した？")
        machine2._render_player_chat_reply(event2)  # type: ignore[attr-defined]
        assert llm2.details is not None
        self.assertTrue(llm2.details.get("asks_about_sound"))
        self.assertTrue(llm2.details.get("hearing_summary") or llm2.details.get("hearing_named_mobs"))

    def test_player_chat_prompt_forbids_invented_sounds_when_empty(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "なんか音がしなかった?",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "weather_label": "晴れ",
                    "hearing_summary": "",
                    "threat_summary": "",
                    "reply_stance": "none",
                    "reply_policy": "雑談として自然に返す。根拠のない種名・敵・音の捏造はしない。",
                },
            )
        )
        content = messages[1]["content"]
        # E′: 空 hearing は節ごと省略。種名ガードは白リスト／sanitize
        self.assertNotIn("音のメモ:", content)
        self.assertIn("天気は晴れ", content)
        self.assertIn("一人称はオレ", content)
        self.assertIn("セリフだけ返す", content)

    def test_player_chat_details_include_weather_from_world(self) -> None:
        """雨は weather 状態から。hearing とは混ぜない。"""
        event = make_event(sequence=50, user_text="雨が降ってるね")
        event = event.model_copy(
            update={"world": event.world.model_copy(update={"weather": Weather.RAIN})}
        )
        details_holder: dict = {}

        class CaptureLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                details_holder.update(request.details)
                return "ほんまやな、しとしと降っとるわ。"

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                return {}

        llm = CaptureLLM()
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=llm,
        )
        machine.player_input = route_player_input("雨が降ってるね")
        machine._render_player_chat_reply(event)  # type: ignore[attr-defined]
        self.assertEqual(details_holder.get("weather"), "rain")
        self.assertEqual(details_holder.get("weather_label"), "雨")
        # 昼+雨 → 淡々とした事実のみ（安全断定しない）
        self.assertEqual(details_holder.get("weather_fact"), "薄暗い。敵が地上にいる。")
        self.assertFalse(details_holder.get("asks_about_sound"))
        self.assertEqual(details_holder.get("hearing_summary") or "", "")

        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details=dict(details_holder),
            )
        )
        content = messages[1]["content"]
        self.assertIn("天気は雨", content)
        self.assertIn("天気の事実: 薄暗い。敵が地上にいる。", content)
        self.assertIn("モブ音メモとは別", content)
        self.assertNotIn("音のメモ:", content)

        # 夜の雨にはその事実を付けない
        night = event.model_copy(
            update={
                "world": event.world.model_copy(
                    update={"weather": Weather.RAIN, "time_phase": TimePhase.NIGHT}
                )
            }
        )
        details_night: dict = {}

        class Capture2:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                details_night.update(request.details)
                return "夜やな。"

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                return {}

        machine2 = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=Capture2(),
        )
        machine2.player_input = route_player_input("雨やね")
        machine2._render_player_chat_reply(night)  # type: ignore[attr-defined]
        self.assertEqual(details_night.get("weather_fact") or "", "")

    def test_player_chat_receives_same_height_and_snow_evidence_as_haiku(self) -> None:
        event = make_event(sequence=52, user_text="雪はないね")
        event = event.model_copy(
            update={"world": event.world.model_copy(update={"biome": "taiga"})}
        )
        details_holder: dict = {}

        class CaptureLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                details_holder.update(request.details)
                return "せやな、ここには雪は見えへんな。"

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                return {}

        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=CaptureLLM(),
        )
        machine.player_input = route_player_input("雪はないね")
        machine._render_player_chat_reply(event)  # type: ignore[attr-defined]

        self.assertEqual(details_holder["current_y"], 64)
        self.assertEqual(details_holder["snow_start_y"], 153)
        self.assertFalse(details_holder["snowfall_zone"])
        self.assertEqual(details_holder["snow_evidence"], "none")
        prompt = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details=details_holder,
            )
        )[1]["content"]
        self.assertIn("標高・降雪の確定情報", prompt)
        self.assertIn("雪や積雪を現在場面の材料にしない", prompt)

        snowy = event.model_copy(
            update={
                "player": event.player.model_copy(
                    update={"position": event.player.position.model_copy(update={"y": 160.0})}
                ),
                "world": event.world.model_copy(update={"weather": Weather.RAIN}),
                "nearby_resources": [
                    NearbyResource(type="block", name="minecraft:snow_block", distance=2.0)
                ],
            }
        )
        details_snow: dict = {}

        class CaptureSnow(CaptureLLM):
            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                details_snow.update(request.details)
                return "ここは雪やな。"

        snowy_machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=CaptureSnow(),
        )
        snowy_machine.player_input = route_player_input("雪やね")
        snowy_machine._render_player_chat_reply(snowy)  # type: ignore[attr-defined]
        self.assertEqual(details_snow["weather_label"], "雪")
        self.assertEqual(details_snow["snow_evidence"], "observed_surface")

    def test_player_chat_prompt_uses_hearing_when_present(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "なんか音がしなかった?",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "day",
                    "hearing_summary": "村人っぽい声 左 close",
                    "threat_summary": "とくになし",
                    "hearing_named_mobs": ["村人"],
                    "reply_stance": "none",
                    "reply_policy": "雑談として自然に返す。根拠のない種名・敵・音の捏造はしない。",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("村人っぽい声 左 close", content)
        self.assertIn("音から触れてよい具体モブ名: 村人", content)

    def test_player_chat_prompt_uses_observed_environment_source(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="player_chat",
                fallback_text="fallback",
                details={
                    "user_text": "今の音なに？",
                    "mode": "normal",
                    "biome": "平原",
                    "time_phase": "night",
                    "hearing_summary": "焚き火の音 右 close",
                    "hearing_named_mobs": [],
                    "hearing_source_labels": ["焚き火"],
                    "threat_summary": "音メモ: 焚き火の音 右 close",
                    "reply_stance": "saw",
                },
            )
        )
        content = messages[1]["content"]
        self.assertIn("焚き火の音 右 close", content)
        self.assertIn("実再生音から確定した環境音源名: 焚き火", content)


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

    def test_pushed_text_answered_even_on_ambient_mob_event(self) -> None:
        """ambient_mob イベントに相乗りしても話しかけが捨てられない。"""
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))
            service.push_player_input("お風呂に入ってる")

            ambient = make_event(sequence=2, at_sec=1.0)
            ambient.event = EventDescriptor(
                name=EventName.AMBIENT_MOB_DETECTED,
                source_kind=SourceKind.VISUAL,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            )
            ambient.passive_mobs = [
                PassiveMob(
                    type="cow",
                    distance=5.0,
                    direction=Direction(horizontal=HorizontalDirection.FRONT),
                )
            ]
            processed = service.process_event(ambient)
            texts = [action.text for action in processed.actions if action.text]
            self.assertEqual([CHAT_REPLY], texts)

    def test_push_without_session_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            result = service.push_player_input("おーい", source="voice")
            self.assertFalse(result["accepted"])
            self.assertEqual("no_active_session", result["reason"])

    def test_short_voice_noise_is_rejected_without_queueing(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))

            for text in ("はい", "はい。", "おい", "なあ"):
                with self.subTest(text=text):
                    result = service.push_player_input(text, source="voice")
                    self.assertFalse(result["accepted"])
                    self.assertEqual("too_short", result["reason"])

            session = next(iter(service.sessions.values()))
            self.assertIsNone(session.pending_player_text)

    def test_known_stt_noise_is_rejected_without_queueing(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))

            for text in ("Thank", "THANK.", "Thanks!", "Thank you."):
                with self.subTest(text=text):
                    result = service.push_player_input(text, source="voice")
                    self.assertFalse(result["accepted"])
                    self.assertEqual("noise_text", result["reason"])

            session = next(iter(service.sessions.values()))
            self.assertIsNone(session.pending_player_text)

    def test_short_voice_allowlist_is_still_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))

            for text in ("おーい", "ドギド", "うん", "おう"):
                with self.subTest(text=text):
                    result = service.push_player_input(text, source="voice")
                    self.assertTrue(result["accepted"])

            session = next(iter(service.sessions.values()))
            self.assertEqual("おう", session.pending_player_text)

    def test_adapter_chat_wins_over_pending_voice_text(self) -> None:
        with TemporaryDirectory() as tmp:
            service = self.make_service(tmp)
            service.process_event(make_event(sequence=1, at_sec=0.0))
            service.push_player_input("ボイス入力や", source="voice")

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
