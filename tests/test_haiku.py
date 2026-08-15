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
    VehicleState,
    Weather,
    WorldState,
)
from dogido_server.entry_catalog import mob_poetic_line, mob_poetic_tags
from dogido_server.llm.haiku_prompts import (
    build_haiku_draft_messages,
    build_haiku_irony_messages,
    build_haiku_scene_messages,
)
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.haiku.source_atoms import PrefaceClause
from dogido_server.state_machine.haiku_context import SceneContext


def grounded_haiku_payload(
    request: StructuredGenerationRequest,
    lines: tuple[str, str, str],
) -> dict[str, object] | None:
    """state machine テスト用。最終句の新しい厳格JSON契約だけを返す。"""

    if request.kind == "haiku_preface_grounding":
        return {
            "assessments": [
                {
                    "clause_index": index,
                    "basis_atom_ids": clause["basis_atom_ids"],
                    "claim_class": clause["claim_class"],
                    "meaning_retained": True,
                    "class_correct": True,
                    "within_claim_scope": True,
                    "natural_japanese": True,
                }
                for index, clause in enumerate(request.details.get("preface_clauses", []))
            ]
        }
    if request.kind == "haiku_draft":
        return {"lines": list(lines)}
    if request.kind != "haiku_line_grounding":
        return None
    atoms = [
        atom for atom in request.details.get("source_atoms", [])
        if isinstance(atom, dict) and atom.get("atom_id")
    ]
    grounding_lines = [
        row for row in request.details.get("grounding_lines", [])
        if isinstance(row, dict)
    ]
    return {
        "assessments": [
            {
                "line_index": row["line_index"],
                "atom_ids": [atoms[index]["atom_id"]],
                "meaning_retained": True,
                "natural_japanese": True,
            }
            for index, row in enumerate(grounding_lines)
        ]
    }


