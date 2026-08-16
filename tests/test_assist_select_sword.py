from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from dogido_server.assist import ActionContext, ActionName, build_assist_registry
from dogido_server.assist.select_sword import (
    SELECT_HOTBAR_CAPABILITY,
    finalize_select_sword_intent_payload,
    interpret_voice_select_sword_request,
    is_explicit_select_sword_request,
    select_weapon_slot,
)
from dogido_server.config import Settings
from dogido_server.models import (
    AdapterCommandResult,
    AdapterSessionCreateRequest,
    GameEvent,
    HotbarSlot,
)
from dogido_server.player_input.normalize import is_too_short_voice_text
from dogido_server.player_input.routing import route_player_input
from dogido_server.service import DogidoService


OBSERVED_AT = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def hotbar_slots() -> list[dict[str, object]]:
    return [
        {
            "slot": 0,
            "item_id": "minecraft:diamond_sword",
            "count": 1,
            "damage": 10,
            "max_damage": 1561,
            "attack_damage": 7.0,
            "weapon_kind": "sword",
        },
        {
            "slot": 1,
            "item_id": "minecraft:stone_sword",
            "count": 1,
            "damage": 120,
            "max_damage": 131,
            "attack_damage": 5.0,
            "weapon_kind": "sword",
        },
        *[
            {"slot": slot, "count": 0, "weapon_kind": "empty"}
            for slot in range(2, 9)
        ],
    ]


def make_event(
    *,
    sequence: int,
    user_text: str | None = None,
    slots: list[dict[str, object]] | None = None,
    command_results: list[dict[str, object]] | None = None,
) -> GameEvent:
    return GameEvent.model_validate(
        {
            "schema_version": "2026-05-24",
            "game": "minecraft-java",
            "adapter": "test-adapter",
            "observed_at": (OBSERVED_AT + timedelta(seconds=sequence)).isoformat(),
            "sequence": sequence,
            "event": {
                "name": "status_snapshot",
                "source_kind": "system",
                "priority_hint": "background",
                "certainty": "high",
            },
            "player": {
                "name": "main_player",
                "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                "dimension": "minecraft:overworld",
                "held_item": "minecraft:torch",
                "hotbar": {
                    "selected_slot": 8,
                    "slots": slots if slots is not None else hotbar_slots(),
                },
            },
            "world": {
                "time_phase": "day",
                "weather": "clear",
                "biome": "plains",
                "local_light": 15,
                "sky_visible": True,
                "danger_darkness_score": 0.0,
            },
            "combat": {"combat_active_hint": False},
            "meta": {"user_text": user_text},
            "command_results": command_results or [],
        }
    )


def make_panic_event(*, sequence: int, user_text: str | None = None) -> GameEvent:
    payload = make_event(sequence=sequence, user_text=user_text).model_dump(mode="json")
    payload["event"] = {
        "name": "threat_approaching",
        "source_kind": "visual",
        "priority_hint": "urgent",
        "certainty": "high",
    }
    payload["visual_threats"] = [
        {
            "type": "creeper",
            "entity_id": "creeper-1",
            "distance": 4.0,
            "direction": {"horizontal": "back", "vertical": "same"},
            "approaching": True,
            "certainty": "high",
        }
    ]
    payload["combat"] = {
        "recent_hostile_visual_ms": 100,
        "hostiles_within_7": 1,
        "hostiles_within_10": 1,
        "combat_active_hint": True,
    }
    return GameEvent.model_validate(payload)


def create_session(service: DogidoService, *, executable: bool = True) -> str:
    response = service.create_session(
        AdapterSessionCreateRequest(
            adapter_name="test-adapter",
            adapter_version="1",
            schema_version="2026-05-24",
            player_name="main_player",
            capabilities=["hotbar_slots"],
            execution_capabilities=(
                [SELECT_HOTBAR_CAPABILITY] if executable else []
            ),
        )
    )
    return response.session_id


