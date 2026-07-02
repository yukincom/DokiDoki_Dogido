from __future__ import annotations

import unittest

from dogido_server.config import Settings
from dogido_server.llm import DogidoLLM, LeafGenerationRequest
from dogido_server.models import GameEvent
from dogido_server.state_machine import CHARGED_CREEPER_CALL, DogidoStateMachine, USHIRO_CALL
from dogido_server.state_machine.response_catalog import response_lines

# セリフ推敲に自動追従するよう、データ (combat.json) から直接読む
HOSTILE_MASSIVE_VARIANTS = set(response_lines("combat", "pressure", "hostile_massive_variants"))


class FakeLLM(DogidoLLM):
    def __init__(self) -> None:
        super().__init__(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))

    def generate_leaf_text(self, request):  # type: ignore[override]
        return f"LLM:{request.kind}"


class CaptureLLM(DogidoLLM):
    def __init__(self) -> None:
        super().__init__(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))
        self.requests: list[LeafGenerationRequest] = []

    def generate_leaf_text(self, request):  # type: ignore[override]
        self.requests.append(request)
        return f"LLM:{request.kind}"


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DogidoStateMachine(Settings(audio_enabled=False))

    def test_close_creeper_enters_panic(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 1,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.8,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "creeper",
                  "distance": 5.8,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertEqual(result.state.mode, "panic")
        self.assertTrue(any(action.layer == "panic_cue" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" for action in result.actions))

    def test_ambient_mob_event_emits_llm_line_for_neutral_mob(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:00:00+09:00",
              "sequence": 1,
              "event": {
                "name": "ambient_mob_detected",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true
              },
              "passive_mobs": [
                {
                  "type": "bee",
                  "distance": 4.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "certainty": "high"
                }
              ],
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "normal")
        self.assertTrue(any(action.text == "LLM:ambient" for action in result.actions))

    def test_status_snapshot_can_emit_llm_line_for_passive_mob_when_quiet(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T12:00:00+09:00",
              "sequence": 2,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true
              },
              "passive_mobs": [
                {
                  "type": "fox",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "certainty": "high",
                  "temperament": "friendly"
                }
              ],
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "normal")
        self.assertTrue(any(action.text == "LLM:ambient" for action in result.actions))

    def test_ambient_mob_comment_cooldown_is_per_species(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())

        def mob_event(sequence: int, observed_at: str, mob_type: str) -> GameEvent:
            return GameEvent.model_validate(
                {
                    "schema_version": "2026-05-24",
                    "game": "minecraft-java",
                    "adapter": "dogido-fabric-client",
                    "observed_at": observed_at,
                    "sequence": sequence,
                    "event": {
                        "name": "ambient_mob_detected",
                        "source_kind": "visual",
                        "priority_hint": "background",
                        "certainty": "high",
                    },
                    "player": {"name": "main_player"},
                    "world": {"time_phase": "day", "biome": "plains", "sky_visible": True},
                    "passive_mobs": [
                        {
                            "type": mob_type,
                            "distance": 4.0,
                            "direction": {"horizontal": "front", "vertical": "same"},
                            "certainty": "high",
                        }
                    ],
                    "combat": {"combat_active_hint": False},
                }
            )

        first = machine.process(mob_event(1, "2026-06-04T12:00:00+09:00", "bee"))
        same_species = machine.process(mob_event(2, "2026-06-04T12:00:05+09:00", "bee"))
        other_species = machine.process(mob_event(3, "2026-06-04T12:00:10+09:00", "fox"))

        # 同じ種はクールダウン中なので黙る
        self.assertTrue(any(action.text == "LLM:ambient" for action in first.actions))
        self.assertFalse(any(action.layer == "speech" for action in same_species.actions))
        # 別の種ならすぐ反応してよい（うしさんや→にわとりさんや）
        self.assertTrue(any(action.text == "LLM:ambient" for action in other_species.actions))

    def test_neutral_mob_turning_hostile_emits_caution_call(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())

        ambient = GameEvent.model_validate(
            {
                "schema_version": "2026-05-24",
                "game": "minecraft-java",
                "adapter": "dogido-fabric-client",
                "observed_at": "2026-06-04T12:00:00+09:00",
                "sequence": 11,
                "event": {
                    "name": "status_snapshot",
                    "source_kind": "system",
                    "priority_hint": "background",
                    "certainty": "high",
                },
                "player": {"name": "main_player"},
                "world": {"time_phase": "day", "biome": "plains", "sky_visible": True},
                "passive_mobs": [
                    {
                        "type": "wolf",
                        "distance": 6.0,
                        "direction": {"horizontal": "front", "vertical": "same"},
                        "certainty": "high",
                        "temperament": "neutral",
                    }
                ],
                "combat": {"combat_active_hint": False},
            }
        )

        def hostile_wolf(sequence: int, observed_at: str) -> GameEvent:
            return GameEvent.model_validate(
                {
                    "schema_version": "2026-05-24",
                    "game": "minecraft-java",
                    "adapter": "dogido-fabric-client",
                    "observed_at": observed_at,
                    "sequence": sequence,
                    "event": {
                        "name": "threat_approaching",
                        "source_kind": "visual",
                        "priority_hint": "urgent",
                        "certainty": "high",
                    },
                    "player": {"name": "main_player"},
                    "world": {"time_phase": "day", "biome": "plains", "sky_visible": True},
                    "visual_threats": [
                        {
                            "type": "wolf",
                            "entity_id": "wolf-1",
                            "distance": 6.0,
                            "direction": {"horizontal": "front", "vertical": "same"},
                            "approaching": True,
                            "certainty": "high",
                        }
                    ],
                    "combat": {
                        "recent_hostile_visual_ms": 100,
                        "hostiles_within_7": 1,
                        "hostiles_within_10": 1,
                        "combat_active_hint": True,
                    },
                }
            )

        expected_variants = {
            "プレイヤー！オオカミの動きが怪しいで！",
            "プレイヤー、オオカミが睨んどる！なんか怒らせたんちゃう！？",
            "あかん、オオカミがこっち来る気や！気ぃつけて！",
        }

        machine.process(ambient)
        turned = machine.process(hostile_wolf(12, "2026-06-04T12:00:10+09:00"))
        repeat = machine.process(hostile_wolf(13, "2026-06-04T12:00:15+09:00"))

        # さっきまで平和だった中立モブの敵対化には警告のキュー音声を出す
        self.assertTrue(any((action.text or "") in expected_variants for action in turned.actions))
        # 同じ種への警告はクールダウン中は繰り返さない
        self.assertFalse(any((action.text or "") in expected_variants for action in repeat.actions))

    def test_player_input_blocks_ambient_until_two_minutes_pass(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T12:00:00+09:00",
              "sequence": 10,
              "event": {
                "name": "ambient_mob_detected",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "meta": {
                "user_text": "こんにちは"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true
              },
              "passive_mobs": [
                {
                  "type": "fox",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "certainty": "high",
                  "temperament": "friendly"
                }
              ],
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T12:01:59+09:00",
              "sequence": 11,
              "event": {
                "name": "ambient_mob_detected",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true
              },
              "passive_mobs": [
                {
                  "type": "fox",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "certainty": "high",
                  "temperament": "friendly"
                }
              ],
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T12:02:01+09:00",
              "sequence": 12,
              "event": {
                "name": "ambient_mob_detected",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true
              },
              "passive_mobs": [
                {
                  "type": "fox",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "certainty": "high",
                  "temperament": "friendly"
                }
              ],
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )

        first_result = machine.process(first)
        second_result = machine.process(second)
        third_result = machine.process(third)

        self.assertFalse(any(action.text == "LLM:ambient" for action in first_result.actions))
        self.assertFalse(any(action.text == "LLM:ambient" for action in second_result.actions))
        self.assertTrue(any(action.text == "LLM:ambient" for action in third_result.actions))

    def test_damaging_light_source_emits_hot_warning(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T14:00:00+09:00",
              "sequence": 20,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true,
                "local_light": 10,
                "danger_darkness_score": 0.1,
                "nearby_damaging_light_source_count": 1,
                "nearest_damaging_light_source_distance": 1.0,
                "standing_on_magma_block": false
              },
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.text == "触るとあちちやで！" for action in result.actions))

    def test_standing_on_magma_block_prefers_specific_comment(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T14:05:00+09:00",
              "sequence": 21,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true,
                "local_light": 10,
                "danger_darkness_score": 0.1,
                "nearby_damaging_light_source_count": 1,
                "nearest_damaging_light_source_distance": 0.0,
                "standing_on_magma_block": true
              },
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.text == "………しゃがめば大丈夫なんが不思議やな……" for action in result.actions))
        self.assertFalse(any(action.text == "触るとあちちやで！" for action in result.actions))

    def test_ambient_mob_detected_during_dark_push_stop_emits_ambient_line_without_waiting_for_normal(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        machine.state.mode = "alert"
        machine.state.dark_push_active = True
        machine.state.dark_push_reference_light = 1
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T12:00:01+09:00",
              "sequence": 3,
              "event": {
                "name": "ambient_mob_detected",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "biome": "soul_sand_valley",
                "sky_visible": false,
                "local_light": 3,
                "danger_darkness_score": 1.0,
                "enclosure_score": 0.58,
                "ceiling_height": 24.0,
                "overhead_cover_type": "none",
                "connected_dark_volume": 559
              },
              "passive_mobs": [
                {
                  "type": "zombified_piglin",
                  "distance": 7.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "certainty": "high",
                  "temperament": "neutral",
                  "caution_reason": "provoked_only"
                }
              ],
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))
        self.assertTrue(any(action.text == "LLM:ambient" for action in result.actions))

    def test_low_health_warning_fires_once_until_player_recovers_above_five(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T22:00:00+09:00",
              "sequence": 1,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "health": 5.0
              },
              "world": {
                "time_phase": "night",
                "biome": "plains",
                "danger_darkness_score": 0.5,
                "sky_visible": true
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z1",
                  "distance": 8.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T22:00:01+09:00",
              "sequence": 2,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "health": 4.0
              },
              "world": {
                "time_phase": "night",
                "biome": "plains",
                "danger_darkness_score": 0.5,
                "sky_visible": true
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z1",
                  "distance": 7.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        recovered = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T22:00:03+09:00",
              "sequence": 3,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "health": 8.0
              },
              "world": {
                "time_phase": "night",
                "biome": "plains",
                "danger_darkness_score": 0.5,
                "sky_visible": true
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z1",
                  "distance": 7.5,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T22:00:05+09:00",
              "sequence": 4,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "health": 5.0
              },
              "world": {
                "time_phase": "night",
                "biome": "plains",
                "danger_darkness_score": 0.5,
                "sky_visible": true
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z2",
                  "distance": 7.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)
        self.machine.process(recovered)
        third_result = self.machine.process(third)

        self.assertTrue(any(action.text == "main_player！体力やばいで！" for action in first_result.actions))
        self.assertFalse(any(action.text == "main_player！体力やばいで！" for action in second_result.actions))
        self.assertTrue(any(action.text == "main_player！体力やばいで！" for action in third_result.actions))

    def test_dimension_change_emits_flush_interrupt_only(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:00:00+09:00",
              "sequence": 1,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "day",
                "biome": "plains",
                "sky_visible": true
              },
              "combat": {
                "combat_active_hint": false
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:00:02+09:00",
              "sequence": 2,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:the_nether"
              },
              "world": {
                "biome": "nether_wastes",
                "sky_visible": false,
                "danger_darkness_score": 1.0
              },
              "visual_threats": [
                {
                  "type": "zombified_piglin",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertEqual(len(result.actions), 1)
        self.assertEqual(result.actions[0].layer, "flush")
        self.assertTrue(result.actions[0].interrupt)

    def test_already_visible_enemy_moving_close_does_not_trigger_new_ambush_scream(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 2,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.8,
                "sky_visible": true,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-visible-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:03+09:00",
              "sequence": 3,
              "event": {
                "name": "status_snapshot",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.8,
                "sky_visible": true,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-visible-1",
                  "distance": 2.5,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertFalse(any(action.cue_id == "panic_scream_start" for action in result.actions))

    def test_ushiro_call_triggers_for_melee_enemy_directly_behind_within_three_blocks(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 1,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.8,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z-ushiro-1",
                  "distance": 2.4,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "meta": {"call_name": "メルちゃん"}
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.cue_id == "ushiro_scream" for action in result.actions))
        self.assertTrue(any(action.text == "メルちゃんうしろ！うしろ〜！" for action in result.actions))

    def test_ushiro_call_rarely_uses_classic_shimura_variant(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:04+09:00",
              "sequence": 4,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.8,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z-4",
                  "distance": 2.4,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "meta": {"call_name": "メルちゃん"}
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.text == USHIRO_CALL for action in result.actions))

    def test_ushiro_call_has_global_one_minute_cooldown(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 1,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z-ushiro-1",
                  "distance": 2.4,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "meta": {"call_name": "メルちゃん"}
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:11+09:00",
              "sequence": 2,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "spider",
                  "entity_id": "s-ushiro-2",
                  "distance": 2.8,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "meta": {"call_name": "メルちゃん"}
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:12+09:00",
              "sequence": 3,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "spider",
                  "entity_id": "s-ushiro-3",
                  "distance": 2.2,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "meta": {"call_name": "メルちゃん"}
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)
        third_result = self.machine.process(third)

        self.assertTrue(any(action.text == "メルちゃんうしろ！うしろ〜！" for action in first_result.actions))
        self.assertFalse(any((action.text or "").endswith("うしろ！うしろ〜！") for action in second_result.actions))
        self.assertTrue(any((action.text or "").endswith("うしろ！うしろ〜！") for action in third_result.actions))

    def test_ushiro_sequence_suppresses_followup_visual_callouts(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 1,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z-ushiro-1",
                  "distance": 2.4,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:02+09:00",
              "sequence": 2,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "s-follow-1",
                  "distance": 7.5,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertFalse(any("スケルトン" in (action.text or "") for action in result.actions))

    def test_shut_up_three_times_enters_suppressed_panic(self) -> None:
        base_json = """
        {
          "schema_version": "2026-05-24",
          "game": "minecraft-java",
          "adapter": "dogido-fabric-client",
          "observed_at": "%s",
          "sequence": %d,
          "event": {
            "name": "threat_approaching",
            "source_kind": "visual",
            "priority_hint": "urgent",
            "certainty": "high"
          },
          "player": {"name": "main_player"},
          "world": {"time_phase": "night", "danger_darkness_score": 0.8},
          "visual_threats": [
            {
              "type": "zombie",
              "distance": 4.0,
              "direction": {"horizontal": "front", "vertical": "same"},
              "approaching": true,
              "certainty": "high"
            }
          ],
          "combat": {
            "recent_hostile_visual_ms": 100,
            "hostiles_within_7": 1,
            "hostiles_within_10": 1,
            "combat_active_hint": true
          },
          "meta": {"user_text": "うるさい"}
        }
        """

        for offset in range(3):
            event = GameEvent.model_validate_json(
                base_json % (f"2026-05-25T21:10:0{offset + 1}+09:00", offset + 1)
            )
            result = self.machine.process(event)

        self.assertEqual(result.state.mode, "suppressed_panic")
        self.assertTrue(any(action.layer == "panic_cue" for action in result.actions))

    def test_darkness_with_torch_materials_gives_advice(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:05+09:00",
              "sequence": 3,
              "event": {
                "name": "danger_darkness_changed",
                "source_kind": "inferred",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.88
              },
              "inventory": {
                "torch": 0,
                "coal": 2,
                "stick": 4
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertTrue(any("松明" in (action.text or "") for action in result.actions))

    def test_local_light_four_does_not_trigger_darkness_alert(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T20:10:05+09:00",
              "sequence": 3,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "night",
                "time_of_day": 14000,
                "weather": "clear",
                "danger_darkness_score": 0.81,
                "local_light": 4,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )

        result = self.machine.process(event)

        self.assertEqual(result.state.mode, "normal")
        self.assertFalse(any(action.layer == "speech" for action in result.actions))

    def test_darkness_advice_uses_hiragana_ie_and_respects_cooldown(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T20:10:05+09:00",
              "sequence": 30,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld",
                "held_item": "minecraft:iron_sword"
              },
              "world": {
                "time_phase": "night",
                "time_of_day": 14000,
                "weather": "clear",
                "danger_darkness_score": 0.81,
                "local_light": 3,
                "sky_visible": true,
                "respawn_point_set": true,
                "respawn_distance": 10.0,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T20:10:10+09:00",
              "sequence": 31,
              "event": {
                "name": "danger_darkness_changed",
                "source_kind": "inferred",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld",
                "held_item": "minecraft:iron_sword"
              },
              "world": {
                "time_phase": "night",
                "time_of_day": 14000,
                "weather": "clear",
                "danger_darkness_score": 0.83,
                "local_light": 3,
                "sky_visible": true,
                "respawn_point_set": true,
                "respawn_distance": 10.0,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )

        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T20:10:15+09:00",
              "sequence": 32,
              "event": {
                "name": "danger_darkness_changed",
                "source_kind": "inferred",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld",
                "held_item": "minecraft:iron_sword"
              },
              "world": {
                "time_phase": "night",
                "time_of_day": 14000,
                "weather": "clear",
                "danger_darkness_score": 0.84,
                "local_light": 3,
                "sky_visible": true,
                "respawn_point_set": true,
                "respawn_distance": 10.0,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)
        third_result = self.machine.process(third)

        # TTSの読み間違い対策で「家」ではなくひらがなの「いえ」を使う
        self.assertTrue(
            any(action.text == "これはもうあかん、こんなんいえに帰ったほうがええって。" for action in first_result.actions)
        )
        # 以降はクールダウン中なので繰り返さない
        self.assertFalse(any(action.layer == "speech" for action in second_result.actions))
        self.assertFalse(any(action.layer == "speech" for action in third_result.actions))

    def test_combat_ended_waits_for_safe_zone_with_door_before_aftermath(self) -> None:
        combat_ended = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:12+09:00",
              "sequence": 4,
              "event": {
                "name": "combat_ended",
                "source_kind": "system",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.2
              },
              "combat": {
                "recent_damage_ms": 6200,
                "recent_hostile_visual_ms": 6100,
                "recent_hostile_audio_ms": 6300,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              }
            }
            """
        )
        safe_zone = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:18+09:00",
              "sequence": 5,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.12,
                "local_light": 11,
                "sky_visible": false,
                "ceiling_height": 3,
                "nearby_door_count": 2,
                "safe_zone_with_door": true,
                "enclosure_score": 0.26,
                "biome": "plains"
              },
              "combat": {
                "recent_damage_ms": 7000,
                "recent_hostile_visual_ms": 7000,
                "recent_hostile_audio_ms": 7000,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              }
            }
            """
        )

        first_result = self.machine.process(combat_ended)
        first_mode = first_result.state.mode
        first_actions = list(first_result.actions)
        second_result = self.machine.process(safe_zone)

        self.assertEqual(first_mode, "normal")
        self.assertFalse(first_actions)
        self.assertEqual(second_result.state.mode, "aftermath")
        self.assertTrue(any(action.layer == "speech" for action in second_result.actions))
        self.assertTrue(any(action.cue_id == "aftermath_relief" for action in second_result.actions))

    def test_special_biome_line_has_ten_minute_cooldown_on_reentry(self) -> None:
        plains_first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:00:00+09:00",
              "sequence": 1,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {
                "time_phase": "day",
                "time_of_day": 6000,
                "weather": "clear",
                "danger_darkness_score": 0.05,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        forest_first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:00:05+09:00",
              "sequence": 2,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {
                "time_phase": "day",
                "time_of_day": 6000,
                "weather": "clear",
                "danger_darkness_score": 0.05,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "forest"
              },
              "inventory": {}
            }
            """
        )
        plains_second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:02:00+09:00",
              "sequence": 3,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {
                "time_phase": "day",
                "time_of_day": 6000,
                "weather": "clear",
                "danger_darkness_score": 0.05,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        forest_second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:02:05+09:00",
              "sequence": 4,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {
                "time_phase": "day",
                "time_of_day": 6000,
                "weather": "clear",
                "danger_darkness_score": 0.05,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "forest"
              },
              "inventory": {}
            }
            """
        )
        plains_third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:10:10+09:00",
              "sequence": 5,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {
                "time_phase": "day",
                "time_of_day": 6000,
                "weather": "clear",
                "danger_darkness_score": 0.05,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        forest_third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:10:15+09:00",
              "sequence": 6,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {
                "time_phase": "day",
                "time_of_day": 6000,
                "weather": "clear",
                "danger_darkness_score": 0.05,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "forest"
              },
              "inventory": {}
            }
            """
        )

        self.machine.process(plains_first)
        first_forest = self.machine.process(forest_first)
        self.machine.process(plains_second)
        second_forest = self.machine.process(forest_second)
        self.machine.process(plains_third)
        third_forest = self.machine.process(forest_third)

        self.assertTrue(any(action.layer == "speech" for action in first_forest.actions))
        self.assertFalse(any(action.layer == "speech" for action in second_forest.actions))
        self.assertTrue(any(action.layer == "speech" for action in third_forest.actions))

    def test_tree_canopy_does_not_emit_emergency_shelter_advice(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T20:20:05+09:00",
              "sequence": 40,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "night",
                "time_of_day": 14000,
                "weather": "clear",
                "danger_darkness_score": 1.0,
                "local_light": 3,
                "sky_visible": false,
                "overhead_cover_type": "foliage",
                "ceiling_height": 4.0,
                "enclosure_score": 0.69,
                "drafty_opening_count": 33,
                "connected_dark_volume": 581,
                "biome": "meadow",
                "respawn_point_set": true,
                "respawn_distance": 80.0
              },
              "inventory": {}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(
            any(
                action.text is not None and "緊急シェルターつくるで" in action.text
                for action in result.actions
            )
        )

    def test_stale_combat_ended_does_not_trigger_aftermath_on_late_safe_zone_entry(self) -> None:
        combat_ended = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:12+09:00",
              "sequence": 4,
              "event": {
                "name": "combat_ended",
                "source_kind": "system",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.2
              },
              "combat": {
                "recent_damage_ms": 6200,
                "recent_hostile_visual_ms": 6100,
                "recent_hostile_audio_ms": 6300,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              }
            }
            """
        )
        late_safe_zone = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:45+09:00",
              "sequence": 5,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.12,
                "local_light": 11,
                "sky_visible": false,
                "ceiling_height": 3,
                "nearby_door_count": 2,
                "safe_zone_with_door": true,
                "enclosure_score": 0.26,
                "biome": "plains"
              },
              "combat": {
                "recent_damage_ms": 40000,
                "recent_hostile_visual_ms": 40000,
                "recent_hostile_audio_ms": 40000,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              }
            }
            """
        )

        self.machine.process(combat_ended)
        result = self.machine.process(late_safe_zone)

        self.assertEqual(result.state.mode, "normal")
        self.assertFalse(any(action.cue_id == "aftermath_relief" for action in result.actions))

    def test_alert_visual_threat_does_not_play_spot_cue_outside_melee_range(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 5,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 8.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertFalse(any(action.cue_id == "spot_hostile_gasp" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" and not action.interrupt for action in result.actions))

    def test_close_melee_visual_is_prioritized_over_far_skeleton(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 5,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-priority-1",
                  "distance": 11.0,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                },
                {
                  "type": "spider",
                  "entity_id": "spider-priority-1",
                  "distance": 3.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        target = self.machine._highest_priority_visual(event.visual_threats)

        self.assertIsNotNone(target)
        self.assertEqual(target.type, "spider")

    def test_close_audio_ambush_in_occluded_area_uses_llm_callout_without_cue(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:15+09:00",
              "sequence": 6,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.7,
                "sky_visible": false,
                "enclosure_score": 0.42,
                "biome": "dripstone_caves"
              },
              "auditory_threats": [
                {
                  "label": "hostile_presence",
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "distance_band": "very_close",
                  "certainty": "medium",
                  "spoken_name_allowed": false
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": true
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertFalse(any(action.layer == "panic_cue" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" for action in result.actions))
        self.assertTrue(any(action.text == "LLM:occluded_hostile_presence" for action in result.actions))

    def test_occluded_hostile_presence_comment_has_five_minute_cooldown(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:15+09:00",
              "sequence": 6,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.7,
                "sky_visible": false,
                "enclosure_score": 0.42,
                "biome": "dripstone_caves"
              },
              "auditory_threats": [
                {
                  "label": "hostile_presence",
                  "source_id": "audio-zombie-1",
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "distance_band": "very_close",
                  "certainty": "medium",
                  "spoken_name_allowed": false
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:15+09:00",
              "sequence": 7,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.7,
                "sky_visible": false,
                "enclosure_score": 0.42,
                "biome": "dripstone_caves"
              },
              "auditory_threats": [
                {
                  "label": "hostile_presence",
                  "source_id": "audio-zombie-1",
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "distance_band": "very_close",
                  "certainty": "medium",
                  "spoken_name_allowed": false
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": true
              }
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:15:16+09:00",
              "sequence": 8,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.7,
                "sky_visible": false,
                "enclosure_score": 0.42,
                "biome": "dripstone_caves"
              },
              "auditory_threats": [
                {
                  "label": "hostile_presence",
                  "source_id": "audio-zombie-1",
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "distance_band": "very_close",
                  "certainty": "medium",
                  "spoken_name_allowed": false
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = machine.process(first)
        second_result = machine.process(second)
        third_result = machine.process(third)

        self.assertTrue(any(action.text == "LLM:occluded_hostile_presence" for action in first_result.actions))
        self.assertFalse(any(action.text == "LLM:occluded_hostile_presence" for action in second_result.actions))
        self.assertTrue(any(action.text == "LLM:occluded_hostile_presence" for action in third_result.actions))

    def test_other_realm_single_occluded_audio_keeps_specific_hostile_label_for_llm(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T00:10:15+09:00",
              "sequence": 12,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:the_nether"
              },
              "world": {
                "biome": "soul_sand_valley",
                "sky_visible": false,
                "enclosure_score": 0.42,
                "time_phase": "night"
              },
              "auditory_threats": [
                {
                  "label": "zombie",
                  "source_id": "nether-audio-1",
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "distance_band": "mid",
                  "certainty": "medium",
                  "spoken_name_allowed": true
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": true
              }
            }
            """
        )

        result = machine.process(event)

        self.assertTrue(any(action.text == "LLM:occluded_hostile_presence" for action in result.actions))
        self.assertEqual(llm.requests[-1].kind, "occluded_hostile_presence")
        self.assertEqual(llm.requests[-1].details.get("hostile"), "ゾンビ")

    def test_skeleton_damage_ambush_uses_scream_only(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:20+09:00",
              "sequence": 7,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.6,
                "sky_visible": false,
                "enclosure_score": 0.35,
                "biome": "forest"
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "distance": 6.0,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_damage_ms": 200,
                "recent_hostile_audio_ms": 40000,
                "recent_hostile_visual_ms": 0,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertEqual(result.state.mode, "panic")
        self.assertTrue(any(action.cue_id == "panic_scream_start" for action in result.actions))
        self.assertFalse(any(action.layer == "callout" for action in result.actions))

    def test_same_hostile_comment_is_suppressed_for_one_minute(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 30,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.3},
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 8.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:10+09:00",
              "sequence": 31,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.3},
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 7.8,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)

        self.assertTrue(any(action.layer == "callout" for action in first_result.actions))
        self.assertFalse(any(action.layer == "callout" for action in second_result.actions))

    def test_new_entity_same_hostile_type_still_gets_comment_within_cooldown(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 34,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z1",
                  "distance": 8.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:10+09:00",
              "sequence": 35,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z2",
                  "distance": 9.0,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertFalse(any(action.layer == "callout" for action in result.actions))

    def test_darkness_escape_llm_is_rate_limited(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        base_event = """
        {
          "schema_version": "2026-05-24",
          "game": "minecraft-java",
          "adapter": "dogido-fabric-client",
          "observed_at": "%s",
          "sequence": %d,
          "event": {
            "name": "danger_darkness_changed",
            "source_kind": "inferred",
            "priority_hint": "normal",
            "certainty": "high"
          },
          "player": {
            "name": "main_player",
            "held_item": "minecraft:air"
          },
          "world": {
            "time_phase": "night",
            "danger_darkness_score": 0.88,
            "biome": "dark_forest",
            "sky_visible": false,
            "enclosure_score": 0.4
          },
          "inventory": {
            "torch": 0,
            "stick": 0,
            "coal": 0
          }
        }
        """

        first = GameEvent.model_validate_json(base_event % ("2026-05-25T21:10:05+09:00", 36))
        second = GameEvent.model_validate_json(base_event % ("2026-05-25T21:10:15+09:00", 37))

        first_result = machine.process(first)
        second_result = machine.process(second)

        self.assertTrue(any(action.text == "LLM:darkness_escape" for action in first_result.actions))
        self.assertFalse(second_result.actions)

    def test_occluded_entry_with_light_uses_llm_leaf(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:00+09:00",
              "sequence": 70,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.42,
                "biome": "dripstone_caves"
              },
              "inventory": {
                "torch": 3
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertTrue(any(action.text == "LLM:occluded_entry_with_light" for action in result.actions))

    def test_occluded_entry_without_light_uses_llm_leaf(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:01+09:00",
              "sequence": 71,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.4,
                "biome": "lush_caves"
              },
              "inventory": {
                "stick": 2
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertTrue(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))

    def test_immediately_severe_occluded_entry_skips_stage_one_and_uses_dark_push_line(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:01+09:00",
              "sequence": 711,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.4,
                "ceiling_height": 5.0,
                "overhead_cover_type": "solid",
                "biome": "lush_caves"
              },
              "inventory": {
                "stick": 2
              }
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "alert")
        self.assertTrue(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertTrue(result.state.dark_push_active)
        self.assertEqual(result.state.dark_push_stage, 2)

    def test_cramped_dark_burrow_does_not_trigger_dark_push_or_occluded_entry(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T00:11:01+09:00",
              "sequence": 712,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 53, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "ceiling_height": 2.0,
                "overhead_cover_type": "solid",
                "cardinal_wall_count": 4,
                "connected_dark_volume": 7,
                "nearest_dark_spawn_distance": 0.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_side_opening_two_blocks_tall_is_not_treated_as_shelter(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T00:12:01+09:00",
              "sequence": 7121,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "ceiling_height": 2.0,
                "overhead_cover_type": "wood",
                "cardinal_wall_count": 2,
                "double_height_open_side_count": 1,
                "connected_dark_volume": 60,
                "drafty_opening_count": 2,
                "nearest_dark_spawn_distance": 0.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )

        self.assertFalse(machine._is_emergency_shelter_event(event))

    def test_tree_canopy_with_hidden_sky_does_not_trigger_occluded_entry(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:11:01+09:00",
              "sequence": 713,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.53,
                "local_light": 11,
                "sky_visible": false,
                "enclosure_score": 0.83,
                "ceiling_height": 4.0,
                "overhead_cover_type": "foliage",
                "biome": "jungle"
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))

    def test_occluded_entry_prompt_receives_call_name(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:01+09:00",
              "sequence": 701,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.4,
                "biome": "lush_caves"
              },
              "inventory": {
                "stick": 2
              },
              "meta": {
                "call_name": "プレイヤーちゃん"
              }
            }
            """
        )

        machine.process(event)

        self.assertTrue(llm.requests)
        self.assertEqual(llm.requests[-1].details.get("player_name"), "プレイヤーちゃん")
        self.assertEqual(llm.requests[-1].details.get("biome"), "繁茂した洞窟")

    def test_occluded_entry_fallback_mentions_call_name(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, llm_enabled=False))
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:54+09:00",
              "sequence": 71,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.4,
                "biome": "lush_caves"
              },
              "inventory": {
                "stick": 2
              },
              "meta": {
                "call_name": "プレイヤーちゃん"
              }
            }
            """
        )

        result = machine.process(event)

        self.assertTrue(any("プレイヤーちゃん" in (action.text or "") for action in result.actions))

    def test_occluded_entry_fallback_uses_default_call_name(self) -> None:
        machine = DogidoStateMachine(
            Settings(audio_enabled=False, llm_enabled=False, default_call_name="メルちゃん")
        )
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:54+09:00",
              "sequence": 71,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.4,
                "biome": "lush_caves"
              },
              "inventory": {
                "stick": 2
              }
            }
            """
        )

        result = machine.process(event)

        self.assertTrue(any("メルちゃん" in (action.text or "") for action in result.actions))

    def test_dark_push_without_light_starts_with_warning_before_breath(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 72,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {"torch": 1}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 73,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {"torch": 1}
            }
            """
        )

        machine.process(entry)
        result = machine.process(deeper)

        self.assertEqual(result.state.mode, "alert")
        self.assertTrue(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))
        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))

    def test_dark_push_starts_breathing_after_delay_and_continues_while_darkness_persists(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 721,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {"torch": 1}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 722,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {"torch": 1}
            }
            """
        )
        still_dark = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:27+09:00",
              "sequence": 723,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        later_still_dark = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:32+09:00",
              "sequence": 724,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        warning_result = machine.process(deeper)
        result = machine.process(still_dark)
        continued = machine.process(later_still_dark)

        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in warning_result.actions))
        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))
        self.assertTrue(any(action.cue_id == "suppressed_breath" for action in continued.actions))
        self.assertFalse(any(action.layer == "speech" for action in continued.actions))

    def test_dark_push_stops_when_light_recovers_to_entry_level(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 731,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 732,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.50,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        recovered = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:31+09:00",
              "sequence": 733,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.92,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.46,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        machine.process(deeper)
        result = machine.process(recovered)

        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))
        self.assertTrue(any(action.interrupt and action.text is None and action.cue_id is None for action in result.actions))
        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))

    def test_open_night_outdoors_does_not_trigger_dark_push_even_in_forest_biome(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 73,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "minecraft:air"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 4,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24,
                "biome": "forest"
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        # 夜の屋外（中程度の暗さ 0.72）は閉所扱いせず、騒がない
        self.assertEqual(result.state.mode, "normal")
        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_submerged_dark_area_does_not_trigger_occluded_dark_logic(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 731,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "minecraft:air"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.70,
                "local_light": 0,
                "sky_visible": false,
                "ceiling_height": 1,
                "overhead_cover_type": "solid",
                "is_submerged": true,
                "submerged_depth_blocks": 6,
                "air_supply": 300,
                "enclosure_score": 1.0,
                "biome": "river"
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        self.assertEqual(result.state.mode, "normal")
        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))
        self.assertTrue(any(action.text == "……暗いのは、にがてなんやけど……。" for action in result.actions))

    def test_submerged_dark_comment_only_once_while_staying_underwater(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 732,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.70,
                "local_light": 0,
                "is_submerged": true,
                "submerged_depth_blocks": 6,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "biome": "river"
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:26+09:00",
              "sequence": 733,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 0,
                "is_submerged": true,
                "submerged_depth_blocks": 6,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "biome": "river"
              }
            }
            """
        )

        first_result = machine.process(first)
        second_result = machine.process(second)

        self.assertTrue(any(action.text == "……暗いのは、にがてなんやけど……。" for action in first_result.actions))
        self.assertEqual(
            [action.text for action in second_result.actions if action.layer == "speech"],
            ["そろそろ夜やなー。今から地上に出ると敵とかち合うなー"],
        )

    def test_high_ceiling_low_enclosure_falls_back_to_outdoor_even_if_sky_visible_is_false(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:25+09:00",
              "sequence": 74,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "minecraft:air"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 4,
                "sky_visible": false,
                "ceiling_height": 24,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        # 屋外扱いになるので閉所系の反応（息・暗所プッシュ）は出さない
        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_sky_visible_high_ceiling_overrides_noisy_enclosure_score(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:26+09:00",
              "sequence": 75,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "minecraft:air"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.79,
                "local_light": 4,
                "sky_visible": true,
                "ceiling_height": 24,
                "enclosure_score": 0.39,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        # 空が見える高天井は屋外扱い。閉所系の反応は出さない
        self.assertFalse(any(action.cue_id == "suppressed_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_dark_push_stops_immediately_after_leaving_occluded_zone(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 76,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 77,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        escaped = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:26+09:00",
              "sequence": 78,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.12,
                "local_light": 13,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        machine.process(deeper)
        result = machine.process(escaped)

        self.assertTrue(any(action.interrupt and action.text is None and action.cue_id is None for action in result.actions))
        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))

    def test_dark_push_stop_on_bright_day_surface_suppresses_after_breath(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T05:11:02+09:00",
              "sequence": 760,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.40,
                "ceiling_height": 5.0,
                "overhead_cover_type": "solid",
                "biome": "lush_caves"
              },
              "inventory": {}
            }
            """
        )
        escaped = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T06:11:05+09:00",
              "sequence": 761,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.10,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "meadow"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        result = machine.process(escaped)

        self.assertTrue(any(action.interrupt and action.text is None and action.cue_id is None for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))

    def test_stage_one_dark_entry_still_emits_after_breath_when_exiting(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 79,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        escaped = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:05+09:00",
              "sequence": 80,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        result = machine.process(escaped)

        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))

    def test_stage_one_same_spot_does_not_immediately_escalate_to_dark_push(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 79,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.51,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        same_spot_darker = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:04+09:00",
              "sequence": 80,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 0.51,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        result = machine.process(same_spot_darker)

        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))

    def test_stage_one_progressing_forward_can_escalate_to_dark_push(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 81,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.51,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:04+09:00",
              "sequence": 82,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 12, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 0.51,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        result = machine.process(deeper)

        self.assertTrue(any(action.text == "LLM:dark_push_no_light" for action in result.actions))

    def test_dark_push_forward_spawn_interrupts_with_scream_and_monster_name(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 181,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 2,
                "sky_visible": false,
                "enclosure_score": 0.51,
                "ceiling_height": 5.0,
                "overhead_cover_type": "solid",
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:04+09:00",
              "sequence": 182,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 12, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 0.51,
                "ceiling_height": 5.0,
                "overhead_cover_type": "solid",
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        ambush = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:05+09:00",
              "sequence": 183,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 12, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 1.0,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 0.53,
                "ceiling_height": 5.0,
                "overhead_cover_type": "solid",
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "cave-zombie-front-1",
                  "distance": 2.4,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        machine.process(entry)
        machine.process(deeper)
        result = machine.process(ambush)

        self.assertTrue(any(action.layer == "control" and action.interrupt for action in result.actions))
        self.assertTrue(any(action.cue_id == "front_spawn_scream" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" and "ゾンビ" in (action.text or "") for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))

    def test_stage_one_progress_without_deep_darkness_does_not_escalate(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 83,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.50,
                "local_light": 14,
                "sky_visible": false,
                "enclosure_score": 0.64,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )
        slightly_darker_forward = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:04+09:00",
              "sequence": 84,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 12, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.65,
                "local_light": 12,
                "sky_visible": false,
                "enclosure_score": 0.64,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        result = machine.process(slightly_darker_forward)

        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))

    def test_daytime_after_breath_rejects_one_nann_style(self) -> None:
        class OneNannLLM:
            def generate_leaf_text(self, request):  # type: ignore[override]
                return "また一難去ってまた一難やな、心臓に悪いわ"

        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=OneNannLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:11:08+09:00",
              "sequence": 90,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.30,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              }
            }
            """
        )

        line = machine._render_dark_push_after_breath_line(event)

        self.assertEqual(line, "心臓に悪いわ、マジで心臓に悪いわ……。")

    def test_evening_after_breath_rewrites_mada_yoru_to_mou_yoru(self) -> None:
        class EveningLLM:
            def generate_leaf_text(self, request):  # type: ignore[override]
                return "メルちゃん、まだ夜やで、一難去ってまた一難や、心臓に悪いわ"

        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=EveningLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T18:11:08+09:00",
              "sequence": 90,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "evening",
                "danger_darkness_score": 0.30,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              }
            }
            """
        )

        line = machine._render_dark_push_after_breath_line(event)

        self.assertIn("もう夜", line)
        self.assertNotIn("まだ夜", line)

    def test_non_overworld_after_breath_falls_back_without_time_text(self) -> None:
        class NetherLLM:
            def generate_leaf_text(self, request):  # type: ignore[override]
                return "もう夜やんか…一難去ってまた一難やないか、心臓に悪いわ。"

        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=NetherLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:11:08+09:00",
              "sequence": 90,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:the_nether"
              },
              "world": {
                "danger_darkness_score": 0.30,
                "local_light": 0,
                "sky_visible": false,
                "enclosure_score": 0.70,
                "biome": "nether_wastes"
              }
            }
            """
        )

        line = machine._render_dark_push_after_breath_line(event)

        self.assertEqual(line, "心臓に悪いわ、マジで心臓に悪いわ……。")

    def test_dimension_change_does_not_trigger_occluded_entry_on_first_snapshot(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        machine.state.current_dimension = "minecraft:the_nether"
        machine.state.last_occluded_dark_zone = False
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T12:12:08+09:00",
              "sequence": 91,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.20,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.66,
                "ceiling_height": 24.0,
                "biome": "plains"
              }
            }
            """
        )

        result = machine.process(event)

        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))

    def test_distant_visual_threat_does_not_block_cave_exit_after_breath_when_already_commented(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        far_threat = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:58+09:00",
              "sequence": 91,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 8,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "enderman",
                  "entity_id": "ender-far-1",
                  "distance": 18.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": true
              }
            }
            """
        )
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 92,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              }
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 93,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              }
            }
            """
        )
        escaped_with_same_far_threat = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:26+09:00",
              "sequence": 94,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 10, "y": 64, "z": 10}
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.74,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "enderman",
                  "entity_id": "ender-far-1",
                  "distance": 18.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": true
              }
            }
            """
        )

        machine.process(far_threat)
        machine.process(entry)
        machine.process(deeper)
        result = machine.process(escaped_with_same_far_threat)

        # 遠距離（18ブロック・非接近・コメント済み）の脅威は脱出後の一息プッシュを妨げない
        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in result.actions))

    def test_dark_push_after_breath_is_deferred_until_quiet_after_exit_with_threats(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 80,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 81,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        escaped_with_threat = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:26+09:00",
              "sequence": 82,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.74,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "exit-zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "inventory": {}
            }
            """
        )
        quiet_outside = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:29+09:00",
              "sequence": 83,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        machine.process(deeper)
        threat_result = machine.process(escaped_with_threat)
        quiet_result = machine.process(quiet_outside)

        self.assertTrue(any(action.interrupt and action.text is None and action.cue_id is None for action in threat_result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in threat_result.actions))
        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in quiet_result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in quiet_result.actions))

    def test_stage_one_dark_entry_defers_after_breath_until_quiet_if_enemy_present_on_exit(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 84,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        escaped_with_threat = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:05+09:00",
              "sequence": 85,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "exit-zombie-stage1",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              },
              "inventory": {}
            }
            """
        )
        quiet_outside = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:08+09:00",
              "sequence": 86,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.72,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        machine.process(entry)
        threat_result = machine.process(escaped_with_threat)
        quiet_result = machine.process(quiet_outside)

        self.assertFalse(any(action.text == "LLM:dark_push_after_breath" for action in threat_result.actions))
        self.assertTrue(any(action.text == "LLM:dark_push_after_breath" for action in quiet_result.actions))

    def test_light_source_crafted_stops_dark_push_then_celebrates(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        entry = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 79,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.45,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        deeper = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:24+09:00",
              "sequence": 80,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.95,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 0.47,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        crafted = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:31+09:00",
              "sequence": 81,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.55,
                "local_light": 9,
                "sky_visible": false,
                "enclosure_score": 0.35,
                "biome": "dripstone_caves"
              },
              "inventory": {
                "torch": 4
              }
            }
            """
        )

        machine.process(entry)
        machine.process(deeper)
        result = machine.process(crafted)

        self.assertTrue(any(action.interrupt and action.text is None and action.cue_id is None for action in result.actions))
        self.assertTrue(any(action.text == "LLM:light_crafted" for action in result.actions))

    def test_light_source_crafted_uses_llm_leaf(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        before = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:30+09:00",
              "sequence": 74,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.55,
                "local_light": 7,
                "sky_visible": false,
                "enclosure_score": 0.35,
                "biome": "dripstone_caves"
              },
              "inventory": {
                "coal": 1,
                "stick": 2
              }
            }
            """
        )
        after = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:31+09:00",
              "sequence": 75,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.55,
                "local_light": 7,
                "sky_visible": false,
                "enclosure_score": 0.35,
                "biome": "dripstone_caves"
              },
              "inventory": {
                "torch": 4
              }
            }
            """
        )

        machine.process(before)
        result = machine.process(after)

        self.assertTrue(any(action.text == "LLM:light_crafted" for action in result.actions))

    def test_hostile_visible_callout_uses_realtime_template(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 38,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "distance": 8.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        result = machine.process(event)

        self.assertTrue(any(action.cue_id == "spot_hostile_gasp" for action in result.actions))
        self.assertTrue(any("スケルトン" in (action.text or "") for action in result.actions))

    def test_new_hostile_type_still_gets_comment_within_cooldown(self) -> None:
        zombie = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 32,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.3},
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 8.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        skeleton = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:12+09:00",
              "sequence": 33,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.3},
              "visual_threats": [
                {
                  "type": "skeleton",
                  "distance": 8.2,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )

        self.machine.process(zombie)
        result = self.machine.process(skeleton)

        self.assertFalse(any(action.layer == "callout" for action in result.actions))

    def test_auditory_report_is_suppressed_when_visual_threat_exists(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:12+09:00",
              "sequence": 39,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "z1",
                  "distance": 8.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ],
              "auditory_threats": [
                {
                  "label": "hostile_presence",
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "distance_band": "very_close",
                  "certainty": "medium",
                  "spoken_name_allowed": false
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "recent_hostile_audio_ms": 0,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        result = machine.process(event)

        self.assertTrue(any("ゾンビ" in (action.text or "") for action in result.actions))
        self.assertFalse(any("声する" in (action.text or "") for action in result.actions))

    def test_auditory_only_report_uses_precise_direction(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:13+09:00",
              "sequence": 40,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "auditory_threats": [
                {
                  "label": "hostile_presence",
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "distance_band": "close",
                  "certainty": "medium",
                  "spoken_name_allowed": false
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": false
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any("左" in (action.text or "") for action in result.actions))

    def test_same_enemy_audio_after_visual_comment_is_suppressed(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:14+09:00",
              "sequence": 41,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "same-zombie",
                  "distance": 8.0,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:15+09:00",
              "sequence": 42,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.3,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "auditory_threats": [
                {
                  "label": "zombie",
                  "source_id": "same-zombie",
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "distance_band": "close",
                  "certainty": "medium",
                  "spoken_name_allowed": true
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 0,
                "combat_active_hint": false
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertFalse(any(action.layer == "callout" for action in result.actions))

    def test_auditory_repeats_only_speak_on_first_fourth_and_tenth(self) -> None:
        base_event = """
        {
          "schema_version": "2026-05-24",
          "game": "minecraft-java",
          "adapter": "dogido-fabric-client",
          "observed_at": "%s",
          "sequence": %d,
          "event": {
            "name": "hostile_audio_detected",
            "source_kind": "auditory",
            "priority_hint": "normal",
            "certainty": "medium"
          },
          "player": {
            "name": "main_player",
            "position": {"x": 0, "y": 64, "z": 0}
          },
          "world": {
            "time_phase": "night",
            "danger_darkness_score": 0.3,
            "sky_visible": true,
            "enclosure_score": 0.05,
            "biome": "plains"
          },
          "auditory_threats": [
            {
              "label": "skeleton",
              "source_id": "same-skeleton",
              "direction": {"horizontal": "left", "vertical": "same"},
              "distance_band": "close",
              "certainty": "medium",
              "spoken_name_allowed": true
            }
          ],
          "combat": {
            "recent_hostile_audio_ms": 0,
            "combat_active_hint": false
          }
        }
        """

        results = []
        for index in range(1, 11):
            event = GameEvent.model_validate_json(
                base_event % (f"2026-05-25T21:10:{index:02d}+09:00", 100 + index)
            )
            results.append(self.machine.process(event))

        self.assertTrue(any("声する" in (action.text or "") for action in results[0].actions))
        self.assertFalse(any(action.layer == "callout" for action in results[1].actions))
        self.assertFalse(any(action.layer == "callout" for action in results[2].actions))
        self.assertTrue(any("まだスケルトンおるで" in (action.text or "") for action in results[3].actions))
        self.assertFalse(any(action.layer == "callout" for action in results[4].actions))
        self.assertFalse(any(action.layer == "callout" for action in results[8].actions))
        self.assertTrue(any("こっち来んよな" in (action.text or "") for action in results[9].actions))

    def test_auditory_tenth_message_switches_to_chasing_if_player_keeps_moving(self) -> None:
        results = []
        for index in range(1, 11):
            event = GameEvent.model_validate_json(
                """
                {
                  "schema_version": "2026-05-24",
                  "game": "minecraft-java",
                  "adapter": "dogido-fabric-client",
                  "observed_at": "%s",
                  "sequence": %d,
                  "event": {
                    "name": "hostile_audio_detected",
                    "source_kind": "auditory",
                    "priority_hint": "normal",
                    "certainty": "medium"
                  },
                  "player": {
                    "name": "main_player",
                    "position": {"x": %d, "y": 64, "z": 0}
                  },
                  "world": {
                    "time_phase": "night",
                    "danger_darkness_score": 0.3,
                    "sky_visible": true,
                    "enclosure_score": 0.05,
                    "biome": "plains"
                  },
                  "auditory_threats": [
                    {
                      "label": "zombie",
                      "source_id": "same-zombie-audio",
                      "direction": {"horizontal": "back", "vertical": "same"},
                      "distance_band": "close",
                      "certainty": "medium",
                      "spoken_name_allowed": true
                    }
                  ],
                  "combat": {
                    "recent_hostile_audio_ms": 0,
                    "combat_active_hint": false
                  }
                }
                """
                % (f"2026-05-25T21:11:{index:02d}+09:00", 200 + index, index)
            )
            results.append(self.machine.process(event))

        self.assertTrue(any("追ってきよる" in (action.text or "") for action in results[9].actions))

    def test_newly_burning_visible_hostile_gets_reaction(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 40,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.2,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "on_fire": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:02+09:00",
              "sequence": 41,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.2,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                }
              ]
            }
            """
        )

        machine.process(first)
        result = machine.process(second)

        self.assertTrue(any(action.text == "LLM:newly_burning_visual" for action in result.actions))

    def test_newly_burning_visible_hostile_has_ten_second_cooldown(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 40,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.2,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "on_fire": false,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:02+09:00",
              "sequence": 41,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.2,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:07+09:00",
              "sequence": 42,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.2,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-2",
                  "distance": 6.4,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                }
              ]
            }
            """
        )
        fourth = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:13+09:00",
              "sequence": 43,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.2,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-2",
                  "distance": 6.4,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-3",
                  "distance": 6.8,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": false,
                  "on_fire": true,
                  "certainty": "high"
                }
              ]
            }
            """
        )

        machine.process(first)
        second_result = machine.process(second)
        third_result = machine.process(third)
        fourth_result = machine.process(fourth)

        self.assertTrue(any(action.text == "LLM:newly_burning_visual" for action in second_result.actions))
        self.assertFalse(any(action.text == "LLM:newly_burning_visual" for action in third_result.actions))
        self.assertTrue(any(action.text == "LLM:newly_burning_visual" for action in fourth_result.actions))

    def test_same_species_large_increase_triggers_swarm_callout(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 34,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        swarm = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 35,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "distance": 5.5,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "distance": 6.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "distance": 6.8,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 4,
                "hostiles_within_10": 4,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(swarm)

        self.assertTrue(any(action.text == "ゾンビが増えたで！" for action in result.actions))

    def test_stable_multi_hostile_count_does_not_repeat_on_small_position_changes(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 341,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-a",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-b",
                  "distance": 6.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:04+09:00",
              "sequence": 342,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-a",
                  "distance": 5.3,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-b",
                  "distance": 5.9,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in first_result.actions))
        self.assertTrue(any("ゾンビ2体おるで" in (action.text or "") for action in first_result.actions))
        self.assertFalse(any("ゾンビ2体おるで" in (action.text or "") for action in second_result.actions))

    def test_multi_species_visible_uses_summary_callout(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 343,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-1",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-2",
                  "distance": 5.5,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-3",
                  "distance": 6.2,
                  "direction": {"horizontal": "left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-4",
                  "distance": 6.8,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "spider",
                  "entity_id": "spider-1",
                  "distance": 7.0,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "creeper",
                  "entity_id": "creeper-1",
                  "distance": 7.4,
                  "direction": {"horizontal": "back_right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 5,
                "hostiles_within_10": 6,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any("あかんあかんあかん！もうあかん！四方八方敵やんけ！ 前にスケルトン、右後ろにクリーパーおる！" == (action.text or "") for action in result.actions))

    def test_nine_or_more_hostiles_uses_gyousan_summary(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 999,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {"type": "zombie", "entity_id": "z1", "distance": 6.0, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z2", "distance": 6.1, "direction": {"horizontal": "front_left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z3", "distance": 6.2, "direction": {"horizontal": "left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z4", "distance": 6.3, "direction": {"horizontal": "right", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z5", "distance": 6.4, "direction": {"horizontal": "back", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z6", "distance": 6.5, "direction": {"horizontal": "back_left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "skeleton", "entity_id": "s1", "distance": 6.6, "direction": {"horizontal": "front_right", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "skeleton", "entity_id": "s2", "distance": 6.7, "direction": {"horizontal": "right", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "spider", "entity_id": "sp1", "distance": 6.8, "direction": {"horizontal": "back_right", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 9,
                "hostiles_within_10": 9,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any((action.text or "") in HOSTILE_MASSIVE_VARIANTS for action in result.actions))

    def test_overwhelmed_callout_focuses_on_ranged_or_high_threat_targets(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 343,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 3.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-2",
                  "distance": 4.4,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-1",
                  "distance": 9.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "creeper",
                  "entity_id": "creeper-1",
                  "distance": 6.2,
                  "direction": {"horizontal": "back_right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 3,
                "hostiles_within_10": 4,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.text == "あかんあかんあかん！もうあかん！四方八方敵やんけ！ 右にスケルトン、右後ろにクリーパーおる！" for action in result.actions))

    def test_overwhelmed_callout_without_support_targets_is_generic(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 343,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-1",
                  "distance": 3.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-2",
                  "distance": 4.4,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "spider",
                  "entity_id": "spider-1",
                  "distance": 4.8,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-3",
                  "distance": 5.2,
                  "direction": {"horizontal": "back", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 4,
                "hostiles_within_10": 4,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in result.actions))
        self.assertTrue(
            any(
                action.text == "あかんあかんあかん！もうあかん！四方八方敵やんけ！俺もう終わりや〜！"
                for action in result.actions
            )
        )

    def test_nether_swarm_uses_group_callout_then_silences_named_followups(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T23:20:00+09:00",
              "sequence": 910,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:the_nether"
              },
              "world": {
                "biome": "nether_wastes",
                "sky_visible": false,
                "danger_darkness_score": 1.0
              },
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp1", "distance": 5.0, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp2", "distance": 5.2, "direction": {"horizontal": "front_left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp3", "distance": 5.4, "direction": {"horizontal": "left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp4", "distance": 5.6, "direction": {"horizontal": "right", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp5", "distance": 5.8, "direction": {"horizontal": "back_right", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 5,
                "hostiles_within_10": 5,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T23:20:02+09:00",
              "sequence": 911,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:the_nether"
              },
              "world": {
                "biome": "nether_wastes",
                "sky_visible": false,
                "danger_darkness_score": 1.0
              },
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp1", "distance": 4.9, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": false, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp2", "distance": 5.1, "direction": {"horizontal": "front_left", "vertical": "same"}, "approaching": false, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp3", "distance": 5.3, "direction": {"horizontal": "left", "vertical": "same"}, "approaching": false, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp4", "distance": 5.5, "direction": {"horizontal": "right", "vertical": "same"}, "approaching": false, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp5", "distance": 5.7, "direction": {"horizontal": "back_right", "vertical": "same"}, "approaching": false, "certainty": "high"}
              ],
              "auditory_threats": [
                {
                  "label": "zombie",
                  "source_id": "zpig-audio-legacy",
                  "sound_event": "entity.zombie_pigman.ambient",
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "distance_band": "close",
                  "certainty": "medium",
                  "spoken_name_allowed": true
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "recent_hostile_audio_ms": 100,
                "hostiles_within_7": 5,
                "hostiles_within_10": 5,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in first_result.actions))
        self.assertTrue(any(action.text in HOSTILE_MASSIVE_VARIANTS for action in first_result.actions))
        self.assertFalse(any(action.layer == "callout" for action in second_result.actions))

    def test_overwhelmed_callout_does_not_repeat_within_thirty_seconds(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 343,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {"type": "zombie", "entity_id": "z1", "distance": 3.8, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z2", "distance": 4.4, "direction": {"horizontal": "front_left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "skeleton", "entity_id": "s1", "distance": 9.5, "direction": {"horizontal": "right", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "creeper", "entity_id": "c1", "distance": 6.2, "direction": {"horizontal": "back_right", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {"recent_hostile_visual_ms": 100, "hostiles_within_7": 3, "hostiles_within_10": 4, "combat_active_hint": true}
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:20+09:00",
              "sequence": 344,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {"type": "zombie", "entity_id": "z1", "distance": 4.0, "direction": {"horizontal": "front_left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombie", "entity_id": "z2", "distance": 4.6, "direction": {"horizontal": "left", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "skeleton", "entity_id": "s1", "distance": 9.2, "direction": {"horizontal": "front_right", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "creeper", "entity_id": "c1", "distance": 5.8, "direction": {"horizontal": "right", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {"recent_hostile_visual_ms": 100, "hostiles_within_7": 3, "hostiles_within_10": 4, "combat_active_hint": true}
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)

        self.assertTrue(any("あかんあかんあかん！もうあかん！四方八方敵やんけ！" in (action.text or "") for action in first_result.actions))
        self.assertFalse(any("あかんあかんあかん！もうあかん！四方八方敵やんけ！" in (action.text or "") for action in second_result.actions))

    def test_unseen_spider_audio_can_comment_while_other_visible_enemy_exists(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 345,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-visible-1",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:10+09:00",
              "sequence": 346,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-visible-1",
                  "distance": 5.1,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "auditory_threats": [
                {
                  "label": "spider",
                  "source_id": "spider-audio-1",
                  "sound_event": "entity.spider.ambient",
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "distance_band": "close",
                  "certainty": "medium",
                  "spoken_name_allowed": true
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "recent_hostile_audio_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertTrue(any(action.text == "右でスパイダーの声する！" for action in result.actions))

    def test_auditory_comment_keeps_zombified_piglin_name(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T23:10:06+09:00",
              "sequence": 347,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombified_piglin",
                  "entity_id": "zpig-visible-1",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T23:10:08+09:00",
              "sequence": 348,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombified_piglin",
                  "entity_id": "zpig-visible-1",
                  "distance": 5.1,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "auditory_threats": [
                {
                  "label": "zombified_piglin",
                  "source_id": "zpig-audio-1",
                  "sound_event": "entity.zombified_piglin.ambient",
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "distance_band": "close",
                  "certainty": "medium",
                  "spoken_name_allowed": true
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "recent_hostile_audio_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertTrue(any(action.text == "右でゾンビピグリンの声する！" for action in result.actions))

    def test_daylight_water_survivor_gets_special_comment(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:10:08+09:00",
              "sequence": 344,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "sky_visible": true,
                "danger_darkness_score": 0.1
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-river-1",
                  "distance": 7.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        result = self.machine.process(event)

        water_actions = [
            action
            for action in result.actions
            if action.text is not None and "スケルトンが水入ってしもた" in action.text
        ]
        self.assertTrue(water_actions)
        self.assertEqual(water_actions[0].protect_ms, 5000)

    def test_daylight_water_survivor_appends_count_info_and_has_two_minute_cooldown(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:10:08+09:00",
              "sequence": 344,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "sky_visible": true,
                "danger_darkness_score": 0.1
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-river-1",
                  "distance": 7.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-river-1",
                  "distance": 6.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "combat_active_hint": false
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:11:08+09:00",
              "sequence": 345,
              "event": {
                "name": "status_snapshot",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "sky_visible": true,
                "danger_darkness_score": 0.1
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-river-1",
                  "distance": 6.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-river-1",
                  "distance": 6.2,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "combat_active_hint": false
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)

        self.assertTrue(
            any(
                action.text is not None
                and "スケルトンが水入ってしもた" in action.text
                and "スケルトン1体、ゾンビ1体おるで。" in action.text
                for action in first_result.actions
            )
        )
        self.assertFalse(
            any(
                action.text is not None and "スケルトンが水入ってしもた" in action.text
                for action in second_result.actions
            )
        )

    def test_daylight_water_skeleton_uses_llm_leaf_when_available(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:10:08+09:00",
              "sequence": 344,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "sky_visible": true,
                "danger_darkness_score": 0.1,
                "biome": "river"
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-river-1",
                  "distance": 7.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        result = machine.process(event)

        self.assertTrue(any(action.text == "LLM:daylight_water_skeleton" for action in result.actions))

    def test_daylight_water_comment_marks_enemy_as_already_handled_for_close_scream(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:10:08+09:00",
              "sequence": 344,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "sky_visible": true,
                "danger_darkness_score": 0.1
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-river-2",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T12:10:10+09:00",
              "sequence": 345,
              "event": {
                "name": "status_snapshot",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "sky_visible": true,
                "danger_darkness_score": 0.1
              },
              "visual_threats": [
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-river-2",
                  "distance": 2.5,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertFalse(any(action.cue_id == "panic_scream_start" for action in result.actions))

    def test_daylight_rain_interrupts_daylight_burn_reaction(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T06:10:08+09:00",
              "sequence": 346,
              "event": {
                "name": "status_snapshot",
                "source_kind": "visual",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "morning",
                "weather": "rain",
                "sky_visible": true,
                "danger_darkness_score": 0.2
              },
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-rain-1",
                  "distance": 8.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "on_fire": false,
                  "in_water": false,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 1,
                "combat_active_hint": false
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.text == "あ！？なんでここで雨がふるねん！！燃えてくれやーー！！" for action in result.actions))

    def test_weather_transition_clear_to_rain_emits_weather_comment(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:10:08+09:00",
              "sequence": 347,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "weather": "clear",
                "sky_visible": true,
                "danger_darkness_score": 0.1,
                "biome": "plains"
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:11:08+09:00",
              "sequence": 348,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "weather": "rain",
                "sky_visible": true,
                "danger_darkness_score": 0.15,
                "biome": "plains"
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertTrue(
            any(
                action.text == "うわっ……雨降ってきたで！くろぉなったらまた敵湧きやすなるで……怖いわぁ！"
                for action in result.actions
            )
        )

    def test_weather_transition_thunder_to_rain_in_cold_biome_emits_snow_comment(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:10:08+09:00",
              "sequence": 349,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "weather": "thunder",
                "sky_visible": true,
                "danger_darkness_score": 0.2,
                "biome": "snowy_plains"
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:11:08+09:00",
              "sequence": 350,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "weather": "rain",
                "sky_visible": true,
                "danger_darkness_score": 0.15,
                "biome": "snowy_plains"
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertTrue(any(action.text == "雪や！きれいやけど……おっさんには辛いわぁ……。" for action in result.actions))

    def test_weather_transition_is_deferred_until_after_dark_entry(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:10:08+09:00",
              "sequence": 351,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 0, "y": 64, "z": 0}
              },
              "world": {
                "time_phase": "day",
                "weather": "clear",
                "sky_visible": true,
                "danger_darkness_score": 0.1,
                "biome": "plains"
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:11:08+09:00",
              "sequence": 352,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "position": {"x": 0, "y": 64, "z": 0}
              },
              "world": {
                "time_phase": "day",
                "weather": "rain",
                "danger_darkness_score": 0.9,
                "local_light": 3,
                "sky_visible": false,
                "enclosure_score": 0.4,
                "ceiling_height": 5.0,
                "overhead_cover_type": "solid",
                "biome": "lush_caves"
              },
              "inventory": {
                "stick": 2
              }
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:11:18+09:00",
              "sequence": 353,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "weather": "rain",
                "sky_visible": true,
                "danger_darkness_score": 0.15,
                "biome": "plains"
              }
            }
            """
        )
        fourth = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T15:11:28+09:00",
              "sequence": 354,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "weather": "rain",
                "sky_visible": true,
                "danger_darkness_score": 0.15,
                "biome": "plains"
              }
            }
            """
        )

        machine.process(first)
        dark_entry = machine.process(second)
        stop_result = machine.process(third)
        result = machine.process(fourth)

        self.assertTrue(any(action.text == "LLM:occluded_entry_no_light" for action in dark_entry.actions))
        self.assertTrue(any(action.layer == "control" and action.interrupt for action in stop_result.actions))
        self.assertTrue(any(action.text == "LLM:weather_transition" for action in result.actions))

    def test_charged_creeper_uses_special_callout(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:01+09:00",
              "sequence": 351,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.7,
                "sky_visible": true,
                "biome": "plains"
              },
              "visual_threats": [
                {
                  "type": "charged_creeper",
                  "entity_id": "charged-creeper-1",
                  "distance": 5.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.text == CHARGED_CREEPER_CALL for action in result.actions))

    def test_stalled_visible_enemy_gets_not_killing_comment(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 345,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-stall-1",
                  "distance": 7.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        later = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:12+09:00",
              "sequence": 346,
              "event": {
                "name": "threat_detected",
                "source_kind": "visual",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-stall-1",
                  "distance": 6.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(later)

        self.assertTrue(any("いやや〜！まだ敵おるやん！" in (action.text or "") for action in result.actions))

    def test_new_uncommented_enemy_within_three_blocks_uses_scream(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 347,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-old-1",
                  "distance": 6.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:10+09:00",
              "sequence": 348,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-old-1",
                  "distance": 5.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "creeper",
                  "entity_id": "creeper-new-1",
                  "distance": 2.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertTrue(any(action.cue_id == "panic_scream_start" for action in result.actions))

    def test_ranged_enemy_is_prioritized_over_closer_melee_for_new_callout(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 349,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-old-1",
                  "distance": 4.5,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:10+09:00",
              "sequence": 350,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-old-1",
                  "distance": 4.6,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "zombie",
                  "entity_id": "zombie-new-2",
                  "distance": 4.1,
                  "direction": {"horizontal": "front_left", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "skeleton",
                  "entity_id": "skeleton-new-1",
                  "distance": 10.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 3,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        result = self.machine.process(second)

        self.assertTrue(any(action.text == "ゾンビが増えたで！" for action in result.actions))

    def test_same_close_enemy_does_not_scream_twice_immediately(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 349,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "creeper",
                  "entity_id": "creeper-close-1",
                  "distance": 2.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:09+09:00",
              "sequence": 350,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "creeper",
                  "entity_id": "creeper-close-1",
                  "distance": 2.4,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)

        self.assertTrue(any(action.cue_id == "panic_scream_start" for action in first_result.actions))
        self.assertFalse(any(action.cue_id == "panic_scream_start" for action in second_result.actions))

    def test_panic_scream_logs_trigger_reason_and_emitted_action(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:08+09:00",
              "sequence": 949,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-old-1",
                  "distance": 5.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:10+09:00",
              "sequence": 950,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "entity_id": "zombie-old-1",
                  "distance": 5.8,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                },
                {
                  "type": "creeper",
                  "entity_id": "creeper-new-1",
                  "distance": 2.5,
                  "direction": {"horizontal": "right", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "combat_active_hint": true
              }
            }
            """
        )

        self.machine.process(first)
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            result = self.machine.process(second)

        self.assertTrue(any(action.cue_id == "panic_scream_start" for action in result.actions))
        self.assertTrue(
            any(
                "panic_cue_decision cue_id=panic_scream_start reason=new_close_visual_ambush" in line
                and "threat=creeper" in line
                and "entity_id=creeper-new-1" in line
                for line in captured.output
            )
        )
        self.assertTrue(
            any(
                "action_emit event=threat_approaching sequence=950" in line
                and "layer=panic_cue" in line
                and "cue_id=panic_scream_start" in line
                for line in captured.output
            )
        )

    def test_suppressed_panic_uses_gasp_then_breath(self) -> None:
        event_json = """
        {
          "schema_version": "2026-05-24",
          "game": "minecraft-java",
          "adapter": "dogido-fabric-client",
          "observed_at": "%s",
          "sequence": %d,
          "event": {
            "name": "threat_approaching",
            "source_kind": "visual",
            "priority_hint": "urgent",
            "certainty": "high"
          },
          "player": {"name": "main_player"},
          "world": {"time_phase": "night", "danger_darkness_score": 0.8},
          "visual_threats": [
            {
              "type": "zombie",
              "distance": 4.0,
              "direction": {"horizontal": "front", "vertical": "same"},
              "approaching": true,
              "certainty": "high"
            }
          ],
          "combat": {
            "recent_hostile_visual_ms": 100,
            "hostiles_within_7": 1,
            "hostiles_within_10": 1,
            "combat_active_hint": true
          },
          "meta": {"user_text": "うるさい"}
        }
        """

        first = None
        for offset in range(3):
            event = GameEvent.model_validate_json(
                event_json % (f"2026-05-25T21:10:0{offset + 1}+09:00", offset + 10)
            )
            result = self.machine.process(event)
            if offset == 2:
                first = result

        self.assertIsNotNone(first)
        self.assertTrue(any(action.cue_id == "suppressed_gasp" for action in first.actions))

        later_event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:05+09:00",
              "sequence": 20,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {"time_phase": "night", "danger_darkness_score": 0.8},
              "visual_threats": [
                {
                  "type": "zombie",
                  "distance": 4.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(later_event)
        self.assertEqual(result.state.mode, "suppressed_panic")
        self.assertTrue(any(action.cue_id == "suppressed_breath" for action in result.actions))

    def test_aftermath_uses_llm_leaf_when_available(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        combat_ended = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:12+09:00",
              "sequence": 21,
              "event": {
                "name": "combat_ended",
                "source_kind": "system",
                "priority_hint": "normal",
                "certainty": "high"
              },
              "player": {"name": "main_player", "health": 15},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.2
              },
              "combat": {
                "recent_damage_ms": 6200,
                "recent_hostile_visual_ms": 6100,
                "recent_hostile_audio_ms": 6300,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              }
            }
            """
        )
        safe_zone = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:10:18+09:00",
              "sequence": 22,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "health": 15},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.1,
                "local_light": 11,
                "sky_visible": false,
                "ceiling_height": 3,
                "nearby_door_count": 2,
                "safe_zone_with_door": true,
                "enclosure_score": 0.26
              },
              "combat": {
                "recent_damage_ms": 7000,
                "recent_hostile_visual_ms": 7000,
                "recent_hostile_audio_ms": 7000,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              }
            }
            """
        )

        machine.process(combat_ended)
        result = machine.process(safe_zone)

        speech = next(action for action in result.actions if action.layer == "speech")
        self.assertEqual(speech.text, "LLM:aftermath")

    def test_stone_house_with_torches_and_door_is_not_treated_as_occluded_dark_entry(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:02+09:00",
              "sequence": 500,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.4,
                "local_light": 9,
                "sky_visible": false,
                "enclosure_score": 0.62,
                "ceiling_height": 4.0,
                "biome": "stony_shore",
                "nearby_door_count": 1,
                "safe_zone_with_door": true
              },
              "inventory": {}
            }
            """
        )

        self.assertFalse(self.machine._is_occluded_dark_zone_event(event))
        self.assertFalse(self.machine._entered_occluded_dark_zone(event))

    def test_lit_wood_interior_without_door_is_not_treated_as_occluded_dark_entry(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T21:11:02+09:00",
              "sequence": 5001,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.66,
                "local_light": 10,
                "sky_visible": false,
                "enclosure_score": 0.72,
                "ceiling_height": 4.0,
                "overhead_cover_type": "wood",
                "connected_dark_volume": 18,
                "nearest_dark_spawn_distance": 5.0,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        self.assertFalse(machine._is_occluded_dark_zone_event(event))

        result = machine.process(event)

        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_nearby_actual_light_source_suppresses_occluded_dark_callout(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-04T21:15:02+09:00",
              "sequence": 5002,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.68,
                "local_light": 6,
                "sky_visible": false,
                "enclosure_score": 0.70,
                "ceiling_height": 4.0,
                "overhead_cover_type": "wood",
                "connected_dark_volume": 18,
                "nearest_dark_spawn_distance": 5.0,
                "nearby_light_source_count": 2,
                "nearest_light_source_distance": 2.0,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        self.assertFalse(machine._is_occluded_dark_zone_event(event))

        result = machine.process(event)

        self.assertFalse(any(action.text == "LLM:occluded_entry_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:dark_push_no_light" for action in result.actions))
        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_safe_zone_with_door_suppresses_darkness_escape_advice(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-25T21:11:05+09:00",
              "sequence": 501,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "bread"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.84,
                "local_light": 10,
                "sky_visible": false,
                "enclosure_score": 0.72,
                "ceiling_height": 2.0,
                "biome": "stony_shore",
                "nearby_door_count": 1,
                "safe_zone_with_door": true
              },
              "inventory": {}
            }
            """
        )

        result = machine.process(event)

        self.assertFalse(any(action.text == "LLM:darkness_escape" for action in result.actions))

    def test_near_non_home_bed_at_night_emits_sleep_prompt(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:11:05+09:00",
              "sequence": 701,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "bread"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "stony_shore",
                "nearby_bed_count": 1
              },
              "inventory": {"torch": 1}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))

    def test_near_respawn_point_at_night_emits_home_sleep_prompt(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:11:25+09:00",
              "sequence": 701,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "held_item": "bread"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "stony_shore",
                "respawn_point_set": true,
                "respawn_distance": 8.0
              },
              "inventory": {"torch": 1}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))

    def test_sleep_prompt_is_suppressed_for_one_minute(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:11:25+09:00",
              "sequence": 701,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "stony_shore",
                "respawn_point_set": true,
                "respawn_distance": 8.0
              },
              "inventory": {"torch": 1}
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:11:55+09:00",
              "sequence": 702,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "stony_shore",
                "respawn_point_set": true,
                "respawn_distance": 8.0
              },
              "inventory": {"torch": 1}
            }
            """
        )
        third = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:12:30+09:00",
              "sequence": 703,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "stony_shore",
                "respawn_point_set": true,
                "respawn_distance": 8.0
              },
              "inventory": {"torch": 1}
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)
        third_result = self.machine.process(third)

        self.assertFalse(any(action.layer == "speech" for action in first_result.actions))
        self.assertFalse(any(action.layer == "speech" for action in second_result.actions))
        self.assertFalse(any(action.layer == "speech" for action in third_result.actions))

    def test_night_without_bed_or_home_does_not_emit_sleep_prompt(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:12:05+09:00",
              "sequence": 702,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player"
              },
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.10,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "plains",
                "nearby_bed_count": 0
              },
              "inventory": {"torch": 1}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(
            any(
                action.text in {"さ。ねよねよ！", "ベッドあるし、寝よか。"}
                for action in result.actions
            )
        )

    def test_sleeping_neighbor_emits_llm_observation(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:13:05+09:00",
              "sequence": 703,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.10,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.23,
                "ceiling_height": 24.0,
                "biome": "plains",
                "nearby_sleeping_people_count": 1
              },
              "inventory": {"torch": 1}
            }
            """
        )

        result = machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))

    def test_sleeping_neighbor_comment_takes_priority_over_sleep_prompt_when_bed_is_occupied(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-28T21:14:05+09:00",
              "sequence": 704,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.10,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "biome": "plains",
                "nearby_bed_count": 1,
                "nearby_sleeping_people_count": 1
              },
              "inventory": {"torch": 1}
            }
            """
        )
        result = machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))

    def test_foliage_shade_emits_tree_cover_warning_and_then_suppresses(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T12:14:05+09:00",
              "sequence": 801,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.60,
                "local_light": 7,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "overhead_cover_type": "foliage",
                "biome": "jungle"
              },
              "inventory": {}
            }
            """
        )
        again = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T12:01:05+09:00",
              "sequence": 802,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.62,
                "local_light": 7,
                "sky_visible": true,
                "enclosure_score": 0.10,
                "ceiling_height": 24.0,
                "overhead_cover_type": "foliage",
                "biome": "jungle"
              },
              "inventory": {}
            }
            """
        )

        first = self.machine.process(event)
        second = self.machine.process(again)

        speech = next(action for action in first.actions if action.layer == "speech")
        self.assertEqual(
            speech.text,
            "木がしげっているとこは暗いわー。こういうとこはおひさんでとっても敵が残っとるんやで……。",
        )
        self.assertFalse(any(action.layer == "speech" for action in second.actions))

    def test_shallow_submerged_dark_zone_does_not_emit_underwater_darkness_line(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T12:24:05+09:00",
              "sequence": 803,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.80,
                "local_light": 2,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "ceiling_height": 1.0,
                "overhead_cover_type": "fluid",
                "biome": "river",
                "is_submerged": true,
                "submerged_depth_blocks": 2
              },
              "inventory": {}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(
            any(
                action.text == "夕方や！あと1分もしないうちに敵出るで！"
                for action in result.actions
            )
        )

    def test_deep_submerged_dark_zone_emits_darkness_then_haiku_after_silence(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=FakeLLM())
        first_event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T12:24:05+09:00",
              "sequence": 804,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.80,
                "local_light": 2,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "ceiling_height": 1.0,
                "overhead_cover_type": "fluid",
                "biome": "river",
                "is_submerged": true,
                "submerged_depth_blocks": 6
              },
              "inventory": {}
            }
            """
        )
        second_event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T12:29:05+09:00",
              "sequence": 805,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "day",
                "danger_darkness_score": 0.82,
                "local_light": 1,
                "sky_visible": false,
                "enclosure_score": 1.0,
                "ceiling_height": 1.0,
                "overhead_cover_type": "fluid",
                "biome": "river",
                "is_submerged": true,
                "submerged_depth_blocks": 6
              },
              "inventory": {}
            }
            """
        )

        def calm_river_snapshot(sequence: int, observed_at: str) -> GameEvent:
            # 水面近くまで戻って明るくなった川（暗所ラインは出ない静かな場面）
            return GameEvent.model_validate(
                {
                    "schema_version": "2026-05-24",
                    "game": "minecraft-java",
                    "adapter": "dogido-fabric-client",
                    "observed_at": observed_at,
                    "sequence": sequence,
                    "event": {
                        "name": "status_snapshot",
                        "source_kind": "system",
                        "priority_hint": "background",
                        "certainty": "high",
                    },
                    "player": {"name": "main_player"},
                    "world": {
                        "time_phase": "day",
                        "danger_darkness_score": 0.10,
                        "local_light": 12,
                        "sky_visible": True,
                        "enclosure_score": 0.05,
                        "ceiling_height": 24.0,
                        "biome": "river",
                        "is_submerged": True,
                        "submerged_depth_blocks": 1,
                    },
                    "inventory": {},
                }
            )

        third_event = calm_river_snapshot(806, "2026-05-31T12:34:10+09:00")
        fourth_event = calm_river_snapshot(807, "2026-05-31T12:34:12+09:00")

        first = machine.process(first_event)
        second = machine.process(second_event)
        third = machine.process(third_event)
        fourth = machine.process(fourth_event)

        speech = next(action for action in first.actions if action.layer == "speech")
        self.assertEqual(speech.text, "……暗いのは、にがてなんやけど……。")
        # 川柳の周期（10分）が満ちるまでは詠まない
        self.assertEqual([action.text for action in second.actions if action.layer == "speech"], [])
        # 周期が満ちたら発句し、次のスナップショットで本句を出す
        self.assertEqual(
            [action.text for action in third.actions if action.layer == "speech"],
            ["ここで一句。"],
        )
        self.assertEqual(
            [action.text for action in fourth.actions if action.layer == "speech"],
            ["五月雨を　集めてはやし　シミュレート"],
        )

    def test_firefly_reaction_happens_once_per_night(self) -> None:
        first_event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T21:24:05+09:00",
              "sequence": 806,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 8,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains",
                "nearby_firefly_bush_count": 2
              },
              "inventory": {"torch": 1}
            }
            """
        )
        second_event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T21:24:15+09:00",
              "sequence": 807,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player"},
              "world": {
                "time_phase": "night",
                "danger_darkness_score": 0.20,
                "local_light": 8,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains",
                "nearby_firefly_bush_count": 2
              },
              "inventory": {"torch": 1}
            }
            """
        )

        first = self.machine.process(first_event)
        second = self.machine.process(second_event)

        self.assertTrue(any(action.cue_id == "suppressed_gasp" for action in first.actions))
        speech = next(action for action in first.actions if action.layer == "speech")
        self.assertEqual(speech.text, "なんや。ほたるかいな……驚いて損したわ……。")
        self.assertFalse(any(action.layer == "speech" for action in second.actions))

    def test_evening_surface_warning_emits_once_per_cycle(self) -> None:
        evening = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:05:00+09:00",
              "sequence": 808,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {"torch": 1}
            }
            """
        )
        night = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:06:00+09:00",
              "sequence": 809,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "night",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {"torch": 1}
            }
            """
        )

        first = self.machine.process(evening)
        second = self.machine.process(night)

        speech = next(action for action in first.actions if action.layer == "speech")
        self.assertEqual(speech.text, "夕方や！あと1分もしないうちに敵出るで！")
        self.assertFalse(
            any(
                action.text == "夕方や！あと1分もしないうちに敵出るで！"
                for action in second.actions
            )
        )

    def test_evening_surface_warning_resets_after_daytime(self) -> None:
        first_evening = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:05:00+09:00",
              "sequence": 810,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {"torch": 1}
            }
            """
        )
        daytime = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-01T10:05:00+09:00",
              "sequence": 811,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "day",
                "weather": "clear",
                "danger_darkness_score": 0.10,
                "local_light": 15,
                "sky_visible": true,
                "enclosure_score": 0.02,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {"torch": 1}
            }
            """
        )
        second_evening = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-01T18:05:00+09:00",
              "sequence": 812,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {"torch": 1}
            }
            """
        )

        self.machine.process(first_evening)
        self.machine.process(daytime)
        result = self.machine.process(second_evening)

        speech = next(action for action in result.actions if action.layer == "speech")
        self.assertEqual(speech.text, "夕方や！あと1分もしないうちに敵出るで！")

    def test_cave_biome_evening_uses_surface_exit_warning(self) -> None:
        daytime = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T16:10:00+09:00",
              "sequence": 813,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "day",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": false,
                "enclosure_score": 0.72,
                "ceiling_height": 8.0,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:10:00+09:00",
              "sequence": 814,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": false,
                "enclosure_score": 0.72,
                "ceiling_height": 8.0,
                "biome": "dripstone_caves"
              },
              "inventory": {}
            }
            """
        )

        self.machine.process(daytime)
        result = self.machine.process(event)

        speech = next(action for action in result.actions if action.layer == "speech")
        self.assertEqual(
            speech.text,
            "そろそろ夕方やなー。今から地上に出ると敵とかち合うなー",
        )

    def test_underwater_night_warning_waits_until_threats_are_gone(self) -> None:
        combat = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:11:00+09:00",
              "sequence": 814,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.40,
                "local_light": 9,
                "sky_visible": true,
                "enclosure_score": 0.08,
                "ceiling_height": 24.0,
                "biome": "river",
                "is_submerged": true,
                "submerged_depth_blocks": 6
              },
              "visual_threats": [
                {
                  "type": "drowned",
                  "entity_id": "drowned-warning-1",
                  "distance": 5.0,
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        clear = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:12:00+09:00",
              "sequence": 815,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "night",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 10,
                "sky_visible": true,
                "enclosure_score": 0.08,
                "ceiling_height": 24.0,
                "biome": "river",
                "is_submerged": true,
                "submerged_depth_blocks": 6
              },
              "combat": {
                "recent_hostile_visual_ms": 9000,
                "recent_hostile_audio_ms": 9000,
                "recent_damage_ms": 9000,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "combat_active_hint": false
              },
              "inventory": {}
            }
            """
        )

        first = self.machine.process(combat)
        second = self.machine.process(clear)

        self.assertFalse(
            any(
                action.text == "そろそろ夕方やなー。今から地上に出ると敵とかち合うなー"
                for action in first.actions
            )
        )
        speech = next(action for action in second.actions if action.layer == "speech")
        self.assertEqual(
            speech.text,
            "そろそろ夜やなー。今から地上に出ると敵とかち合うなー",
        )

    def test_evening_warning_is_suppressed_in_the_end(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:15:00+09:00",
              "sequence": 816,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:the_end"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "the_end"
              },
              "inventory": {}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))

    def test_evening_warning_is_suppressed_in_dark_forest(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:16:00+09:00",
              "sequence": 817,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.08,
                "ceiling_height": 24.0,
                "biome": "dark_forest"
              },
              "inventory": {}
            }
            """
        )

        result = self.machine.process(event)

        self.assertFalse(
            any(
                action.text == "夕方や！あと1分もしないうちに敵出るで！"
                for action in result.actions
            )
        )

    def test_evening_warning_interrupts_user_input_with_attention_then_detail(self) -> None:
        talking = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:18:00+09:00",
              "sequence": 818,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {},
              "meta": {"user_text": "ちょっと待って"}
            }
            """
        )
        later = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-05-31T18:18:10+09:00",
              "sequence": 819,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {
                "name": "main_player",
                "dimension": "minecraft:overworld"
              },
              "world": {
                "time_phase": "evening",
                "weather": "clear",
                "danger_darkness_score": 0.20,
                "local_light": 12,
                "sky_visible": true,
                "enclosure_score": 0.05,
                "ceiling_height": 24.0,
                "biome": "plains"
              },
              "inventory": {}
            }
            """
        )

        first = self.machine.process(talking)
        second = self.machine.process(later)

        # 夕方警告は時限性が高いので、入力中でも注意喚起行で割り込む
        attention = next(action for action in first.actions if action.layer == "speech")
        self.assertEqual(attention.text, "！そろそろ夜やで！")
        self.assertTrue(attention.interrupt)
        # 本文は次のイベントで出す
        speech = next(action for action in second.actions if action.layer == "speech")
        self.assertEqual(speech.text, "夕方や！あと1分もしないうちに敵出るで！")

    def test_mass_hostile_callout_latches_until_query_range_clears(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:00:00+09:00",
              "sequence": 900,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 1.0},
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp1", "distance": 5.0, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp2", "distance": 5.5, "direction": {"horizontal": "left", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "hostiles_within_30_ground": 5,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:00:02+09:00",
              "sequence": 901,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 1.0},
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp1", "distance": 4.9, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": false, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 5,
                "combat_active_hint": true
              }
            }
            """
        )
        cleared = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:00:10+09:00",
              "sequence": 902,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 0.1},
              "combat": {
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "hostiles_within_30_ground": 0,
                "combat_active_hint": false
              }
            }
            """
        )
        again = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:00:20+09:00",
              "sequence": 903,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 1.0},
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp3", "distance": 5.2, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 4,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = self.machine.process(first)
        second_result = self.machine.process(second)
        self.machine.process(cleared)
        again_result = self.machine.process(again)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in first_result.actions))
        self.assertTrue(any(action.text in HOSTILE_MASSIVE_VARIANTS for action in first_result.actions))
        self.assertFalse(any(action.layer == "callout" for action in second_result.actions))
        self.assertTrue(any(action.text in HOSTILE_MASSIVE_VARIANTS for action in again_result.actions))

    def test_player_query_answers_ground_hostile_count_within_thirty_blocks(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T12:00:00+09:00",
              "sequence": 920,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {"time_phase": "day", "biome": "plains", "danger_darkness_score": 0.1, "sky_visible": true},
              "combat": {
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "hostiles_within_30_ground": 3,
                "combat_active_hint": false
              },
              "meta": {"user_text": "敵残り何体？"}
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.layer == "speech" and action.text == "30マス以内には今は3体おるで。" for action in result.actions))

    def test_audio_only_event_does_not_clear_mass_hostile_latch(self) -> None:
        first = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:10:00+09:00",
              "sequence": 925,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 1.0},
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp1", "distance": 5.0, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"},
                {"type": "zombified_piglin", "entity_id": "zp2", "distance": 5.5, "direction": {"horizontal": "left", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 2,
                "hostiles_within_10": 2,
                "hostiles_within_30_ground": 5,
                "combat_active_hint": true
              }
            }
            """
        )
        audio_only = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:10:01+09:00",
              "sequence": 926,
              "event": {
                "name": "hostile_audio_detected",
                "source_kind": "auditory",
                "priority_hint": "normal",
                "certainty": "medium"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 1.0},
              "auditory_threats": [
                {
                  "label": "zombified_piglin",
                  "source_id": "zp-a1",
                  "sound_event": "entity.zombie_pigman.ambient",
                  "direction": {"horizontal": "front", "vertical": "same"},
                  "distance_band": "close",
                  "certainty": "medium",
                  "spoken_name_allowed": true
                }
              ],
              "combat": {
                "recent_hostile_audio_ms": 100,
                "hostiles_within_30_ground": 5,
                "combat_active_hint": true
              }
            }
            """
        )
        second = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T18:10:02+09:00",
              "sequence": 927,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 1.0},
              "visual_threats": [
                {"type": "zombified_piglin", "entity_id": "zp1", "distance": 4.8, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": false, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "recent_hostile_audio_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 5,
                "combat_active_hint": true
              }
            }
            """
        )

        first_result = self.machine.process(first)
        self.machine.process(audio_only)
        second_result = self.machine.process(second)

        self.assertTrue(any(action.text in HOSTILE_MASSIVE_VARIANTS for action in first_result.actions))
        self.assertFalse(any(action.text in HOSTILE_MASSIVE_VARIANTS for action in second_result.actions))

    def test_flying_hostile_entry_warns_from_above(self) -> None:
        event = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T23:00:00+09:00",
              "sequence": 930,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {"time_phase": "night", "biome": "plains", "danger_darkness_score": 0.6, "sky_visible": true},
              "visual_threats": [
                {
                  "type": "phantom",
                  "entity_id": "phantom-1",
                  "distance": 20.0,
                  "direction": {"horizontal": "front", "vertical": "above"},
                  "approaching": true,
                  "certainty": "high"
                }
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "hostiles_within_30_ground": 0,
                "combat_active_hint": true
              }
            }
            """
        )

        result = self.machine.process(event)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" and action.text == "上からファントムきたで！" for action in result.actions))

    def test_overworld_return_line_waits_until_threats_clear(self) -> None:
        nether = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T21:00:00+09:00",
              "sequence": 940,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:the_nether"},
              "world": {"biome": "nether_wastes", "danger_darkness_score": 0.9},
              "combat": {"hostiles_within_30_ground": 0, "combat_active_hint": false}
            }
            """
        )
        change = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T21:00:05+09:00",
              "sequence": 941,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {"time_phase": "night", "biome": "plains", "danger_darkness_score": 0.4, "sky_visible": true},
              "visual_threats": [
                {"type": "zombie", "entity_id": "z1", "distance": 5.0, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        threat = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T21:00:07+09:00",
              "sequence": 942,
              "event": {
                "name": "threat_approaching",
                "source_kind": "visual",
                "priority_hint": "urgent",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {"time_phase": "night", "biome": "plains", "danger_darkness_score": 0.4, "sky_visible": true},
              "visual_threats": [
                {"type": "zombie", "entity_id": "z1", "distance": 4.5, "direction": {"horizontal": "front", "vertical": "same"}, "approaching": true, "certainty": "high"}
              ],
              "combat": {
                "recent_hostile_visual_ms": 100,
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
                "combat_active_hint": true
              }
            }
            """
        )
        clear = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T21:00:08+09:00",
              "sequence": 943,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {"time_phase": "night", "biome": "plains", "danger_darkness_score": 0.1, "sky_visible": true},
              "combat": {
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "hostiles_within_30_ground": 0,
                "combat_active_hint": false
              }
            }
            """
        )
        later = GameEvent.model_validate_json(
            """
            {
              "schema_version": "2026-05-24",
              "game": "minecraft-java",
              "adapter": "dogido-fabric-client",
              "observed_at": "2026-06-05T21:00:10+09:00",
              "sequence": 944,
              "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high"
              },
              "player": {"name": "main_player", "dimension": "minecraft:overworld"},
              "world": {"time_phase": "night", "biome": "plains", "danger_darkness_score": 0.1, "sky_visible": true},
              "combat": {
                "hostiles_within_7": 0,
                "hostiles_within_10": 0,
                "hostiles_within_30_ground": 0,
                "combat_active_hint": false
              }
            }
            """
        )

        self.machine.process(nether)
        changed_result = self.machine.process(change)
        threat_result = self.machine.process(threat)
        clear_result = self.machine.process(clear)
        later_result = self.machine.process(later)

        self.assertTrue(any(action.layer == "flush" for action in changed_result.actions))
        self.assertFalse(any(action.text == "オーバーワールドは落ち着くな・・・" for action in threat_result.actions))
        self.assertFalse(any(action.text == "オーバーワールドは落ち着くな・・・" for action in clear_result.actions))
        self.assertTrue(any(action.text == "オーバーワールドは落ち着くな・・・" for action in later_result.actions))


if __name__ == "__main__":
    unittest.main()
