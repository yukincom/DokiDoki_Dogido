from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dogido_server.audio import AudioDispatcher, RunningAudio
from dogido_server.config import Settings
from dogido_server.llm import DogidoLLM
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.models import (
    AuditoryThreat,
    Certainty,
    CombatState,
    Direction,
    DistanceBand,
    EventDescriptor,
    EventName,
    GameEvent,
    HorizontalDirection,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    TimePhase,
    VisualThreat,
    WorldState,
)
from dogido_server.service import DogidoService
from dogido_server.state_machine import AudioAction, DogidoStateMachine


BASE = datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc)


class CaptureLLM(DogidoLLM):
    def __init__(self, response: str = "あー怖かったぁ。もう安心やな、お疲れさん。") -> None:
        super().__init__(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))
        self.requests: list[LeafGenerationRequest] = []
        self.response = response

    def generate_leaf_text(self, request):  # type: ignore[override]
        self.requests.append(request)
        return self.response


def make_event(
    *,
    sequence: int,
    at_sec: float,
    event_name: EventName,
    visual_threats: list[VisualThreat] | None = None,
    auditory_threats: list[AuditoryThreat] | None = None,
    hostiles_within_7: int = 0,
    hostiles_within_10: int = 0,
    hostiles_within_30_ground: int = 0,
    combat_active_hint: bool = False,
    hostile_outcomes: list[dict[str, str]] | None = None,
    user_text: str | None = None,
) -> GameEvent:
    source_kind = (
        SourceKind.AUDITORY
        if event_name == EventName.HOSTILE_AUDIO_DETECTED
        else SourceKind.SYSTEM
        if event_name == EventName.COMBAT_ENDED
        else SourceKind.VISUAL
    )
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=BASE + timedelta(seconds=at_sec),
        sequence=sequence,
        event=EventDescriptor(
            name=event_name,
            source_kind=source_kind,
            priority_hint=PriorityHint.URGENT,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="player",
            position=Position(x=0.0, y=64.0, z=0.0),
            dimension="minecraft:overworld",
            health=16.0,
        ),
        world=WorldState(
            time_phase=TimePhase.DAY,
            biome="plains",
            local_light=15,
            sky_visible=True,
            danger_darkness_score=0.0,
        ),
        visual_threats=list(visual_threats or []),
        auditory_threats=list(auditory_threats or []),
        combat=CombatState(
            recent_hostile_visual_ms=0 if visual_threats else 6000,
            recent_hostile_audio_ms=0 if auditory_threats else 6000,
            recent_damage_ms=6000,
            hostiles_within_7=hostiles_within_7,
            hostiles_within_10=hostiles_within_10,
            hostiles_within_30_ground=hostiles_within_30_ground,
            combat_active_hint=combat_active_hint,
            hostile_outcomes=hostile_outcomes,
        ),
        meta=MetaState(user_text=user_text),
    )


def spider_threat() -> VisualThreat:
    return VisualThreat(
        type="spider",
        entity_id="spider-1",
        distance=5.0,
        direction=Direction(horizontal=HorizontalDirection.FRONT),
        approaching=True,
        certainty=Certainty.HIGH,
    )


def spider_audio() -> AuditoryThreat:
    return AuditoryThreat(
        label="spider",
        source_id="spider-audio-1",
        sound_event="entity.spider.ambient",
        direction=Direction(horizontal=HorizontalDirection.FRONT),
        distance_band=DistanceBand.CLOSE,
        certainty=Certainty.MEDIUM,
        spoken_name_allowed=True,
    )