class SelectSwordRulesTest(unittest.TestCase):
    def test_explicit_fast_path_is_narrow(self) -> None:
        for text in ("剣", "剣に持ち替えて", "けん装備して", "ソードにして"):
            with self.subTest(text=text):
                self.assertTrue(is_explicit_select_sword_request(text))
                self.assertTrue(route_player_input(text).requests_sword)
        for text in (
            "剣の話をしよう",
            "剣ある？",
            "その剣かっこいい",
            "剣を持ってる？",
            "剣に持ち替えないで",
            "剣に切り替えなくていい",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_explicit_select_sword_request(text))
        self.assertFalse(route_player_input("/say 剣").requests_sword)

    def test_voice_short_sword_is_allowed(self) -> None:
        self.assertFalse(is_too_short_voice_text("剣"))
        self.assertFalse(is_too_short_voice_text("けん"))

    def test_voice_sword_homophone_repair_requires_adjacent_action_and_stays_narrow(self) -> None:
        examples = {
            "ドギドを県に持ち替えてください": "ドギドを剣に持ち替えてください",
            "ドギド、県に変えてください": "ドギド、剣に変えてください",
            "県にハインコをしてください": "剣に変更をしてください",
            "チェンに変更してください": "剣に変更してください",
            "ケンに装備して": "剣に装備して",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(interpret_voice_select_sword_request(raw), expected)
        for text in (
            "県",
            "チェン",
            "チェンの話をしよう",
            "県の話をしよう",
            "県に持ち替えないで",
            "大阪府から他の県に行きたい",
        ):
            with self.subTest(text=text):
                self.assertIsNone(interpret_voice_select_sword_request(text))

    def test_qwen_payload_requires_closed_values_confidence_and_grounded_evidence(self) -> None:
        accepted = finalize_select_sword_intent_payload(
            {
                "intent": "select_weapon",
                "weapon_kind": "sword",
                "is_request": True,
                "evidence": "剣の方がよくない？",
                "confidence": 0.94,
            },
            player_text="そろそろ剣の方がよくない？",
        )
        self.assertTrue(accepted.requested)
        for payload in (
            {
                "intent": "other",
                "weapon_kind": "sword",
                "is_request": True,
                "evidence": "剣の方がよくない？",
                "confidence": 0.99,
            },
            {
                "intent": "select_weapon",
                "weapon_kind": "sword",
                "is_request": True,
                "evidence": "補作した依頼",
                "confidence": 0.99,
            },
            {
                "intent": "select_weapon",
                "weapon_kind": "sword",
                "is_request": True,
                "evidence": "剣の方がよくない？",
                "confidence": 0.80,
            },
            {
                "intent": "select_weapon",
                "weapon_kind": "sword",
                "is_request": True,
                "evidence": "剣に持ち替えないで",
                "confidence": 0.99,
            },
        ):
            self.assertFalse(
                finalize_select_sword_intent_payload(
                    payload,
                    player_text=(
                        "剣に持ち替えないで"
                        if payload.get("evidence") == "剣に持ち替えないで"
                        else "そろそろ剣の方がよくない？"
                    ),
                ).requested
            )

    def test_selection_prefers_lower_attack_then_lower_remaining_durability(self) -> None:
        selection = select_weapon_slot(
            [
                HotbarSlot(slot=0, item_id="minecraft:iron_sword", count=1, max_damage=250, damage=245, attack_damage=6, weapon_kind="sword"),
                HotbarSlot(slot=1, item_id="minecraft:stone_sword", count=1, max_damage=131, damage=10, attack_damage=5, weapon_kind="sword"),
                HotbarSlot(slot=2, item_id="minecraft:stone_sword", count=1, max_damage=131, damage=125, attack_damage=5, weapon_kind="sword"),
            ]
        )
        self.assertIsNotNone(selection)
        self.assertEqual(selection.slot, 2)  # type: ignore[union-attr]

    def test_selection_fallback_order_and_strongest_tool(self) -> None:
        selection = select_weapon_slot(
            [
                HotbarSlot(slot=0, item_id="minecraft:bow", count=1, max_damage=384, damage=10, attack_damage=1, weapon_kind="bow"),
                HotbarSlot(slot=1, item_id="minecraft:iron_axe", count=1, max_damage=250, damage=5, attack_damage=9, weapon_kind="axe"),
                HotbarSlot(slot=2, item_id="minecraft:trident", count=1, max_damage=250, damage=5, attack_damage=9, weapon_kind="trident"),
            ]
        )
        self.assertEqual(selection.weapon_kind, "trident")  # type: ignore[union-attr]
        tools = select_weapon_slot(
            [
                HotbarSlot(slot=3, item_id="minecraft:wooden_pickaxe", count=1, max_damage=59, damage=58, attack_damage=2, weapon_kind="tool"),
                HotbarSlot(slot=4, item_id="minecraft:iron_pickaxe", count=1, max_damage=250, damage=20, attack_damage=4, weapon_kind="tool"),
            ]
        )
        self.assertEqual(tools.slot, 4)  # type: ignore[union-attr]

    def test_gate_requires_execution_capability_and_current_hotbar(self) -> None:
        event = make_event(sequence=1)
        registry = build_assist_registry()
        denied = registry.propose(
            ActionName.SELECT_SWORD,
            ActionContext(event, frozenset(), OBSERVED_AT),
        )
        self.assertEqual(denied.detail_code, "capability_missing")
        allowed = registry.propose(
            ActionName.SELECT_SWORD,
            ActionContext(event, frozenset({SELECT_HOTBAR_CAPABILITY}), OBSERVED_AT),
        )
        self.assertEqual(allowed.status, "command")
        self.assertEqual(allowed.command.slot, 1)  # type: ignore[union-attr]


class SelectSwordServiceTest(unittest.TestCase):
    def test_command_is_returned_redelivered_until_result_and_acknowledged(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)

        first = service.process_event(make_event(sequence=1, user_text="剣に持ち替えて"), session_id)
        self.assertEqual(len(first.response.commands), 1)
        command = first.response.commands[0]
        self.assertEqual(command.slot, 1)
        self.assertTrue(any(action.text == "剣やな、持ち替えるで！" for action in first.actions))

        redelivery = service.process_event(make_event(sequence=2), session_id)
        self.assertEqual([row.command_id for row in redelivery.response.commands], [command.command_id])

        result = {
            "command_id": command.command_id,
            "command_type": "select_hotbar",
            "status": "succeeded",
            "executed_at": (command.issued_at + timedelta(milliseconds=500)).isoformat(),
            "selected_slot": command.slot,
            "selected_item_id": command.expected_item_id,
            "detail_code": "selected",
        }
        completed = service.process_event(
            make_event(sequence=3, command_results=[result]),
            session_id,
        )
        self.assertEqual(completed.response.acknowledged_command_ids, [command.command_id])
        self.assertEqual(completed.response.commands, [])

        duplicate_result = service.process_event(
            make_event(sequence=4, command_results=[result]),
            session_id,
        )
        self.assertEqual(duplicate_result.response.acknowledged_command_ids, [command.command_id])
        self.assertEqual(duplicate_result.response.commands, [])

    def test_no_execution_capability_fails_closed(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service, executable=False)
        result = service.process_event(make_event(sequence=1, user_text="剣"), session_id)
        self.assertEqual(result.response.commands, [])
        self.assertTrue(any("まだ持ち替え操作" in (action.text or "") for action in result.actions))

    def test_no_weapon_returns_fixed_feedback(self) -> None:
        empty = [{"slot": slot, "count": 0, "weapon_kind": "empty"} for slot in range(9)]
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        result = service.process_event(make_event(sequence=1, user_text="剣", slots=empty), session_id)
        self.assertEqual(result.response.commands, [])
        self.assertTrue(any("ホットバーにない" in (action.text or "") for action in result.actions))

    def test_batch_preserves_command(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        response, _actions = service.process_batch(
            [make_event(sequence=1, user_text="剣"), make_event(sequence=2)],
            session_id,
        )
        self.assertEqual(len(response.commands), 1)

    def test_open_workshop_keeps_ambiguous_sword_word_but_allows_explicit_equip(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        session = service.sessions[session_id]
        session.haiku_workshop = SimpleNamespace(open=True, combat_paused=False)  # type: ignore[assignment]

        ambiguous = service._route_assist_player_input(  # noqa: SLF001
            session,
            "剣にして",
            interpreted_player_text=None,
        )
        explicit = service._route_assist_player_input(  # noqa: SLF001
            session,
            "剣に持ち替えて",
            interpreted_player_text=None,
        )

        self.assertFalse(ambiguous.requests_sword)
        self.assertTrue(explicit.requests_sword)

    def test_success_cooldown_suppresses_immediate_repeated_execution(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        issued = service.process_event(make_event(sequence=1, user_text="剣"), session_id)
        command = issued.response.commands[0]
        service.process_event(
            make_event(
                sequence=2,
                command_results=[
                    {
                        "command_id": command.command_id,
                        "command_type": "select_hotbar",
                        "status": "succeeded",
                        "executed_at": (
                            command.issued_at + timedelta(milliseconds=500)
                        ).isoformat(),
                        "selected_slot": command.slot,
                        "selected_item_id": command.expected_item_id,
                        "detail_code": "selected",
                    }
                ],
            ),
            session_id,
        )

        repeated = service.process_event(make_event(sequence=3, user_text="剣"), session_id)

        self.assertEqual(repeated.response.commands, [])
        self.assertTrue(any("もう持ち替えた" in (action.text or "") for action in repeated.actions))

    def test_voice_fast_path_executes_during_panic_without_dropping_safety_layers(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        pushed = service.push_player_input("剣", source="voice")
        self.assertTrue(pushed["accepted"])

        result = service.process_event(make_panic_event(sequence=1), session_id)

        self.assertEqual(len(result.response.commands), 1)
        layers = {action.layer for action in result.actions}
        self.assertTrue(layers.intersection({"panic_cue", "callout"}))

    def test_voice_sword_homophone_executes_and_episode_preserves_raw_text(self) -> None:
        with TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp),
                )
            )
            session_id = create_session(service)
            raw = "ドギドを県に持ち替えてください"
            self.assertTrue(service.push_player_input(raw, source="voice")["accepted"])

            result = service.process_event(make_event(sequence=1), session_id)

            self.assertEqual(len(result.response.commands), 1)
            self.assertEqual(
                service.sessions[session_id].machine.player_input.assist_intent_source,
                "code_voice_asr",
            )
            row = json.loads(
                (Path(tmp) / "eval" / "episodes.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertEqual(row["trigger"]["player_input"]["raw"], raw)
            self.assertEqual(
                row["trigger"]["player_input"]["interpreted"],
                "ドギドを剣に持ち替えてください",
            )

    def test_typed_or_non_command_prefecture_text_never_executes(self) -> None:
        typed = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        typed_session = create_session(typed)
        typed_result = typed.process_event(
            make_event(sequence=1, user_text="ドギドを県に持ち替えてください"),
            typed_session,
        )
        self.assertEqual(typed_result.response.commands, [])

        voice = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        voice_session = create_session(voice)
        self.assertTrue(voice.push_player_input("県の話をしよう", source="voice")["accepted"])
        voice_result = voice.process_event(make_event(sequence=1), voice_session)
        self.assertEqual(voice_result.response.commands, [])

    def test_observed_voice_change_variants_execute_but_typed_variant_does_not(self) -> None:
        for sequence, raw in enumerate(
            (
                "時と県に変えて",
                "チェンに変更してください",
                "持ち物を県に変えてください",
                "県にハインコをしてください",
            ),
            start=1,
        ):
            with self.subTest(raw=raw):
                service = DogidoService(
                    Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False)
                )
                session_id = create_session(service)
                self.assertTrue(service.push_player_input(raw, source="voice")["accepted"])
                result = service.process_event(make_event(sequence=sequence), session_id)
                self.assertEqual(len(result.response.commands), 1)

        typed = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        typed_session = create_session(typed)
        typed_result = typed.process_event(
            make_event(sequence=1, user_text="チェンに変更してください"),
            typed_session,
        )
        self.assertEqual(typed_result.response.commands, [])

    def test_voice_sword_homophone_bypasses_panic_hold(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        service.process_event(make_panic_event(sequence=1), session_id)
        self.assertEqual(service.sessions[session_id].machine.state.mode, "panic")
        self.assertTrue(
            service.push_player_input(
                "ドギドを県に持ち替えてください",
                source="voice",
            )["accepted"]
        )

        result = service.process_event(make_panic_event(sequence=2), session_id)

        self.assertEqual(len(result.response.commands), 1)
        self.assertIsNone(service.sessions[session_id].pending_player_text)

    def test_mismatched_success_result_is_failed_and_does_not_claim_success(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        issued = service.process_event(make_event(sequence=1, user_text="剣"), session_id)
        command = issued.response.commands[0]
        result = service.process_event(
            make_event(
                sequence=2,
                command_results=[
                    {
                        "command_id": command.command_id,
                        "command_type": "select_hotbar",
                        "status": "succeeded",
                        "executed_at": (
                            command.issued_at + timedelta(milliseconds=500)
                        ).isoformat(),
                        "selected_slot": 0,
                        "selected_item_id": "minecraft:diamond_sword",
                        "detail_code": "selected",
                    }
                ],
            ),
            session_id,
        )
        self.assertTrue(any("手元が変わった" in (action.text or "") for action in result.actions))

    def test_success_report_outside_command_window_is_failed(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        issued = service.process_event(make_event(sequence=1, user_text="剣"), session_id)
        command = issued.response.commands[0]

        result = service.process_event(
            make_event(
                sequence=2,
                command_results=[
                    {
                        "command_id": command.command_id,
                        "command_type": "select_hotbar",
                        "status": "succeeded",
                        "executed_at": command.expires_at.isoformat(),
                        "selected_slot": command.slot,
                        "selected_item_id": command.expected_item_id,
                        "detail_code": "selected",
                    }
                ],
            ),
            session_id,
        )

        self.assertTrue(any("うまく持ち替え" in (action.text or "") for action in result.actions))

    def test_server_expires_unanswered_command_and_stops_redelivery(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        session_id = create_session(service)
        issued = service.process_event(make_event(sequence=1, user_text="剣"), session_id)
        command = issued.response.commands[0]
        session = service.sessions[session_id]
        session.pending_commands[command.command_id] = command.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )

        expired = service.process_event(make_event(sequence=2), session_id)

        self.assertEqual(expired.response.commands, [])
        self.assertTrue(any("間に合わへんかった" in (action.text or "") for action in expired.actions))

    def test_qwen_closed_extraction_can_request_but_conversation_does_not(self) -> None:
        class FakeQwen:
            def route_enabled(self, route: str) -> bool:
                return route == "chat"

            def generate_structured_json(self, request: object) -> dict[str, object]:
                return {
                    "intent": "select_weapon",
                    "weapon_kind": "sword",
                    "is_request": True,
                    "evidence": "剣の方がよくない？",
                    "confidence": 0.95,
                }

            def generate_leaf_text(self, _request: object) -> str:
                raise AssertionError("assist request must not become ordinary player_chat")

        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False, memory_enabled=False))
        service.llm = FakeQwen()  # type: ignore[assignment]
        session_id = create_session(service)
        result = service.process_event(
            make_event(sequence=1, user_text="そろそろ剣の方がよくない？"),
            session_id,
        )
        self.assertEqual(len(result.response.commands), 1)

    def test_episode_correlates_command_issue_and_real_adapter_result(self) -> None:
        with TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=Path(tmp),
                )
            )
            session_id = create_session(service)
            issued = service.process_event(make_event(sequence=1, user_text="剣"), session_id)
            command = issued.response.commands[0]
            service.process_event(
                make_event(
                    sequence=2,
                    command_results=[
                        AdapterCommandResult(
                            command_id=command.command_id,
                            command_type="select_hotbar",
                            status="succeeded",
                            executed_at=command.issued_at + timedelta(milliseconds=500),
                            selected_slot=command.slot,
                            selected_item_id=command.expected_item_id,
                            detail_code="selected",
                        ).model_dump(mode="json")
                    ],
                ),
                session_id,
            )
            rows = [
                json.loads(line)
                for line in (Path(tmp) / "eval" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                rows[0]["action"]["adapter_commands"][0]["command_id"],
                command.command_id,
            )
            self.assertEqual(
                rows[1]["result"]["adapter_command_results"][0]["command_id"],
                command.command_id,
            )
            self.assertEqual(rows[1]["result"]["scope"], "adapter_execution_observed")


if __name__ == "__main__":
    unittest.main()