def scene_payload(
    request: StructuredGenerationRequest,
    text: str,
    *,
    motifs: tuple[str, ...] = (),
    focus: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> dict[str, object]:
    """state machine テスト用の節単位preface契約。"""

    atom_ids = [
        atom["atom_id"]
        for atom in request.details.get("source_atoms", [])
        if isinstance(atom, dict) and isinstance(atom.get("atom_id"), str)
    ]
    if not atom_ids:
        return {"found": False}
    return {
        "found": True,
        "clauses": [{
            "text": text,
            "basis_atom_ids": atom_ids[:3],
            "claim_class": "interpretive",
        }],
        "motifs": list(motifs),
        "focus": list(focus),
        "confidence": confidence,
    }


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
    structure: str | None = None,
    vehicle: VehicleState | None = None,
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
            vehicle=vehicle,
        ),
        world=WorldState(
            time_of_day=time_of_day,
            time_phase=time_phase,
            weather=Weather.CLEAR,
            biome=biome,
            structure=structure,
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
        # 通常デフォルト（10分周期 + 30秒静寂）は
        # test_haiku_emits_on_interval_after_quiet_window で検証する。
        self.settings = Settings(
            llm_enabled=False,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        self.machine = DogidoStateMachine(self.settings)
        self.base_time = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

    def test_generation_defaults_are_explicit(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.haiku_interval_ms, 600000)
        self.assertEqual(settings.haiku_generation_strategy, "three_slot")
        self.assertEqual(settings.haiku_max_regeneration_rounds, 6)

    def test_haiku_emits_on_interval_after_quiet_window(self) -> None:
        machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))

        # 初回イベントから通常設定の10分周期が始まる
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
            machine.process(make_snapshot(self.base_time + timedelta(seconds=1180))).actions,
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
        from dogido_server.state_machine.fallback_catalog import fallback_text

        self.assertEqual(chat_actions[0].text, fallback_text("general", "chat", "reply"))

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
        context = self.machine._haiku_context(event)
        candidates = context.feature_candidate_labels()
        self.assertIn("地帯 冷帯バイオーム", candidates)
        self.assertFalse(any("Y座標" in label or "Z座標" in label for label in candidates))
        self.assertFalse(any("降水 0." in label or "気温 0." in label for label in candidates))
        details = context.prompt_details()
        for internal_key in ("current_y", "biome_temperature", "snow_start_y", "snowfall_zone", "z_value"):
            self.assertNotIn(internal_key, details)
        self.assertEqual(details["snowfall_environment"], "no")
        self.assertIn("雪や積雪を現在場面の材料にしない", details["weather_context"])
        prompt = build_haiku_draft_messages(details)[1]["content"]
        for leaked_value in (
            "Y座標",
            "Z座標",
            "現在Y",
            "降雪開始Y",
            "バイオーム基準気温",
            "降水 0.8",
        ):
            self.assertNotIn(leaked_value, prompt)

    def test_haiku_vehicle_material_keeps_player_as_subject(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="plains",
            vehicle=VehicleState(
                vehicle_id="minecraft:horse",
                activity="running",
                controlling=True,
            ),
        )
        context = self.machine._haiku_context(event)
        fact = "プレイヤーはウマに乗って走っている"

        self.assertIn(f"乗車 {fact}", context.feature_candidate_labels())
        vehicle_atom = next(
            atom for atom in context.source_atoms
            if atom.observation_role == "vehicle_activity"
        )
        self.assertEqual(vehicle_atom.text, fact)
        self.assertIn(fact, build_haiku_draft_messages(context.prompt_details())[1]["content"])

    def test_haiku_uses_observed_snow_or_active_snowfall_only(self) -> None:
        observed = make_snapshot(
            self.base_time,
            biome="taiga",
            nearby_resources=[NearbyResource(type="block", name="minecraft:snow", distance=2.0)],
        )
        observed_context = self.machine._haiku_context(observed)
        self.assertTrue(observed_context.prompt_details()["surface_snow_observed"])
        self.assertIn("周辺 雪", observed_context.feature_candidate_labels())

        falling = make_snapshot(
            self.base_time,
            biome="taiga",
            player_y=160,
        )
        falling = falling.model_copy(
            update={"world": falling.world.model_copy(update={"weather": Weather.RAIN})}
        )
        falling_context = self.machine._haiku_context(falling)
        self.assertEqual(falling_context.weather_label, "雪")
        self.assertEqual(falling_context.prompt_details()["precipitation_kind"], "snow")
        self.assertIn("降雪 現在は雪", falling_context.feature_candidate_labels())

    def test_structure_present_prefers_structure_over_biome_candidates(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="desert",
            structure="desert_pyramid",
        )
        context = self.machine._haiku_context(event)
        labels = context.feature_candidate_labels()
        self.assertTrue(any(label.startswith("構造物 ") for label in labels), msg=labels)
        self.assertTrue(any("ピラミッド" in label for label in labels), msg=labels)
        # biome 名を主役候補に載せない
        self.assertFalse(any(label.startswith("バイオーム ") for label in labels), msg=labels)
        self.assertFalse(any(label.startswith("地帯 ") for label in labels), msg=labels)
        # 気候は参考程度
        self.assertTrue(any(label.startswith("気候 ") for label in labels), msg=labels)
        self.assertEqual(context.structure_label, "砂漠のピラミッド")
        self.assertTrue(context.climate_hint)

        details = context.irony_details()
        irony_user = build_haiku_irony_messages(details)[1]["content"]
        self.assertIn("いまの材料", irony_user)
        self.assertIn("砂漠のピラミッド", irony_user)
        self.assertIn("いまいる場所", irony_user)
        self.assertIn("場所の空気", irony_user)
        # biome を状況の主役行にしない（structure 時）
        self.assertNotIn("いまの景色:", irony_user)

        haiku_user = build_haiku_draft_messages(details)[1]["content"]
        self.assertIn("いまの材料", haiku_user)
        self.assertIn("砂漠のピラミッド", haiku_user)
        self.assertIn("行の出典", haiku_user)
        self.assertIn("ドギド", haiku_user)

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
                haiku = grounded_haiku_payload(
                    request,
                    ("すなあつめ", "くりーぱーくる", "こわいわあ"),
                )
                if haiku is not None:
                    return haiku
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "深い地下でヒツジがのんびりしとる",
                        motifs=("地下", "ヒツジ"),
                        focus=("地下", "ヒツジ"),
                    )
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
        self.assertEqual(len(fake_llm.structured_requests), 5)
        self.assertEqual(fake_llm.structured_requests[0].route, "chat")
        self.assertEqual(fake_llm.structured_requests[0].kind, "haiku_irony")
        self.assertEqual(fake_llm.structured_requests[0].max_tokens, self.settings.haiku_structured_max_tokens)
        self.assertEqual(fake_llm.structured_requests[1].route, "chat")
        self.assertEqual(fake_llm.structured_requests[1].kind, "haiku_scene")
        self.assertEqual(fake_llm.structured_requests[1].max_tokens, self.settings.haiku_structured_max_tokens)
        self.assertFalse(any(request.kind == "haiku" for request in fake_llm.leaf_requests))
        preface_grounding = fake_llm.structured_requests[2]
        draft = fake_llm.structured_requests[3]
        grounding = fake_llm.structured_requests[4]
        self.assertEqual((preface_grounding.kind, preface_grounding.route), ("haiku_preface_grounding", "chat"))
        self.assertEqual((draft.kind, draft.route, draft.temperature), ("haiku_draft", "haiku", 0.60))
        self.assertEqual(draft.details["generation_strategy"], "three_slot")
        self.assertEqual(draft.details["generation_slot_groups"], [[0], [1], [2]])
        self.assertEqual((grounding.kind, grounding.route), ("haiku_line_grounding", "chat"))
        assert machine.emitted_haiku is not None
        self.assertEqual(len(machine.emitted_haiku.materials["line_sources"]), 3)
        self.assertEqual(machine.emitted_haiku.materials["generation_strategy"], "three_slot")
        self.assertEqual(
            machine.emitted_haiku.materials["prompt_variant"],
            "source_atoms_slots_v2_kana_normalize",
        )
        self.assertEqual(machine.emitted_haiku.materials["regeneration_rounds"], 0)
        self.assertTrue(machine.emitted_haiku.materials["catalog_sources"])

    def test_llm_haiku_emits_preface_before_generation(self) -> None:
        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                return "すなあつめ\nくりーぱーくる\nこわいわあ"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                haiku = grounded_haiku_payload(
                    request,
                    ("すなあつめ", "くりーぱーくる", "こわいわあ"),
                )
                if haiku is not None:
                    return haiku
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "深い地下でヒツジがのんびりしとる",
                        motifs=("地下", "ヒツジ"),
                        focus=("地下", "ヒツジ"),
                    )
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

        preface_text = preface[0].text if preface else ""
        # 見どころだけ。「ここで一句。」は本句側のみ
        self.assertNotIn("ここで一句", preface_text)
        self.assertIn("浮かんできた", preface_text)
        self.assertEqual([action.text for action in final_line], ["すなあつめ\nくりーぱーくる\nこわいわあ"])
        assert machine.emitted_haiku is not None
        saved_atoms = machine.emitted_haiku.materials["source_atoms"]
        preface_atoms = [atom for atom in saved_atoms if atom["kind"] == "preface_clause"]
        self.assertTrue(preface_atoms)
        self.assertTrue(all(atom["basis_atom_ids"] for atom in preface_atoms))
        self.assertTrue(all(atom["claim_class"] in {"factual", "interpretive"} for atom in preface_atoms))
        self.assertEqual(machine.emitted_haiku.materials["preface_spoken"], preface_text)

    def test_haiku_zone_ignores_player_chat_until_verse(self) -> None:
        """自分の世界: preface 中は話しかけより本句完了を優先する。"""

        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request: LeafGenerationRequest) -> str:
                return "あさのひに\nむらびとあるく\nきをかかえ"

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                haiku = grounded_haiku_payload(
                    request,
                    ("あさのひに", "むらびとあるく", "きをかかえ"),
                )
                if haiku is not None:
                    return haiku
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "朝の村で木を持つあんた",
                        motifs=("朝", "村", "木"),
                        focus=("村",),
                        confidence=0.85,
                    )
                return {
                    "found": True,
                    "kind": "contrast",
                    "description": "朝の村と手持ちの木の対比",
                    "elements": ["朝", "村", "木"],
                    "focus": ["村"],
                    "confidence": 0.85,
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
                biome="plains",
                passive_mobs=[sheep],
                held_item="minecraft:oak_log",
            )
        )
        preface = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="plains",
                passive_mobs=[sheep],
                held_item="minecraft:oak_log",
            )
        ).actions
        self.assertTrue(machine.state.pending_haiku_after_preface)
        self.assertTrue(preface and (preface[0].text or "").strip())
        self.assertNotIn("ここで一句", (preface[0].text or ""))

        # 詠みの途中に話しかけても本句が先
        verse = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=302),
                biome="plains",
                passive_mobs=[sheep],
                held_item="minecraft:oak_log",
                user_text="ねえ聞こえる？",
            )
        ).actions
        self.assertEqual([a.text for a in verse], ["あさのひに\nむらびとあるく\nきをかかえ"])
        self.assertFalse(machine.state.pending_haiku_after_preface)

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
                haiku = grounded_haiku_payload(
                    request,
                    ("ぽーたるの", "ひかりのさきへ", "いざゆかん"),
                )
                if haiku is not None:
                    return haiku
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "要塞のポータル前で支度を整えている",
                        motifs=("ポータル", "要塞"),
                        focus=("ポータル",),
                    )
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

        # 必ず発句 → 本句の二段階で出る（preface に「ここで一句」は付けない）
        preface_text = preface[0].text if preface else ""
        self.assertTrue(preface_text, msg=preface)
        self.assertNotIn("ここで一句", preface_text)
        self.assertEqual([action.text for action in final_line], ["ぽーたるの\nひかりのさきへ\nいざゆかん"])
        # 情景・取り合わせの思考（irony/scene）もポータル近くで省略されない
        self.assertIn("haiku_irony", fake_llm.structured_kinds)
        self.assertIn("haiku_scene", fake_llm.structured_kinds)

    def test_missing_scene_still_runs_grounded_generator(self) -> None:
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
        settings = Settings(
            llm_enabled=True,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        machine = DogidoStateMachine(settings, llm=fake_llm)
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
        preface = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=301),
                biome="forest",
                time_phase="day",
                player_y=64,
                held_item="minecraft:air",
                inventory={},
            )
        ).actions
        emitted = machine.process(
            make_snapshot(
                self.base_time + timedelta(seconds=302),
                biome="forest",
                time_phase="day",
                player_y=64,
                held_item="minecraft:air",
                inventory={},
            )
        ).actions

        self.assertEqual([action.text for action in preface], ["なんか浮かんできたわ。"])
        self.assertEqual(
            [request.kind for request in fake_llm.structured_requests],
            ["haiku_irony", "haiku_scene", "haiku_draft"],
        )
        self.assertEqual(fake_llm.leaf_requests, [])
        self.assertEqual(emitted[0].text, "まとまらんかった。。。")
        self.assertIsNone(machine.emitted_haiku)

    def test_invalid_scene_contract_logs_rejection_and_does_not_use_catalog_fallback(self) -> None:
        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                if request.kind == "haiku_scene":
                    return {
                        "text": "昼の森に光が落ちる",
                        "basis_atom_ids": ["observation:observed:biome"],
                    }
                return {"found": False}

        settings = Settings(
            llm_enabled=True,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        machine = DogidoStateMachine(settings, llm=FakeLLM())
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
            machine.process(
                make_snapshot(
                    self.base_time + timedelta(seconds=301),
                    biome="forest",
                    time_phase="day",
                    player_y=64,
                    held_item="minecraft:air",
                    inventory={},
                )
            )
            emitted = machine.process(
                make_snapshot(
                    self.base_time + timedelta(seconds=302),
                    biome="forest",
                    time_phase="day",
                    player_y=64,
                    held_item="minecraft:air",
                    inventory={},
                )
            ).actions

        self.assertEqual(emitted[0].text, "まとまらんかった。。。")
        self.assertIsNone(machine.emitted_haiku)
        self.assertTrue(
            any(
                "haiku_scene result=rejected reason=invalid_contract" in line
                for line in captured.output
            )
        )
        self.assertTrue(
            any(
                "haiku_grounding result=fallback reason=invalid_draft" in line
                for line in captured.output
            )
        )
        self.assertTrue(
            any("haiku_emit result=failed_no_pin" in line for line in captured.output)
        )
        self.assertFalse(any("reason=weak_scene" in line for line in captured.output))
        self.assertFalse(any("ふみだして" in line for line in captured.output))

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

    def test_grounding_failure_is_not_pinned_or_saved_as_a_haiku(self) -> None:
        class FakeLLM:
            def preload(self) -> bool:
                return False

            def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
                if request.kind == "haiku_preface_grounding":
                    return grounded_haiku_payload(
                        request,
                        ("はるのかぜ", "ひつじがあるく", "よるのつき"),
                    ) or {"assessments": []}
                if request.kind == "haiku_irony":
                    return {"found": False}
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "晴れた草地を羊が歩いとる",
                        motifs=("草地", "羊"),
                        focus=("羊",),
                    )
                if request.kind == "haiku_draft":
                    return {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]}
                if request.kind == "haiku_line_regeneration":
                    current = {
                        row["line_index"]: row["text"]
                        for row in request.details["current_lines"]
                    }
                    return {
                        "lines": [
                            {"line_index": index, "text": current[index]}
                            for index in request.details["failed_line_indices"]
                        ]
                    }
                atoms = request.details["source_atoms"]
                return {
                    "assessments": [
                        {
                            "line_index": row["line_index"],
                            "atom_ids": [atoms[index]["atom_id"]],
                            "meaning_retained": True,
                            "natural_japanese": False,
                        }
                        for index, row in enumerate(request.details["grounding_lines"])
                    ]
                }

        settings = Settings(
            llm_enabled=True,
            decision_policy="py_trees",
            haiku_interval_ms=300000,
            haiku_quiet_time_ms=300000,
        )
        machine = DogidoStateMachine(settings, llm=FakeLLM())
        machine.process(make_snapshot(self.base_time, biome="meadow"))
        machine.process(make_snapshot(self.base_time + timedelta(seconds=301), biome="meadow"))
        final = machine.process(
            make_snapshot(self.base_time + timedelta(seconds=302), biome="meadow")
        ).actions

        self.assertEqual([action.text for action in final], ["まとまらんかった。。。"])
        self.assertIsNone(machine.emitted_haiku)

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
                haiku = grounded_haiku_payload(
                    request,
                    ("のにいでて", "ひうちいしもつ", "あまいみや"),
                )
                if haiku is not None:
                    return haiku
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "草地で火打石と打ち金を握り、甘い実をしまっとる",
                        motifs=("草地", "火打石と打ち金", "きらめくスイカの薄切り"),
                        focus=("火打石と打ち金", "きらめくスイカの薄切り"),
                        confidence=0.76,
                    )
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
        assert machine.emitted_haiku is not None
        saved_constraints = machine.emitted_haiku.materials["haiku_constraints"]
        draft_requests = [
            request for request in fake_llm.structured_requests
            if request.kind == "haiku_draft"
        ]
        self.assertEqual(len(draft_requests), 1)
        self.assertEqual(saved_constraints, draft_requests[0].details["haiku_constraints"])
        self.assertEqual(draft_requests[0].details["scene"]["spoken_text"], "草地で火打石と打ち金を握り、甘い実をしまっとる")
        self.assertTrue(draft_requests[0].details["scene"]["clauses"][0]["basis_atom_ids"])

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
                haiku = grounded_haiku_payload(
                    request,
                    ("はれののに", "たきびかかえて", "かぜやわら"),
                )
                if haiku is not None:
                    return haiku
                if request.kind == "haiku_scene":
                    return scene_payload(
                        request,
                        "晴れた草地でキャンプファイアを抱えて立っとる",
                        motifs=("草地", "晴れ", "キャンプファイア"),
                        focus=("草地", "キャンプファイア"),
                        confidence=0.76,
                    )
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
        self.assertEqual(fake_llm.leaf_requests, [])

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

        # 火打石は作業道具扱いにしない → 手持ちのまま
        self.assertIn("手持ち 火打石と打ち金", candidates)
        self.assertFalse(any(candidate.startswith("持ち物 ") for candidate in candidates))

    def test_work_tool_held_prefers_weighted_pocket_motif(self) -> None:
        """つるはし手持ち時は所持の非道具を句の主役に（dirt より花を優先）。"""
        event = make_snapshot(
            self.base_time,
            biome="plains",
            held_item="minecraft:diamond_pickaxe",
            inventory={
                "minecraft:diamond_pickaxe": 1,
                "minecraft:dirt": 64,
                "minecraft:poppy": 3,
                "minecraft:cobblestone": 32,
            },
        )
        context = self.machine._haiku_context(event)
        self.assertEqual(context.poem_item_source, "pocket")
        self.assertIn("ポピー", context.held_item)
        labels = context.feature_candidate_labels()
        self.assertTrue(any(x.startswith("持ち物 ") and "ポピー" in x for x in labels))
        self.assertFalse(any("つるはし" in x or "ツルハシ" in x or "ダイヤモンドのツルハシ" in x for x in labels))

    def test_non_tool_held_stays_hand_source(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="plains",
            held_item="minecraft:poppy",
            inventory={"minecraft:dirt": 8, "minecraft:poppy": 1},
        )
        context = self.machine._haiku_context(event)
        self.assertEqual(context.poem_item_source, "hand")
        self.assertIn("ポピー", context.held_item)

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
                clauses=(PrefaceClause(
                    text="雪原でダイヤモンドシャベルを握る",
                    basis_atom_ids=("test:shovel",),
                    claim_class="interpretive",
                    claim_scopes=("poetic_interpretation",),
                ),),
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
        self.machine._begin_prefaced_haiku(event, self.base_time)
        assert self.machine._pending_haiku_materials is not None
        self.assertEqual(
            self.machine._pending_haiku_materials["haiku_constraints"],
            constraints,
        )

    def test_haiku_constraint_details_include_player_lessons(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="snowy_slopes",
            held_item="minecraft:diamond_shovel",
        )
        self.machine.haiku_lessons_provider = lambda: [
            {
                "note": "要素を少し絞って余白を残すとよい",
                "forbidden_fragments": ["謎語"],  # soft のみ。hard に合流しない
                "polarity": "tighten",
            }
        ]
        constraints = self.machine._haiku_constraint_details(
            event,
            SceneContext(
                found=True,
                clauses=(PrefaceClause(
                    text="雪原でダイヤモンドシャベルを握る",
                    basis_atom_ids=("test:shovel",),
                    claim_class="interpretive",
                    claim_scopes=("poetic_interpretation",),
                ),),
                motifs=("ダイヤモンドシャベル", "雪原"),
                focus=("道具の高級感",),
                confidence=0.8,
            ),
        )
        assert constraints is not None
        self.assertIn("余白", constraints["player_lessons"][0])
        # hard は道具語のみ。lesson の fragments は入らない
        self.assertNotIn("謎語", constraints["forbidden_terms"])
        self.assertIn("つるはし", constraints["forbidden_terms"])

    def test_catalog_notes_include_biome_note(self) -> None:
        event = make_snapshot(self.base_time, biome="snowy_taiga")
        context = self.machine._haiku_context(event)

        self.assertTrue(context.catalog_notes)
        joined = "\n".join(context.catalog_notes)
        self.assertIn("雪のタイガ", joined)
        self.assertIn("葉", joined)
        self.assertIn(context.catalog_notes[0], context.irony_details()["catalog_notes"])

    def test_catalog_notes_include_structure_note(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="plains",
            structure="village_plains",
        )
        context = self.machine._haiku_context(event)

        joined = "\n".join(context.catalog_notes)
        self.assertIn("村", joined)
        self.assertIn("鐘", joined)
        # structure note が先頭（場所の主役）
        self.assertTrue(
            context.catalog_notes and "村" in context.catalog_notes[0],
            msg=context.catalog_notes,
        )

    def test_catalog_notes_include_block_note_when_present(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="plains",
            nearby_resources=[
                NearbyResource(type="block", name="minecraft:crimson_nylium", distance=2.0),
            ],
        )
        context = self.machine._haiku_context(event)

        joined = "\n".join(context.catalog_notes)
        self.assertIn("ナイリウム", joined)
        self.assertIn("ネザー", joined)

    def test_catalog_notes_empty_when_no_notes(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="plains",
            nearby_resources=[
                NearbyResource(type="block", name="minecraft:dirt", distance=1.0),
            ],
        )
        context = self.machine._haiku_context(event)

        self.assertEqual(context.catalog_notes, ())

    def test_haiku_context_keeps_ancient_debris_original_and_runtime_atoms(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="nether_wastes",
            held_item="minecraft:ancient_debris",
            inventory={"ancient_debris": 1},
        )

        context = self.machine._haiku_context(event)
        source = next(
            source for source in context.catalog_sources
            if source.source_ref == "item:ancient_debris"
        )
        note_atoms = [
            atom for atom in context.source_atoms
            if atom.source_ref == "item:ancient_debris" and atom.field_path.startswith("note[")
        ]

        self.assertEqual(
            source.note_raw,
            "ネザーに生成される珍しい鉱石。茶色で、ひび割れている。"
            "しかし、非常に高い爆発耐久値を持っており熱に強い。",
        )
        self.assertEqual([atom.field_path for atom in note_atoms], ["note[0]", "note[1]", "note[2]"])

    def test_haiku_prompt_includes_catalog_notes(self) -> None:
        event = make_snapshot(self.base_time, biome="snowy_taiga")
        details = self.machine._haiku_context(event).prompt_details()
        haiku_user = build_haiku_draft_messages(details)[1]["content"]
        irony_user = build_haiku_irony_messages(details)[1]["content"]
        scene_user = build_haiku_scene_messages(details)[1]["content"]

        self.assertIn("ちょっとした知識", haiku_user)
        self.assertIn("雪のタイガ", haiku_user)
        self.assertIn("いまの材料", irony_user)
        self.assertIn("雪のタイガ", irony_user)
        self.assertIn("最上位には必ず found と clauses", scene_user)
        self.assertIn("ID文字列に [ ] を含めない", scene_user)

    def test_poetic_lines_for_passive_mobs_and_dedupe_tags(self) -> None:
        event = make_snapshot(
            self.base_time,
            biome="plains",
            passive_mobs=[
                PassiveMob(type="cow", distance=3.0),
                PassiveMob(type="sheep", distance=5.0),
                PassiveMob(type="minecraft:chicken", distance=6.0),  # prefix 付きでも解決できること
            ],
        )
        context = self.machine._haiku_context(event)

        self.assertEqual(len(context.poetic_lines), 2)
        self.assertTrue(any(line.startswith("ウシ:") for line in context.poetic_lines))
        self.assertTrue(any(line.startswith("ヒツジ:") for line in context.poetic_lines))
        cow_tags = set(mob_poetic_tags("cow"))
        # poetic_lines 済み mob のタグは haiku_tags に再展開しない
        self.assertFalse(cow_tags & set(context.haiku_tags))

        details = context.prompt_details()
        prompt = build_haiku_draft_messages(details)[1]["content"]
        self.assertIn("いきものの声・姿", prompt)
        self.assertIn("ウシ:", prompt)
        self.assertIn("ことばの匂い", prompt)

    def test_mob_poetic_line_format(self) -> None:
        line = mob_poetic_line("cow")
        self.assertIsNotNone(line)
        assert line is not None
        self.assertTrue(line.startswith("ウシ:"))
        self.assertIn("（", line)


if __name__ == "__main__":
    unittest.main()