class CombatAftermathStateMachineTests(unittest.TestCase):
    def test_creeper_fuse_start_is_called_out_once_until_rearmed(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy):
                machine = DogidoStateMachine(
                    Settings(
                        audio_enabled=False,
                        llm_enabled=False,
                        decision_policy=policy,
                    )
                )

                def creeper(*, fuse_active: bool) -> VisualThreat:
                    return VisualThreat(
                        type="creeper",
                        entity_id="creeper-1",
                        distance=3.5,
                        direction=Direction(horizontal=HorizontalDirection.FRONT),
                        approaching=True,
                        fuse_active=fuse_active,
                        certainty=Certainty.HIGH,
                    )

                machine.process(
                    make_event(
                        sequence=1,
                        at_sec=0,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[creeper(fuse_active=False)],
                        combat_active_hint=True,
                    )
                )
                started = machine.process(
                    make_event(
                        sequence=2,
                        at_sec=0.5,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[creeper(fuse_active=True)],
                        combat_active_hint=True,
                    )
                )
                held = machine.process(
                    make_event(
                        sequence=3,
                        at_sec=1.0,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[creeper(fuse_active=True)],
                        combat_active_hint=True,
                    )
                )
                machine.process(
                    make_event(
                        sequence=4,
                        at_sec=1.5,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[creeper(fuse_active=False)],
                        combat_active_hint=True,
                    )
                )
                restarted = machine.process(
                    make_event(
                        sequence=5,
                        at_sec=2.0,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[creeper(fuse_active=True)],
                        combat_active_hint=True,
                    )
                )

                self.assertTrue(
                    any("膨らんどる" in (action.text or "") for action in started.actions)
                )
                self.assertFalse(
                    any("膨らんどる" in (action.text or "") for action in held.actions)
                )
                self.assertTrue(
                    any("膨らんどる" in (action.text or "") for action in restarted.actions)
                )

    def test_charged_creeper_count_keeps_its_distinct_name(self) -> None:
        threats = [
            VisualThreat(
                type="charged_creeper",
                entity_id=f"charged-creeper-{index}",
                distance=6.0 + index,
                direction=Direction(horizontal=HorizontalDirection.FRONT),
                certainty=Certainty.HIGH,
            )
            for index in range(2)
        ]
        machine = DogidoStateMachine(Settings(audio_enabled=False, llm_enabled=False))
        result = machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=threats,
                hostiles_within_7=2,
                hostiles_within_10=2,
                combat_active_hint=True,
            )
        )

        self.assertTrue(
            any("帯電クリーパー2体" in (action.text or "") for action in result.actions)
        )

    def test_creeper_detonation_event_gets_an_immediate_distinct_reaction(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy):
                machine = DogidoStateMachine(
                    Settings(
                        audio_enabled=False,
                        llm_enabled=False,
                        decision_policy=policy,
                    )
                )
                result = machine.process(
                    make_event(
                        sequence=1,
                        at_sec=0,
                        event_name=EventName.CREEPER_DETONATED,
                        hostile_outcomes=[
                            {
                                "type": "charged_creeper",
                                "outcome": "creeper_detonation",
                                "evidence": "explosion_packet",
                            }
                        ],
                    )
                )

                self.assertEqual(len(result.actions), 1)
                self.assertEqual(
                    result.actions[0].cue_id,
                    "creeper_detonation_reaction",
                )
                self.assertTrue(result.actions[0].interrupt)
                self.assertIn("帯電クリーパー", result.actions[0].text or "")
                self.assertIn("爆発した", result.actions[0].text or "")

    def test_hostile_defeat_event_reacts_now_and_is_not_repeated_at_combat_end(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy):
                llm = CaptureLLM()
                machine = DogidoStateMachine(
                    Settings(audio_enabled=False, decision_policy=policy),
                    llm=llm,
                )
                machine.process(
                    make_event(
                        sequence=1,
                        at_sec=0,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[spider_threat()],
                        combat_active_hint=True,
                    )
                )
                outcome = {
                    "entity_id": "spider-1",
                    "type": "spider",
                    "outcome": "player_kill",
                    "evidence": "server_death_event",
                }
                defeated = machine.process(
                    make_event(
                        sequence=2,
                        at_sec=0.5,
                        event_name=EventName.HOSTILE_DEFEATED,
                        combat_active_hint=True,
                        hostile_outcomes=[outcome],
                    )
                )
                duplicate = machine.process(
                    make_event(
                        sequence=3,
                        at_sec=1,
                        event_name=EventName.HOSTILE_DEFEATED,
                        combat_active_hint=True,
                        hostile_outcomes=[outcome],
                    )
                )
                machine.process(
                    make_event(
                        sequence=4,
                        at_sec=6,
                        event_name=EventName.COMBAT_ENDED,
                        hostile_outcomes=[outcome],
                    )
                )

                self.assertEqual(len(defeated.actions), 1)
                self.assertEqual(
                    defeated.actions[0].cue_id,
                    "hostile_defeated_reaction",
                )
                self.assertIn("スパイダー倒したで", defeated.actions[0].text or "")
                self.assertEqual(duplicate.actions, [])
                aftermath = next(
                    request for request in llm.requests if request.kind == "aftermath"
                )
                self.assertEqual(aftermath.details["combat_outcome"], "disengaged")
                self.assertEqual(aftermath.details["hostiles"], [])

    def test_explosion_death_event_does_not_credit_the_player(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, llm_enabled=False))
        result = machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.HOSTILE_DEFEATED,
                hostile_outcomes=[
                    {
                        "entity_id": "zombie-1",
                        "type": "zombie",
                        "outcome": "explosion_death",
                        "evidence": "server_death_event",
                    }
                ],
            )
        )

        self.assertEqual(result.actions[0].cue_id, "hostile_defeated_reaction")
        self.assertIn("爆発でゾンビ倒れた", result.actions[0].text or "")
        self.assertNotIn("ようやった", result.actions[0].text or "")

    def test_confirmed_combat_end_interrupts_stale_audio_and_enters_aftermath(self) -> None:
        for policy in ("py_trees", "legacy"):
            with self.subTest(policy=policy):
                machine = DogidoStateMachine(
                    Settings(audio_enabled=False, llm_enabled=False, decision_policy=policy)
                )
                machine.process(
                    make_event(
                        sequence=1,
                        at_sec=0,
                        event_name=EventName.THREAT_APPROACHING,
                        visual_threats=[spider_threat()],
                        hostiles_within_7=1,
                        hostiles_within_10=1,
                        hostiles_within_30_ground=1,
                        combat_active_hint=True,
                    )
                )

                result = machine.process(
                    make_event(
                        sequence=2,
                        at_sec=6,
                        event_name=EventName.COMBAT_ENDED,
                        # Fabric の送信順により、このヒントだけ true のまま届き得る。
                        combat_active_hint=True,
                    )
                )

                relief = next(
                    action for action in result.actions if action.cue_id == "aftermath_relief"
                )
                self.assertEqual(result.state.mode, "aftermath")
                self.assertFalse(result.combat_active)
                self.assertEqual(relief.layer, "speech")
                self.assertTrue(relief.interrupt)
                self.assertEqual(
                    machine.state.pending_dialogue_notes,
                    ["スパイダーを倒した"],
                )

    def test_explicit_combat_end_is_not_suppressed_by_recent_player_input(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, llm_enabled=False))
        result = machine.process(
            make_event(
                sequence=1,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                user_text="もう大丈夫？",
            )
        )

        self.assertTrue(any(action.cue_id == "aftermath_relief" for action in result.actions))

    def test_combat_end_with_remaining_enemy_does_not_emit_relief(self) -> None:
        cases = {
            "count": {
                "hostiles_within_7": 1,
                "hostiles_within_10": 1,
                "hostiles_within_30_ground": 1,
            },
            "visual": {"visual_threats": [spider_threat()]},
            "audio": {"auditory_threats": [spider_audio()]},
        }
        for case, overrides in cases.items():
            with self.subTest(case=case):
                machine = DogidoStateMachine(Settings(audio_enabled=False, llm_enabled=False))
                result = machine.process(
                    make_event(
                        sequence=1,
                        at_sec=6,
                        event_name=EventName.COMBAT_ENDED,
                        **overrides,  # type: ignore[arg-type]
                    )
                )

                self.assertNotEqual(result.state.mode, "aftermath")
                self.assertFalse(
                    any(action.cue_id == "aftermath_relief" for action in result.actions)
                )

    def test_llm_receives_confirmed_clear_and_recent_audio_only_enemy_name(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.HOSTILE_AUDIO_DETECTED,
                auditory_threats=[spider_audio()],
                combat_active_hint=True,
            )
        )

        machine.process(
            make_event(sequence=2, at_sec=6, event_name=EventName.COMBAT_ENDED)
        )

        request = next(request for request in llm.requests if request.kind == "aftermath")
        self.assertEqual(request.details["hostiles"], ["スパイダー"])
        self.assertTrue(request.details["hostile_clear_confirmed"])
        self.assertEqual(request.details["remaining_hostiles"], 0)

    def test_new_adapter_disengagement_does_not_become_a_kill(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[spider_threat()],
                combat_active_hint=True,
            )
        )

        machine.process(
            make_event(
                sequence=2,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[],
            )
        )

        request = next(request for request in llm.requests if request.kind == "aftermath")
        self.assertEqual(request.details["combat_outcome"], "disengaged")
        self.assertEqual(machine.state.pending_dialogue_notes, ["スパイダーと交戦した"])

    def test_previous_dead_hostile_is_not_reused_for_a_later_disengagement(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        creeper = VisualThreat(
            type="creeper",
            entity_id="creeper-1",
            distance=4.0,
            direction=Direction(horizontal=HorizontalDirection.FRONT),
            certainty=Certainty.HIGH,
        )
        enderman = VisualThreat(
            type="enderman",
            entity_id="enderman-1",
            distance=7.0,
            direction=Direction(horizontal=HorizontalDirection.FRONT),
            certainty=Certainty.HIGH,
        )

        machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[creeper],
                combat_active_hint=True,
            )
        )
        outcome = {
            "entity_id": "creeper-1",
            "type": "creeper",
            "outcome": "other_death",
            "evidence": "server_death_event",
        }
        defeated = machine.process(
            make_event(
                sequence=2,
                at_sec=0.5,
                event_name=EventName.HOSTILE_DEFEATED,
                combat_active_hint=True,
                hostile_outcomes=[outcome],
            )
        )
        self.assertFalse(
            any(memo.mob_type == "creeper" for memo in machine.state.recent_visual_memos)
        )
        machine.process(
            make_event(
                sequence=3,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[outcome],
            )
        )
        machine.process(
            make_event(
                sequence=4,
                at_sec=7,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[enderman],
                combat_active_hint=True,
            )
        )
        machine.process(
            make_event(
                sequence=5,
                at_sec=13,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[],
            )
        )

        aftermath_requests = [
            request for request in llm.requests if request.kind == "aftermath"
        ]
        self.assertIn("クリーパー倒れた", defeated.actions[0].text or "")
        self.assertEqual(aftermath_requests[0].details["hostiles"], [])
        self.assertEqual(aftermath_requests[1].details["hostiles"], ["エンダーマン"])
        self.assertNotIn("クリーパー", aftermath_requests[1].details["hostiles"])

    def test_confirmed_player_kill_gets_a_distinct_outcome(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[spider_threat()],
                combat_active_hint=True,
            )
        )

        machine.process(
            make_event(
                sequence=2,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[
                    {
                        "type": "spider",
                        "outcome": "player_kill",
                        "evidence": "server_death_event",
                    }
                ],
            )
        )

        request = next(request for request in llm.requests if request.kind == "aftermath")
        self.assertEqual(request.details["combat_outcome"], "player_kill")
        self.assertEqual(request.details["hostiles"], ["スパイダー"])
        self.assertEqual(machine.state.pending_dialogue_notes, ["スパイダーを倒した"])

    def test_observed_death_without_player_credit_is_not_called_a_player_kill(self) -> None:
        llm = CaptureLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        machine.process(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[spider_threat()],
                combat_active_hint=True,
            )
        )

        machine.process(
            make_event(
                sequence=2,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[
                    {
                        "type": "spider",
                        "outcome": "other_death",
                        "evidence": "server_death_event",
                    }
                ],
            )
        )

        request = next(request for request in llm.requests if request.kind == "aftermath")
        self.assertEqual(request.details["combat_outcome"], "hostile_defeated")
        self.assertEqual(machine.state.pending_dialogue_notes, ["スパイダーが倒れた"])

    def test_disengagement_rejects_an_llm_kill_claim(self) -> None:
        llm = CaptureLLM("スパイダー、ちゃんと倒せたね。お疲れさんやで。")
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)

        result = machine.process(
            make_event(
                sequence=1,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[],
            )
        )

        relief = next(action for action in result.actions if action.cue_id == "aftermath_relief")
        self.assertEqual(
            relief.text,
            "あー……怖かったぁ。ひとまず気配は遠のいたみたいやな。",
        )

    def test_unattributed_death_rejects_player_credit_from_llm(self) -> None:
        llm = CaptureLLM("よう倒したな！ほんまお疲れさんやで。")
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)

        result = machine.process(
            make_event(
                sequence=1,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[
                    {
                        "type": "spider",
                        "outcome": "other_death",
                        "evidence": "server_death_event",
                    }
                ],
            )
        )

        relief = next(action for action in result.actions if action.cue_id == "aftermath_relief")
        self.assertEqual(relief.text, "敵は倒れたみたいやな。あー……怖かったわ。")

    def test_confirmed_player_kill_keeps_matching_llm_line(self) -> None:
        line = "よっしゃ、スパイダー倒せたな！お疲れさんやで。"
        llm = CaptureLLM(line)
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)

        result = machine.process(
            make_event(
                sequence=1,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[
                    {
                        "type": "spider",
                        "outcome": "player_kill",
                        "evidence": "server_death_event",
                    }
                ],
            )
        )

        relief = next(action for action in result.actions if action.cue_id == "aftermath_relief")
        self.assertEqual(relief.text, line)

    def test_charged_creeper_detonation_gets_a_distinct_outcome(self) -> None:
        llm = CaptureLLM("うわあっ、帯電クリーパー爆発したで！びっくりしたぁ。")
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)

        result = machine.process(
            make_event(
                sequence=1,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[
                    {
                        "type": "charged_creeper",
                        "outcome": "creeper_detonation",
                        "evidence": "explosion_packet",
                    }
                ],
            )
        )

        request = next(request for request in llm.requests if request.kind == "aftermath")
        self.assertEqual(request.details["combat_outcome"], "charged_creeper_detonated")
        self.assertEqual(
            machine.state.pending_dialogue_notes,
            ["帯電クリーパーが爆発した"],
        )
        relief = next(action for action in result.actions if action.cue_id == "aftermath_relief")
        self.assertEqual(relief.text, llm.response)

    def test_explosion_death_does_not_become_a_player_kill(self) -> None:
        llm = CaptureLLM("よう倒したな！ほんまお疲れさんやで。")
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)

        result = machine.process(
            make_event(
                sequence=1,
                at_sec=6,
                event_name=EventName.COMBAT_ENDED,
                hostile_outcomes=[
                    {
                        "type": "zombie",
                        "outcome": "explosion_death",
                        "evidence": "server_death_event",
                    }
                ],
            )
        )

        request = next(request for request in llm.requests if request.kind == "aftermath")
        self.assertEqual(request.details["combat_outcome"], "explosion_death")
        relief = next(action for action in result.actions if action.cue_id == "aftermath_relief")
        self.assertEqual(
            relief.text,
            "うわっ、爆発で敵が倒れたみたいやな。びっくりしたぁ。",
        )

    def test_prompt_for_confirmed_clear_forbids_residual_enemy_hedging(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="aftermath",
                fallback_text="fallback",
                details={
                    "player_name": "プレイヤー",
                    "hostiles": ["スパイダー"],
                    "health_state": "少し減ってる",
                    "hostile_clear_confirmed": True,
                    "remaining_hostiles": 0,
                    "combat_outcome": "disengaged",
                },
            )
        )
        prompt = messages[-1]["content"]

        self.assertIn("残っている敵は0体", prompt)
        self.assertNotIn("敵の排除完了", prompt)
        self.assertIn("残敵を疑わない", prompt)
        self.assertIn("敵の死亡は確認していない", prompt)
        self.assertIn("『倒した』『倒せた』『退治した』『敵を排除した』とは言わない", prompt)
        self.assertIn("プレイヤーを労う", prompt)

    def test_prompt_allows_kill_wording_only_with_player_kill_outcome(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="aftermath",
                fallback_text="fallback",
                details={
                    "player_name": "プレイヤー",
                    "hostiles": ["スパイダー"],
                    "health_state": "少し減ってる",
                    "hostile_clear_confirmed": True,
                    "remaining_hostiles": 0,
                    "combat_outcome": "player_kill",
                },
            )
        )
        prompt = messages[-1]["content"]

        self.assertIn("プレイヤーによる撃破根拠を確認済み", prompt)
        self.assertIn("『倒した』『倒せた』と言ってよい", prompt)


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.killed = True


