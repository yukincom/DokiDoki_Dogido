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
    PassiveMob,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    Weather,
    WorldState,
)
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.haiku_context import SceneContext


def make_snapshot(
    observed_at: datetime,
    *,
    biome: str = "desert",
    time_phase: str = "day",
    time_of_day: int = 6000,
    user_text: str | None = None,
    passive_mobs: list[PassiveMob] | None = None,
    nearby_resources: list[NearbyResource] | None = None,
    player_y: float = 64,
    danger_darkness_score: float = 0.0,
    held_item: str = "minecraft:torch",
    inventory: dict[str, int] | None = None,
    nearby_portal_type: str | None = None,
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
            held_item=held_item,
        ),
        world=WorldState(
            time_of_day=time_of_day,
            time_phase=time_phase,
            weather=Weather.CLEAR,
            biome=biome,
            local_light=15,
            sky_visible=True,
            danger_darkness_score=danger_darkness_score,
            nearby_portal_type=nearby_portal_type,
            nearby_portal_distance=3.0 if nearby_portal_type else None,
        ),
        passive_mobs=list(passive_mobs or []),
        inventory=inventory or {"torch": 2, "oak_log": 4},
        nearby_resources=list(nearby_resources or []),
        meta=MetaState(user_text=user_text),
    )


class HaikuStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        # ルール検証用テストは旧来のタイミング設計（300秒で発句）を維持する。
        # 実運用デフォルト（10分周期 + 30秒静寂）は
        # test_haiku_emits_on_interval_after_quiet_window で検証する。
        self.settings = Settings(
            llm_enabled=False,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        self.machine = DogidoStateMachine(self.settings)
        self.base_time = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_haiku_emits_on_interval_after_quiet_window(self) -> None:
        machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))

        # 初回イベントから10分周期が始まる
        self.assertEqual(machine.process(make_snapshot(self.base_time)).actions, [])
        self.assertEqual(
            machine.process(make_snapshot(self.base_time + timedelta(seconds=599))).actions,
            [],
        )

        # 10分経過 + 30秒以上の静けさ → 発句
        emitted = machine.process(make_snapshot(self.base_time + timedelta(seconds=601))).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 砂集め　燃えろやハスク　ガラス吹き")

        # 詠んだ直後は次の周期まで出ない
        self.assertEqual(
            machine.process(make_snapshot(self.base_time + timedelta(seconds=700))).actions,
            [],
        )

        # 次の周期で再び詠む
        second = machine.process(make_snapshot(self.base_time + timedelta(seconds=1202))).actions
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].text, "ここで一句。 砂集め　燃えろやハスク　ガラス吹き")

    def test_haiku_waits_for_quiet_window_after_priority_activity(self) -> None:
        machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))

        machine.process(make_snapshot(self.base_time))
        # 周期は満ちているが、直前にプレイヤー入力があった場合は静けさを待つ
        machine.process(
            make_snapshot(self.base_time + timedelta(seconds=610), user_text="ねえドギド")
        )
        self.assertEqual(
            machine.process(make_snapshot(self.base_time + timedelta(seconds=620))).actions,
            [],
        )

        # 入力が止んで30秒すぎ + 入力優先クールダウンが明けたら詠む
        emitted = machine.process(make_snapshot(self.base_time + timedelta(seconds=731))).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 砂集め　燃えろやハスク　ガラス吹き")

    def test_user_text_resets_silence_timer(self) -> None:
        self.machine.process(make_snapshot(self.base_time))

        # 話しかけには会話として返事する（返事と同時に静寂タイマーもリセットされる）
        chat_actions = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                user_text="こんにちは",
            )
        ).actions
        self.assertEqual(len(chat_actions), 1)
        self.assertEqual(chat_actions[0].text, "おう、聞こえとるで〜。")

        self.assertEqual(
            self.machine.process(make_snapshot(self.base_time + timedelta(seconds=550))).actions,
            [],
        )

        emitted = self.machine.process(make_snapshot(self.base_time + timedelta(seconds=605))).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 砂集め　燃えろやハスク　ガラス吹き")

    def test_haiku_does_not_emit_while_state_is_alert(self) -> None:
        self.machine.state.mode = "alert"
        self.machine.state.last_non_silent_at = self.base_time

        should_emit = self.machine._should_emit_haiku(
            make_snapshot(self.base_time + timedelta(seconds=301)),
            self.base_time + timedelta(seconds=301),
        )

        self.assertFalse(should_emit)

    def test_sheep_surface_biome_without_sheep_uses_group_fallback_line(self) -> None:
        self.machine.process(make_snapshot(self.base_time, biome="meadow"))
        emitted = self.machine.process(
            make_snapshot(self.base_time + timedelta(seconds=301), biome="meadow")
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 野にいでて　ひつじめえめえ　草がベリ！")

    def test_exact_biome_default_beats_sheep_surface_group_fallback(self) -> None:
        self.machine.process(make_snapshot(self.base_time, biome="windswept_hills"))
        emitted = self.machine.process(
            make_snapshot(self.base_time + timedelta(seconds=301), biome="windswept_hills")
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 うすぎりの　たべかけケーキ　ぎんいろの")

    def test_missing_biome_without_specific_or_group_falls_back_to_under_construction_line(self) -> None:
        self.machine.process(make_snapshot(self.base_time, biome="dark_forest"))
        emitted = self.machine.process(
            make_snapshot(self.base_time + timedelta(seconds=301), biome="dark_forest")
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 今、考え中やねん…")

    def test_tropical_fish_rule_depends_on_mob_not_ocean_biome(self) -> None:
        tropical_fish = PassiveMob(type="tropical_fish")
        self.machine.process(
            make_snapshot(
                self.base_time,
                biome="river",
                passive_mobs=[tropical_fish],
            )
        )
        emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="river",
                passive_mobs=[tropical_fish],
            )
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 おさかなさん　色とりどりの　水の花")

    def test_sheep_rule_is_mob_based_not_biome_limited(self) -> None:
        sheep = PassiveMob(type="sheep")
        self.machine.process(
            make_snapshot(
                self.base_time,
                biome="savanna_plateau",
                passive_mobs=[sheep],
            )
        )
        emitted = self.machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="savanna_plateau",
                passive_mobs=[sheep],
            )
        ).actions
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 野にいでて　ひつじめえめえ　草がベリ！")

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
        self.assertEqual(emitted[0].text, "ここで一句。 しらかばの　ふしめがちょっと　目にみえる")

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
        self.assertEqual(emitted[0].text, "ここで一句。 しんそうや　ダイヤはどこや　怖いわぁ")

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
                if request.kind == "haiku_scene":
                    return {
                        "found": True,
                        "summary": "深い地下でヒツジがのんびりしとる",
                        "motifs": ["地下", "ヒツジ"],
                        "focus": ["地下", "ヒツジ"],
                        "confidence": 0.8,
                    }
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
        sheep = PassiveMob(type="sheep")
        machine.process(
            make_snapshot(
                self.base_time,
                biome="savanna_plateau",
                passive_mobs=[sheep],
                player_y=12,
            )
        )
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="savanna_plateau",
                passive_mobs=[sheep],
                player_y=12,
            )
        ).actions

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。\nすなあつめ\nくりーぱーくる\nこわいわあ")
        self.assertEqual(len(fake_llm.structured_requests), 2)
        self.assertEqual(fake_llm.structured_requests[0].route, "chat")
        self.assertEqual(fake_llm.structured_requests[0].kind, "haiku_irony")
        self.assertEqual(fake_llm.structured_requests[0].max_tokens, self.settings.haiku_structured_max_tokens)
        self.assertEqual(fake_llm.structured_requests[1].route, "chat")
        self.assertEqual(fake_llm.structured_requests[1].kind, "haiku_scene")
        self.assertEqual(fake_llm.structured_requests[1].max_tokens, self.settings.haiku_structured_max_tokens)
        haiku_requests = [request for request in fake_llm.leaf_requests if request.kind == "haiku"]
        self.assertEqual(len(haiku_requests), 1)
        self.assertEqual(haiku_requests[0].route, "haiku")
        self.assertEqual(haiku_requests[0].kind, "haiku")

    def test_llm_haiku_emits_preface_before_generation(self) -> None:
        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                return "すなあつめ\nくりーぱーくる\nこわいわあ"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                if request.kind == "haiku_scene":
                    return {
                        "found": True,
                        "summary": "深い地下でヒツジがのんびりしとる",
                        "motifs": ["地下", "ヒツジ"],
                        "focus": ["地下", "ヒツジ"],
                        "confidence": 0.8,
                    }
                return {
                    "found": True,
                    "kind": "contrast",
                    "description": "深い地下なのにのどか",
                    "elements": ["地下", "ヒツジ"],
                    "focus": ["地下", "ヒツジ"],
                    "confidence": 0.8,
                }

        settings = Settings(
            llm_enabled=True,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        machine = DogidoStateMachine(settings, llm=FakeLLM())
        sheep = PassiveMob(type="sheep")
        machine.process(
            make_snapshot(
                self.base_time,
                biome="savanna_plateau",
                passive_mobs=[sheep],
                player_y=12,
            )
        )

        preface = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="savanna_plateau",
                passive_mobs=[sheep],
                player_y=12,
            )
        ).actions
        final_line = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=302),
                biome="savanna_plateau",
                passive_mobs=[sheep],
                player_y=12,
            )
        ).actions

        self.assertEqual([action.text for action in preface], ["ここで一句。"])
        self.assertEqual([action.text for action in final_line], ["すなあつめ\nくりーぱーくる\nこわいわあ"])

    def test_haiku_near_portal_still_uses_preface_flow_and_full_context(self) -> None:
        # 回帰テスト: 起動済みポータルの近くに居続けても、ポータル専用の近道で
        # 「ここで一句。」抜き・情景抜きの川柳が出てはいけない
        class FakeLLM:
            def __init__(self) -> None:
                self.structured_kinds: list[str] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                return "ぽーたるの\nひかりのさきへ\nいざゆかん"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                self.structured_kinds.append(request.kind)
                if request.kind == "haiku_scene":
                    return {
                        "found": True,
                        "summary": "要塞のポータル前で支度を整えている",
                        "motifs": ["ポータル", "要塞"],
                        "focus": ["ポータル"],
                        "confidence": 0.8,
                    }
                return {"found": False}

        settings = Settings(
            llm_enabled=True,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        fake_llm = FakeLLM()
        machine = DogidoStateMachine(settings, llm=fake_llm)
        sheep = PassiveMob(type="sheep")

        machine.process(
            make_snapshot(self.base_time, passive_mobs=[sheep], nearby_portal_type="end_portal")
        )
        preface = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                passive_mobs=[sheep],
                nearby_portal_type="end_portal",
            )
        ).actions
        final_line = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=302),
                passive_mobs=[sheep],
                nearby_portal_type="end_portal",
            )
        ).actions

        # 必ず発句 → 本句の二段階で出る
        self.assertEqual([action.text for action in preface], ["ここで一句。"])
        self.assertEqual([action.text for action in final_line], ["ぽーたるの\nひかりのさきへ\nいざゆかん"])
        # 情景・取り合わせの思考（irony/scene）もポータル近くで省略されない
        self.assertIn("haiku_irony", fake_llm.structured_kinds)
        self.assertIn("haiku_scene", fake_llm.structured_kinds)

    def test_weak_scene_without_relation_uses_fallback_instead_of_llm(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.leaf_requests: list[LeafGenerationRequest] = []
                self.structured_requests: list[StructuredGenerationRequest] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                self.leaf_requests.append(request)
                return "あおいじゃが\nしろいようせき\nかくれとる"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                self.structured_requests.append(request)
                return {"found": False}

        fake_llm = FakeLLM()
        machine = DogidoStateMachine(self.settings, llm=fake_llm)
        machine.process(
            make_snapshot(
                self.base_time,
                biome="forest",
                time_phase="day",
                player_y=64,
                held_item="minecraft:air",
                inventory={},
            )
        )
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="forest",
                time_phase="day",
                player_y=64,
                held_item="minecraft:air",
                inventory={},
            )
        ).actions

        self.assertEqual(len(fake_llm.structured_requests), 2)
        self.assertEqual(fake_llm.leaf_requests, [])
        self.assertEqual(emitted[0].text, "ここで一句。 ふみだして　風にたなびく　葉の香り")

    def test_weak_scene_logs_fallback_decision_and_emitted_haiku(self) -> None:
        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                return {"found": False}

        machine = DogidoStateMachine(self.settings, llm=FakeLLM())
        machine.process(
            make_snapshot(
                self.base_time,
                biome="forest",
                time_phase="day",
                player_y=64,
                held_item="minecraft:air",
                inventory={},
            )
        )

        with self.assertLogs("uvicorn.error", level="WARNING") as captured:
            emitted = machine.process(
                make_snapshot(
                    self.base_time + timedelta(seconds=301),
                    biome="forest",
                    time_phase="day",
                    player_y=64,
                    held_item="minecraft:air",
                    inventory={},
                )
            ).actions

        self.assertEqual(emitted[0].text, "ここで一句。 ふみだして　風にたなびく　葉の香り")
        self.assertTrue(
            any("haiku_decision result=fallback reason=weak_scene" in line for line in captured.output)
        )
        self.assertTrue(
            any(
                "haiku_emit result=emitted text=ここで一句。" in line
                and "ふみだして" in line
                and "葉の香り" in line
                for line in captured.output
            )
        )

    def test_invalid_structured_haiku_uses_llm_failed_line_instead_of_catalog_fallback(self) -> None:
        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                return {"found": False, "__dogido_status": "invalid_json"}

        machine = DogidoStateMachine(self.settings, llm=FakeLLM())
        machine.process(
            make_snapshot(
                self.base_time,
                biome="meadow",
                time_phase="day",
                held_item="minecraft:campfire",
                inventory={"campfire": 1},
            )
        )
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="meadow",
                time_phase="day",
                held_item="minecraft:campfire",
                inventory={"campfire": 1},
            )
        ).actions

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。 まとまらんかった。。。")

    def test_scene_summary_can_unlock_llm_haiku_when_irony_is_weak(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.leaf_requests: list[LeafGenerationRequest] = []
                self.structured_requests: list[StructuredGenerationRequest] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                self.leaf_requests.append(request)
                return "のにいでて\nひうちいしもつ\nあまいみや"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                self.structured_requests.append(request)
                if request.kind == "haiku_scene":
                    return {
                        "found": True,
                        "summary": "草地で火打石と打ち金を握り、甘い実をしまっとる",
                        "motifs": ["草地", "火打石と打ち金", "きらめくスイカの薄切り"],
                        "focus": ["火打石と打ち金", "きらめくスイカの薄切り"],
                        "confidence": 0.76,
                    }
                return {"found": False}

        fake_llm = FakeLLM()
        machine = DogidoStateMachine(self.settings, llm=fake_llm)
        inventory = {
            "glistering_melon_slice": 1,
            "poisonous_potato": 1,
            "suspicious_stew": 1,
        }
        sheep = PassiveMob(type="sheep")
        machine.process(
            make_snapshot(
                self.base_time,
                biome="meadow",
                passive_mobs=[sheep],
                held_item="minecraft:flint_and_steel",
                inventory=inventory,
            )
        )
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="meadow",
                passive_mobs=[sheep],
                held_item="minecraft:flint_and_steel",
                inventory=inventory,
            )
        ).actions

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。\nのにいでて\nひうちいしもつ\nあまいみや")
        haiku_requests = [request for request in fake_llm.leaf_requests if request.kind == "haiku"]
        self.assertEqual(len(haiku_requests), 1)
        self.assertEqual(haiku_requests[0].details["scene"]["summary"], "草地で火打石と打ち金を握り、甘い実をしまっとる")

    def test_plain_scene_summary_with_weather_and_held_item_can_use_llm(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.leaf_requests: list[LeafGenerationRequest] = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                self.leaf_requests.append(request)
                return "はれののに\nたきびかかえて\nかぜやわら"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                if request.kind == "haiku_scene":
                    return {
                        "found": True,
                        "summary": "晴れた草地でキャンプファイアを抱えて立っとる",
                        "motifs": ["草地", "晴れ", "キャンプファイア"],
                        "focus": ["草地", "キャンプファイア"],
                        "confidence": 0.76,
                    }
                return {"found": False}

        fake_llm = FakeLLM()
        machine = DogidoStateMachine(self.settings, llm=fake_llm)
        machine.process(
            make_snapshot(
                self.base_time,
                biome="plains",
                held_item="minecraft:campfire",
                inventory={"campfire": 1},
            )
        )
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="plains",
                held_item="minecraft:campfire",
                inventory={"campfire": 1},
            )
        ).actions

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].text, "ここで一句。\nはれののに\nたきびかかえて\nかぜやわら")
        self.assertEqual(len(fake_llm.leaf_requests), 1)

    def test_inventory_details_are_condensed_to_close_pair_and_outlier(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="meadow",
            held_item="minecraft:flint_and_steel",
            inventory={
                "flint_and_steel": 1,
                "glistering_melon_slice": 1,
                "poisonous_potato": 1,
                "suspicious_stew": 1,
            },
        )

        context = self.machine._haiku_context(event)

        self.assertEqual(context.inventory_close_pair, ("青くなったジャガイモ", "怪しげなシチュー"))
        self.assertEqual(context.inventory_far_item, "きらめくスイカの薄切り")
        self.assertEqual(
            context.inventory_items,
            ("青くなったジャガイモ", "怪しげなシチュー", "きらめくスイカの薄切り"),
        )

    def test_feature_candidates_do_not_fill_up_with_inventory_items(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="meadow",
            held_item="minecraft:flint_and_steel",
            inventory={
                "flint_and_steel": 1,
                "glistering_melon_slice": 1,
                "poisonous_potato": 1,
                "suspicious_stew": 1,
            },
        )

        candidates = self.machine._haiku_context(event).feature_candidate_labels()

        self.assertIn("手持ち 火打石と打ち金", candidates)
        self.assertFalse(any(candidate.startswith("持ち物 ") for candidate in candidates))

    def test_haiku_prompt_details_include_tool_constraints_from_held_item_and_scene(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="snowy_slopes",
            held_item="minecraft:diamond_shovel",
        )
        constraints = self.machine._haiku_constraint_details(
            event,
            SceneContext(
                found=True,
                summary="雪原でダイヤモンドシャベルを握る",
                motifs=("ダイヤモンドシャベル", "雪原"),
                focus=("道具の高級感",),
                confidence=0.8,
            ),
        )

        self.assertEqual(
            constraints,
            {
                "allowed_terms": ["しゃべる"],
                "forbidden_terms": ["つるはし", "おの", "くわ"],
            },
        )


if __name__ == "__main__":
    unittest.main()
