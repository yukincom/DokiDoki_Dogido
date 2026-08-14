"""戦闘割り込み中の川柳workshop保持・再開。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
from dogido_server.haiku.combat_pause import finalize_combat_workshop_input_payload
from dogido_server.haiku.workshop import (
    is_active,
    is_open,
    maybe_close_for_time,
    open_from_emission,
    pause_workshop_for_combat,
    resume_workshop_after_combat,
    workshop_prompt_details,
)
from dogido_server.memory_types import HaikuEmission
from dogido_server.models import (
    AdapterSessionCreateRequest,
    Certainty,
    CombatState,
    EventDescriptor,
    EventName,
    GameEvent,
    MetaState,
    PlayerState,
    PriorityHint,
    SourceKind,
    VisualThreat,
    WorldState,
)
from dogido_server.player_input.routing import route_player_input
from dogido_server.service import DogidoService
from dogido_server.state_machine import AudioAction


BASE = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
VERSE = "ぽぴーや\nひるのそらは\nくさちのね"


def _emission() -> HaikuEmission:
    return HaikuEmission(
        created_at=BASE,
        text=VERSE,
        preface="ここで一句。",
        interpretation="昼の草地とポピー",
        biome="meadow",
        structure=None,
        time_phase="day",
        dimension="minecraft:overworld",
        event_sequence=1,
        route="haiku",
    )


def _event(
    seconds: int,
    *,
    name: EventName = EventName.STATUS_SNAPSHOT,
    text: str | None = None,
    threats: list[VisualThreat] | None = None,
    combat: CombatState | None = None,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        observed_at=BASE + timedelta(seconds=seconds),
        sequence=seconds + 10,
        event=EventDescriptor(
            name=name,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.URGENT if threats else PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(name="p", dimension="minecraft:overworld", health=20),
        world=WorldState(biome="meadow", local_light=15, sky_visible=True),
        visual_threats=list(threats or []),
        combat=combat or CombatState(),
        meta=MetaState(user_text=text),
    )


def _zombie(*, approaching: bool = False, distance: float = 5.0) -> VisualThreat:
    return VisualThreat(
        type="zombie",
        entity_id="z-1",
        distance=distance,
        approaching=approaching,
    )


class WorkshopCombatLifecycleTests(unittest.TestCase):
    def test_pause_preserves_verse_and_pending_but_clears_ephemeral_state(self) -> None:
        workshop = open_from_emission(_emission())
        workshop.pending_revision = "ぽぴーや\nひるのそらは\nくさちかな"
        workshop.pending_revision_base_text = VERSE
        workshop.marked_line_index = 2
        workshop.last_findings = [{"line_index": 2, "fragment": "くさちのね"}]
        workshop.awaiting_meaning_ack = True
        workshop.awaiting_close_confirmation = True

        self.assertTrue(
            pause_workshop_for_combat(
                workshop,
                now=BASE + timedelta(seconds=5),
                hostile_types=["zombie"],
            )
        )
        self.assertTrue(is_open(workshop))
        self.assertFalse(is_active(workshop))
        self.assertEqual(workshop.editing_line(), "ぽぴーや\nひるのそらは\nくさちかな")
        self.assertIsNone(workshop.marked_line_index)
        self.assertEqual(workshop.last_findings, [])
        self.assertFalse(workshop.awaiting_meaning_ack)
        self.assertFalse(workshop.awaiting_close_confirmation)
        self.assertEqual(workshop_prompt_details(workshop)["haiku_workshop_open"], "")

        # 戦闘時間はopen/idle timeoutへ数えない。
        maybe_close_for_time(workshop, now=BASE + timedelta(minutes=20))
        self.assertTrue(is_open(workshop))
        self.assertTrue(workshop.combat_paused)

        self.assertTrue(
            resume_workshop_after_combat(
                workshop,
                now=BASE + timedelta(minutes=20),
                reason="escaped",
                ask_confirmation=True,
            )
        )
        self.assertTrue(is_active(workshop))
        self.assertTrue(workshop.awaiting_combat_resume_confirmation)
        self.assertEqual(workshop.last_workshop_at, BASE + timedelta(minutes=20))


class WorkshopCombatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DogidoService(
            Settings(
                llm_enabled=False,
                audio_enabled=False,
                memory_enabled=False,
                platform_ai_provider="chat",
                workshop_low_threat_resume_delay_ms=8000,
            )
        )
        response = self.service.create_session(
            AdapterSessionCreateRequest(
                schema_version="2026-05-24",
                adapter_name="test",
                adapter_version="0",
                game="minecraft",
                player_name="p",
                capabilities=[],
            )
        )
        self.session = self.service.sessions[response.session_id]
        self.service._open_haiku_workshop(
            self.session,
            _emission(),
            entry_id="h_combat",
            now=BASE,
        )

    def test_process_event_pauses_without_losing_pending_revision(self) -> None:
        assert self.session.haiku_workshop is not None
        self.session.haiku_workshop.pending_revision = "ぽぴーや\nひるのそらは\nくさちかな"
        self.session.haiku_workshop.pending_revision_base_text = VERSE

        self.service.process_event(
            _event(
                5,
                threats=[_zombie(approaching=True)],
                combat=CombatState(combat_active_hint=True, hostiles_within_7=1),
            ),
            session_id=self.session.session_id,
        )

        workshop = self.session.haiku_workshop
        self.assertIsNotNone(workshop)
        assert workshop is not None
        self.assertTrue(workshop.combat_paused)
        self.assertEqual(workshop.editing_line(), "ぽぴーや\nひるのそらは\nくさちかな")
        self.assertFalse(self.session.machine._haiku_workshop_is_open())

    def test_victory_waits_for_combat_speech_then_restores_verse(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=2), hostile_types=["zombie"])

        ended = _event(
            8,
            name=EventName.COMBAT_ENDED,
            combat=CombatState(nearby_experience_orb_count=2, combat_active_hint=True),
        )
        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            ended,
            [AudioAction(layer="speech", interrupt=True, text="やった、終わったで！")],
            state_mode="aftermath",
        )
        self.assertEqual(added, [])
        self.assertFalse(consumed)
        self.assertFalse(replaced)
        self.assertTrue(workshop.combat_paused)
        self.assertEqual(workshop.combat_resume_pending_reason, "victory")

        quiet = _event(18)
        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            quiet,
            [],
            state_mode="normal",
        )
        self.assertFalse(consumed)
        self.assertFalse(replaced)
        self.assertTrue(is_active(workshop))
        self.assertTrue(workshop.awaiting_combat_resume_confirmation)
        self.assertIn("倒せたみたいや", added[0].text or "")
        self.assertIn(VERSE, added[0].text or "")

    def test_combat_end_without_victory_evidence_uses_disengaged_wording(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=2), hostile_types=["zombie"])
        ended = _event(8, name=EventName.COMBAT_ENDED)

        added, _, _ = self.service._update_workshop_combat_state(
            self.session,
            ended,
            [],
            state_mode="normal",
        )

        self.assertTrue(is_active(workshop))
        self.assertIn("離れられたみたいや", added[0].text or "")

    def test_single_quiet_frame_does_not_resume_without_combat_clear_delay(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(
            workshop,
            now=BASE + timedelta(seconds=2),
            hostile_types=["zombie"],
        )

        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            _event(3),
            [],
            state_mode="normal",
        )

        self.assertEqual(added, [])
        self.assertFalse(consumed)
        self.assertFalse(replaced)
        self.assertTrue(workshop.combat_paused)

    def test_unrelated_player_input_defers_resume_prompt_to_next_quiet_frame(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(
            workshop,
            now=BASE + timedelta(seconds=2),
            hostile_types=["zombie"],
        )
        self.session.machine.player_input = route_player_input("松明ある？")

        added, _, _ = self.service._update_workshop_combat_state(
            self.session,
            _event(8, text="松明ある？"),
            [],
            state_mode="normal",
        )
        self.assertEqual(added, [])
        self.assertTrue(workshop.combat_paused)

        self.session.machine.player_input = route_player_input(None)
        added, _, _ = self.service._update_workshop_combat_state(
            self.session,
            _event(9),
            [],
            state_mode="normal",
        )
        self.assertTrue(is_active(workshop))
        self.assertIn("続ける？", added[0].text or "")

    def test_stable_single_enemy_can_be_ignored_but_approach_repauses(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=1), hostile_types=["zombie"])
        self.session.machine.state.stalled_visual_started_at = BASE + timedelta(seconds=1)
        self.session.machine.player_input = route_player_input("句を続けよう")
        stable = _event(
            10,
            text="句を続けよう",
            threats=[_zombie(approaching=False)],
            combat=CombatState(combat_active_hint=True, hostiles_within_7=1, hostiles_within_10=1),
        )

        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            stable,
            [],
            state_mode="panic",
        )
        self.assertTrue(consumed)
        self.assertTrue(replaced)
        self.assertTrue(is_active(workshop))
        self.assertIsNotNone(workshop.combat_override_signature)
        self.assertIn("今は近づいてきてへん", added[0].text or "")

        self.session.machine.player_input = route_player_input(None)
        approaching = _event(
            11,
            threats=[_zombie(approaching=True)],
            combat=CombatState(combat_active_hint=True, recent_damage_ms=100, hostiles_within_7=1),
        )
        self.service._update_workshop_combat_state(
            self.session,
            approaching,
            [],
            state_mode="panic",
        )
        self.assertTrue(workshop.combat_paused)
        self.assertIsNone(workshop.combat_override_signature)

    def test_queued_resume_is_attached_even_while_machine_is_in_panic(self) -> None:
        first = _event(
            1,
            threats=[_zombie(approaching=False)],
            combat=CombatState(
                combat_active_hint=True,
                hostiles_within_7=1,
                hostiles_within_10=1,
            ),
        )
        self.service.process_event(first, session_id=self.session.session_id)
        workshop = self.session.haiku_workshop
        assert workshop is not None
        self.assertTrue(workshop.combat_paused)
        self.assertIn(self.session.machine.state.mode, {"panic", "suppressed_panic"})

        pushed = self.service.push_player_input("句を続けよう")
        self.assertTrue(pushed.get("accepted"))
        resumed = self.service.process_event(
            _event(
                10,
                threats=[_zombie(approaching=False)],
                combat=CombatState(
                    combat_active_hint=True,
                    hostiles_within_7=1,
                    hostiles_within_10=1,
                ),
            ),
            session_id=self.session.session_id,
        )

        self.assertIsNone(self.session.pending_player_text)
        self.assertTrue(is_active(workshop))
        speeches = [action.text or "" for action in resumed.actions if action.layer == "speech"]
        self.assertTrue(any("句は戻す" in text for text in speeches))

    def test_semantic_resume_is_analyzed_before_panic_hold(self) -> None:
        self.service.process_event(
            _event(
                1,
                threats=[_zombie(approaching=False)],
                combat=CombatState(
                    combat_active_hint=True,
                    hostiles_within_7=1,
                    hostiles_within_10=1,
                ),
            ),
            session_id=self.session.session_id,
        )
        workshop = self.session.haiku_workshop
        assert workshop is not None
        player_text = "危なくなさそうだし、さっき話してたやつに戻ろうか"
        calls = 0

        def generate(request, *, fallback):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return {
                "action": "resume_workshop",
                "confidence": 0.93,
                "evidence": "さっき話してたやつに戻ろうか",
                "__dogido_platform_ai_provider": "apple_foundation_models",
            }

        self.service.platform_ai.generate_structured_json = generate  # type: ignore[method-assign]
        self.service.push_player_input(player_text, source="voice")
        result = self.service.process_event(
            _event(
                10,
                threats=[_zombie(approaching=False)],
                combat=CombatState(
                    combat_active_hint=True,
                    hostiles_within_7=1,
                    hostiles_within_10=1,
                ),
            ),
            session_id=self.session.session_id,
        )

        self.assertEqual(calls, 1)
        self.assertIsNone(self.session.pending_player_text)
        self.assertTrue(is_active(workshop))
        speeches = [action.text or "" for action in result.actions if action.layer == "speech"]
        self.assertTrue(any("句は戻す" in text for text in speeches))

    def test_pending_unrelated_input_is_cached_and_defers_safe_resume(self) -> None:
        self.service.process_event(
            _event(
                1,
                threats=[_zombie(approaching=False)],
                combat=CombatState(combat_active_hint=True, hostiles_within_7=1),
            ),
            session_id=self.session.session_id,
        )
        workshop = self.session.haiku_workshop
        assert workshop is not None
        player_text = "松明はまだあるかな"
        calls = 0

        def generate(request, *, fallback):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return {
                "action": "unrelated",
                "confidence": 0.96,
                "evidence": player_text,
                "__dogido_platform_ai_provider": "apple_foundation_models",
            }

        self.service.platform_ai.generate_structured_json = generate  # type: ignore[method-assign]
        self.service.push_player_input(player_text, source="voice")

        first_safe = self.service.process_event(
            _event(8),
            session_id=self.session.session_id,
        )
        self.assertTrue(workshop.combat_paused)
        self.assertEqual(self.session.pending_player_text, player_text)
        self.assertFalse(any("続ける？" in (action.text or "") for action in first_safe.actions))

        second_safe = self.service.process_event(
            _event(9),
            session_id=self.session.session_id,
        )
        self.assertEqual(calls, 1)
        self.assertIsNone(self.session.pending_player_text)
        self.assertTrue(workshop.combat_paused)
        self.assertFalse(any("続ける？" in (action.text or "") for action in second_safe.actions))

        resumed = self.service.process_event(
            _event(10),
            session_id=self.session.session_id,
        )
        self.assertTrue(is_active(workshop))
        self.assertTrue(any("続ける？" in (action.text or "") for action in resumed.actions))

    def test_unstable_enemy_rejects_resume_and_keeps_workshop_paused(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=1), hostile_types=["zombie"])
        self.session.machine.state.stalled_visual_started_at = BASE + timedelta(seconds=1)
        self.session.machine.player_input = route_player_input("句を続けよう")
        danger = _event(
            3,
            text="句を続けよう",
            threats=[_zombie(approaching=True, distance=2.5)],
            combat=CombatState(combat_active_hint=True, recent_damage_ms=50, hostiles_within_7=1),
        )

        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            danger,
            [],
            state_mode="panic",
        )
        self.assertTrue(consumed)
        self.assertFalse(replaced)
        self.assertTrue(workshop.combat_paused)
        self.assertIn("句はしまっとく", added[0].text or "")

    def test_os_ai_semantic_resume_handles_wording_outside_rule_markers(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=1), hostile_types=["zombie"])
        self.session.machine.state.stalled_visual_started_at = BASE + timedelta(seconds=1)
        player_text = "危なくなさそうだし、さっき話してたやつに戻ろうか"
        self.session.machine.player_input = route_player_input(player_text)
        seen_kinds: list[str] = []

        def generate(request, *, fallback):  # type: ignore[no-untyped-def]
            seen_kinds.append(request.kind)
            return {
                "action": "resume_workshop",
                "confidence": 0.93,
                "evidence": "さっき話してたやつに戻ろうか",
                "__dogido_platform_ai_provider": "apple_foundation_models",
            }

        self.service.platform_ai.generate_structured_json = generate  # type: ignore[method-assign]
        stable = _event(
            10,
            text=player_text,
            threats=[_zombie(approaching=False)],
            combat=CombatState(combat_active_hint=True, hostiles_within_7=1, hostiles_within_10=1),
        )

        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            stable,
            [],
            state_mode="panic",
        )

        self.assertEqual(seen_kinds, ["haiku_workshop_combat_input"])
        self.assertTrue(is_active(workshop))
        self.assertTrue(consumed)
        self.assertTrue(replaced)
        self.assertIn("句は戻す", added[0].text or "")

    def test_os_ai_workshop_input_cannot_bypass_unstable_enemy(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=1), hostile_types=["zombie"])
        self.session.machine.state.stalled_visual_started_at = BASE + timedelta(seconds=1)
        player_text = "最後の言い方がちょっと固いね"
        self.session.machine.player_input = route_player_input(player_text)

        def generate(request, *, fallback):  # type: ignore[no-untyped-def]
            return {
                "action": "workshop_input",
                "confidence": 0.91,
                "evidence": player_text,
                "__dogido_platform_ai_provider": "apple_foundation_models",
            }

        self.service.platform_ai.generate_structured_json = generate  # type: ignore[method-assign]
        danger = _event(
            3,
            text=player_text,
            threats=[_zombie(approaching=True, distance=2.5)],
            combat=CombatState(combat_active_hint=True, recent_damage_ms=50, hostiles_within_7=1),
        )

        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            danger,
            [],
            state_mode="panic",
        )

        self.assertTrue(workshop.combat_paused)
        self.assertTrue(consumed)
        self.assertFalse(replaced)
        self.assertIn("句はしまっとく", added[0].text or "")

    def test_invalid_os_ai_evidence_does_not_resume(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        pause_workshop_for_combat(workshop, now=BASE + timedelta(seconds=1), hostile_types=["zombie"])
        self.session.machine.state.stalled_visual_started_at = BASE + timedelta(seconds=1)
        player_text = "危なくなさそうだね"
        self.session.machine.player_input = route_player_input(player_text)

        def generate(request, *, fallback):  # type: ignore[no-untyped-def]
            return {
                "action": "resume_workshop",
                "confidence": 0.99,
                "evidence": "句に戻ろう",
                "__dogido_platform_ai_provider": "apple_foundation_models",
            }

        self.service.platform_ai.generate_structured_json = generate  # type: ignore[method-assign]
        stable = _event(
            10,
            text=player_text,
            threats=[_zombie(approaching=False)],
            combat=CombatState(combat_active_hint=True, hostiles_within_7=1, hostiles_within_10=1),
        )

        added, consumed, replaced = self.service._update_workshop_combat_state(
            self.session,
            stable,
            [],
            state_mode="panic",
        )

        self.assertTrue(workshop.combat_paused)
        self.assertFalse(consumed)
        self.assertFalse(replaced)
        self.assertEqual(added, [])

    def test_resume_confirmation_can_continue_or_close(self) -> None:
        workshop = self.session.haiku_workshop
        assert workshop is not None
        workshop.awaiting_combat_resume_confirmation = True
        self.session.machine.player_input = route_player_input("うん、続けよう")
        actions = self.service._haiku_workshop_actions(self.session, _event(20, text="うん、続けよう"))
        self.assertIn("続けよか", actions[0].text or "")
        self.assertFalse(workshop.awaiting_combat_resume_confirmation)

        workshop.awaiting_combat_resume_confirmation = True
        self.session.machine.player_input = route_player_input("もうやめとく")
        actions = self.service._haiku_workshop_actions(self.session, _event(21, text="もうやめとく"))
        self.assertIn("ここまで", actions[0].text or "")
        self.assertIsNone(self.session.haiku_workshop)


class WorkshopCombatInputContractTests(unittest.TestCase):
    def test_requires_grounded_high_confidence_evidence(self) -> None:
        accepted = finalize_combat_workshop_input_payload(
            {
                "action": "workshop_input",
                "confidence": 0.92,
                "evidence": "最後の言い方が固い",
            },
            player_text="最後の言い方が固いと思う",
        )
        self.assertEqual(accepted.action, "workshop_input")

        invented = finalize_combat_workshop_input_payload(
            {
                "action": "resume_workshop",
                "confidence": 0.99,
                "evidence": "句に戻りたい",
            },
            player_text="敵はもう大丈夫そう",
        )
        self.assertEqual(invented.action, "uncertain")

        low_confidence = finalize_combat_workshop_input_payload(
            {
                "action": "resume_workshop",
                "confidence": 0.55,
                "evidence": "さっきの相談に戻ろう",
            },
            player_text="さっきの相談に戻ろう",
        )
        self.assertEqual(low_confidence.action, "uncertain")


if __name__ == "__main__":
    unittest.main()