class CombatAftermathAudioTests(unittest.TestCase):
    def test_interrupting_relief_drops_old_queue_and_stops_current_audio(self) -> None:
        with patch("dogido_server.audio.threading.Thread.start"):
            dispatcher = AudioDispatcher(
                Settings(audio_enabled=False, tts_backend="noop", cue_backend="noop")
            )
        stale = AudioAction(layer="callout", interrupt=False, text="まだスパイダーおるで！")
        dispatcher._pending.append((0, [stale]))
        process = _FakeProcess()
        dispatcher._current = RunningAudio(process=process)  # type: ignore[arg-type]

        relief = AudioAction(
            layer="speech",
            interrupt=True,
            cue_id="aftermath_relief",
            text="あー怖かったぁ。もう安心やな。",
        )
        dispatcher.play_actions([relief])

        self.assertTrue(process.terminated)
        self.assertEqual(dispatcher._epoch, 1)
        self.assertEqual(list(dispatcher._pending), [(1, [relief])])


class CombatAftermathServiceTests(unittest.TestCase):
    def test_player_voice_attached_to_combat_end_is_requeued_after_relief(self) -> None:
        with TemporaryDirectory() as tmp:
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_dir=Path(tmp) / "memory",
                )
            )
            service.process_event(
                make_event(
                    sequence=1,
                    at_sec=0,
                    event_name=EventName.THREAT_APPROACHING,
                    visual_threats=[spider_threat()],
                    hostiles_within_7=1,
                    hostiles_within_10=1,
                    hostiles_within_30_ground=1,
                    combat_active_hint=True,
                )
            )
            service.push_player_input("もう大丈夫？")

            processed = service.process_event(
                make_event(sequence=2, at_sec=6, event_name=EventName.COMBAT_ENDED)
            )
            session = next(iter(service.sessions.values()))

            self.assertTrue(
                any(action.cue_id == "aftermath_relief" for action in processed.actions)
            )
            self.assertEqual(session.pending_player_text, "もう大丈夫？")


if __name__ == "__main__":
    unittest.main()
