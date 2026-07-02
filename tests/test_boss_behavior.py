from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
from dogido_server.cues import DEFAULT_CUE_FILES
from dogido_server.llm import DogidoLLM
from dogido_server.models import (
    AuditoryThreat,
    Certainty,
    CombatState,
    DistanceBand,
    Direction,
    EventDescriptor,
    EventName,
    GameEvent,
    HorizontalDirection,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    TimePhase,
    VisualThreat,
    Weather,
    WorldState,
)
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.response_catalog import response_text

WARDEN_DEFEATED_LINE = response_text("boss", "warden", "defeated")


class FakeLLM(DogidoLLM):
    def __init__(self) -> None:
        super().__init__(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))

    def generate_leaf_text(self, request):  # type: ignore[override]
        return f"LLM:{request.kind}"


def make_visual_threat(hostile_type: str, distance: float) -> VisualThreat:
    return VisualThreat(
        type=hostile_type,
        entity_id=f"{hostile_type}-1",
        distance=distance,
        direction=Direction(horizontal=HorizontalDirection.FRONT_RIGHT),
        certainty=Certainty.HIGH,
    )


def make_event(
    *,
    sequence: int,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
    biome: str = "deep_dark",
    dimension: str = "minecraft:overworld",
    visual_threats: list[VisualThreat] | None = None,
    active_status_effects: list[str] | None = None,
    ominous_sound_kind: str | None = None,
    ominous_sound_recent_ms: int | None = None,
    boss_omen_kind: str | None = None,
) -> GameEvent:
    threats = visual_threats or []
    source_kind = SourceKind.SYSTEM if event_name == EventName.STATUS_SNAPSHOT else SourceKind.VISUAL
    priority = PriorityHint.BACKGROUND if event_name == EventName.STATUS_SNAPSHOT else PriorityHint.URGENT
    observed_at = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=sequence)
    combat_active = bool(threats)
    return GameEvent(
        schema_version="2026-05-24",
        game="minecraft-java",
        adapter="test-adapter",
        observed_at=observed_at,
        sequence=sequence,
        event=EventDescriptor(
            name=event_name,
            source_kind=source_kind,
            priority_hint=priority,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="player",
            position=Position(x=0.0, y=64.0, z=0.0),
            dimension=dimension,
            health=20.0,
            hunger=20,
            held_item="minecraft:torch",
            active_status_effects=active_status_effects or [],
        ),
        world=WorldState(
            time_phase=TimePhase.NIGHT if biome == "deep_dark" else TimePhase.DAY,
            time_of_day=18000 if biome == "deep_dark" else 6000,
            weather=Weather.CLEAR,
            biome=biome,
            local_light=0 if biome == "deep_dark" else 15,
            sky_visible=False if biome == "deep_dark" else True,
            ceiling_height=20.0,
            enclosure_score=0.2,
            overhead_cover_type="solid" if biome == "deep_dark" else "none",
            is_submerged=False,
            safe_zone_with_door=False,
            danger_darkness_score=0.2,
            ominous_sound_kind=ominous_sound_kind,
            ominous_sound_recent_ms=ominous_sound_recent_ms,
            boss_omen_kind=boss_omen_kind,
        ),
        visual_threats=threats,
        combat=CombatState(
            recent_hostile_visual_ms=0 if threats else None,
            hostiles_within_7=sum(1 for threat in threats if (threat.distance or 999.0) <= 7.0),
            hostiles_within_10=sum(1 for threat in threats if (threat.distance or 999.0) <= 10.0),
            hostiles_within_30_ground=len(threats),
            combat_active_hint=combat_active,
        ),
    )


def make_audio_threat(label: str, *, source_id: str = "audio-1") -> AuditoryThreat:
    return AuditoryThreat(
        label=label,
        source_id=source_id,
        sound_event=f"entity.{label}.ambient",
        direction=Direction(horizontal=HorizontalDirection.FRONT),
        distance_band=DistanceBand.CLOSE,
        certainty=Certainty.MEDIUM,
        spoken_name_allowed=True,
    )


class BossBehaviorTests(unittest.TestCase):
    def test_warden_reveal_in_deep_dark_allows_scream_and_escape_callout(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=1,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 7.0)],
            )
        )

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" and action.text == "ウォーデンや！逃げろ逃げろ！！" for action in result.actions))
        self.assertIn("boss_reveal_scream", DEFAULT_CUE_FILES)

    def test_warden_heartbeat_reacts_outside_deep_dark(self) -> None:
        # 地上にスポーンしたウォーデンの心音にもディープダークと同様に反応する
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=1,
                biome="plains",
                ominous_sound_kind="warden_heartbeat",
                ominous_sound_recent_ms=500,
            )
        )

        self.assertTrue(
            any(action.layer == "speech" and action.text for action in result.actions)
        )

    def test_warden_sonic_boom_screams_even_in_deep_dark(self) -> None:
        # ビームはディープダークの静音抑制よりも優先して悲鳴を上げる
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=1,
                biome="deep_dark",
                ominous_sound_kind="warden_sonic_boom",
                ominous_sound_recent_ms=500,
            )
        )

        scream = next(
            (action for action in result.actions if action.cue_id == "warden_sonic_boom_scream"),
            None,
        )
        self.assertIsNotNone(scream)
        self.assertEqual(scream.text, "ぎゃあああ！！")
        self.assertIn("warden_sonic_boom_scream", DEFAULT_CUE_FILES)

    def test_warden_sonic_boom_screams_on_surface_during_combat(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=1,
                event_name=EventName.THREAT_APPROACHING,
                biome="plains",
                visual_threats=[make_visual_threat("warden", 9.0)],
                ominous_sound_kind="warden_sonic_boom",
                ominous_sound_recent_ms=300,
            )
        )

        self.assertTrue(
            any(action.cue_id == "warden_sonic_boom_scream" for action in result.actions)
        )

    def test_ender_dragon_reveal_uses_tactical_callout_without_panic_cue(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=2,
                event_name=EventName.THREAT_APPROACHING,
                biome="the_end",
                dimension="minecraft:the_end",
                visual_threats=[make_visual_threat("ender_dragon", 18.0)],
            )
        )

        self.assertFalse(any(action.layer == "panic_cue" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" and action.text == "くるで！" for action in result.actions))

    def test_warden_combat_ended_emits_defeated_line_without_safe_zone(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=2,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 10.0)],
            )
        )
        result_event = make_event(
            sequence=3,
            event_name=EventName.COMBAT_ENDED,
            visual_threats=[],
            biome="plains",
        )
        result_event.combat.warden_defeat_confirmed = True
        result_event.combat.nearby_experience_orb_count = 1
        result = machine.process(result_event)

        self.assertEqual(result.state.mode, "aftermath")
        self.assertTrue(any(action.layer == "speech" and action.text == WARDEN_DEFEATED_LINE for action in result.actions))
        self.assertTrue(any(action.layer == "speech" and action.interrupt for action in result.actions))

    def test_warden_defeated_line_is_not_blocked_by_lingering_audio(self) -> None:
        # 激しい戦闘の直後は音の残響（auditory_threats）が残るが、
        # 討伐確認済みなら討伐ラインを待たせない
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=6,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 10.0)],
            )
        )
        result_event = make_event(
            sequence=7,
            event_name=EventName.COMBAT_ENDED,
            visual_threats=[],
            biome="plains",
        )
        result_event.combat.warden_defeat_confirmed = True
        result_event.auditory_threats = [make_audio_threat("hostile_presence", source_id="echo-1")]
        result = machine.process(result_event)

        self.assertEqual(result.state.mode, "aftermath")
        self.assertTrue(
            any(
                action.layer == "speech" and action.text == WARDEN_DEFEATED_LINE
                for action in result.actions
            )
        )

    def test_warden_combat_ended_without_defeat_confirmation_does_not_emit_defeated_line(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=4,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 10.0)],
            )
        )
        result = machine.process(
            make_event(
                sequence=5,
                event_name=EventName.COMBAT_ENDED,
                visual_threats=[],
                biome="plains",
            )
        )

        self.assertNotEqual(result.state.mode, "aftermath")
        self.assertFalse(any(action.text == WARDEN_DEFEATED_LINE for action in result.actions))

    def test_warden_attack_start_comment_emits_once_per_combat(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=50,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 9.0)],
            )
        )
        attack_start = make_event(
            sequence=51,
            event_name=EventName.THREAT_APPROACHING,
            visual_threats=[make_visual_threat("warden", 9.0)],
        )
        attack_start.combat.warden_recently_hurt = True
        first = machine.process(attack_start)

        repeat = make_event(
            sequence=52,
            event_name=EventName.THREAT_APPROACHING,
            visual_threats=[make_visual_threat("warden", 9.0)],
        )
        repeat.combat.warden_recently_hurt = True
        second = machine.process(repeat)

        self.assertTrue(any(action.layer == "callout" and action.text == "トロフィーなんてもらえへんで！？" for action in first.actions))
        self.assertFalse(any(action.layer == "callout" and action.text == "トロフィーなんてもらえへんで！？" for action in second.actions))

    def test_warden_ranged_trap_uses_common_extreme_line_once(self) -> None:
        # 上空ちくちくはクリスタル・TNTと共通の「そこまでして」ラインを1回だけ
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=60,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 12.0)],
            )
        )
        first_event = make_event(
            sequence=61,
            event_name=EventName.THREAT_APPROACHING,
            visual_threats=[make_visual_threat("warden", 12.0)],
        )
        first_event.combat.warden_ranged_trap_active = True
        first = machine.process(first_event)

        second_event = make_event(
            sequence=122,
            event_name=EventName.THREAT_APPROACHING,
            visual_threats=[make_visual_threat("warden", 12.0)],
        )
        second_event.combat.warden_ranged_trap_active = True
        second = machine.process(second_event)

        self.assertTrue(
            any(
                action.layer == "callout" and action.text == "……お前、そこまでしてウォーデンを！！"
                for action in first.actions
            )
        )
        self.assertFalse(
            any(
                action.text == "……お前、そこまでしてウォーデンを！！"
                for action in second.actions
            )
        )

    def test_warden_chasing_audio_uses_unknown_line_with_one_minute_cooldown(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        latest = None
        for sequence in range(200, 210):
            event = make_event(
                sequence=sequence,
                event_name=EventName.HOSTILE_AUDIO_DETECTED,
                biome="plains",
                visual_threats=[],
            )
            event.player.position.x = float(sequence - 200)
            event.auditory_threats = [make_audio_threat("warden", source_id="warden-audio")]
            event.combat.combat_active_hint = True
            latest = machine.process(event)

        self.assertIsNotNone(latest)
        self.assertTrue(any(action.layer == "callout" and action.text == "やっこさん、まだ追ってきよるよ！" for action in latest.actions))

        suppressed = make_event(
            sequence=211,
            event_name=EventName.HOSTILE_AUDIO_DETECTED,
            biome="plains",
            visual_threats=[],
        )
        suppressed.player.position.x = 11.0
        suppressed.auditory_threats = [make_audio_threat("warden", source_id="warden-audio")]
        suppressed.combat.combat_active_hint = True
        suppressed_result = machine.process(suppressed)
        self.assertFalse(any(action.layer == "callout" and action.text == "やっこさん、まだ追ってきよるよ！" for action in suppressed_result.actions))

    def test_warden_golem_and_bombardment_comments_emit_once(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=300,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 15.0)],
            )
        )

        golem = make_event(sequence=301, event_name=EventName.THREAT_APPROACHING, visual_threats=[make_visual_threat("warden", 15.0)])
        golem.combat.warden_nearby_iron_golem_count = 4
        golem_result = machine.process(golem)
        self.assertTrue(any(action.text == "うわうわうわ！ゴーレム兄さんらの集団リンチや！数の暴力ってホンマ恐ろしいな……ヤーさんかなんかかいな！！" for action in golem_result.actions))

        # クリスタル爆破は共通の「そこまでして」ライン
        crystal = make_event(sequence=302, event_name=EventName.THREAT_APPROACHING, visual_threats=[make_visual_threat("warden", 15.0)])
        crystal.combat.warden_end_crystal_bombardment_active = True
        crystal_result = machine.process(crystal)
        self.assertTrue(any(action.text == "……お前、そこまでしてウォーデンを！！" for action in crystal_result.actions))

        # TNT装置も同じ共通ライン扱いなので、同一戦闘中は繰り返さない
        tnt = make_event(sequence=303, event_name=EventName.THREAT_APPROACHING, visual_threats=[make_visual_threat("warden", 15.0)])
        tnt.combat.warden_tnt_minecart_setup_active = True
        tnt_result = machine.process(tnt)
        self.assertFalse(any(action.text == "……お前、そこまでしてウォーデンを！！" for action in tnt_result.actions))

    def test_warden_special_tactic_comment_takes_priority_over_attack_start(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=320,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 15.0)],
            )
        )

        crystal = make_event(
            sequence=321,
            event_name=EventName.THREAT_APPROACHING,
            visual_threats=[make_visual_threat("warden", 15.0)],
        )
        crystal.combat.warden_recently_hurt = True
        crystal.combat.warden_end_crystal_bombardment_active = True
        result = machine.process(crystal)

        self.assertTrue(any(action.text == "……お前、そこまでしてウォーデンを！！" for action in result.actions))
        self.assertFalse(any(action.text == "トロフィーなんてもらえへんで！？" for action in result.actions))

    def test_warden_special_comments_can_emit_from_recent_context_without_current_visual(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.process(
            make_event(
                sequence=304,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 15.0)],
            )
        )

        golem = make_event(sequence=305, event_name=EventName.THREAT_APPROACHING, visual_threats=[])
        golem.auditory_threats = [make_audio_threat("warden", source_id="warden-audio")]
        golem.combat.combat_active_hint = True
        golem.combat.warden_nearby_iron_golem_count = 2
        result = machine.process(golem)

        self.assertTrue(any(action.text == "うわうわうわ！ゴーレム兄さんらの集団リンチや！数の暴力ってホンマ恐ろしいな……ヤーさんかなんかかいな！！" for action in result.actions))

    def test_warden_special_comments_do_not_emit_without_warden_visual(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        event = make_event(
            sequence=320,
            event_name=EventName.THREAT_APPROACHING,
            biome="plains",
            visual_threats=[make_visual_threat("zombie", 8.0)],
        )
        event.combat.warden_recently_hurt = True
        event.combat.warden_ranged_trap_active = True
        event.combat.warden_nearby_iron_golem_count = 4
        event.combat.warden_end_crystal_bombardment_active = True
        event.combat.warden_tnt_minecart_setup_active = True

        result = machine.process(event)

        forbidden = {
            "トロフィーなんてもらえへんで！？",
            "……えぐいなぁ。お前、ほんまに容赦ないな。ちょっとウォーデンが可哀想になってきたわ。",
            "うわぁ、完全にハメ作業やん。人間の知恵って恐ろしいわぁ…",
            "うわうわうわ！ゴーレム兄さんらの集団リンチや！数の暴力ってホンマ恐ろしいな……ヤーさんかなんかかいな！！",
            "ドッカン、ドッカンて！！ちょ、やりすぎやりすぎ！地形エグいことなっとるて！お前それ絶対自分が巻き込まれるやつやん！",
            "……お前、そこまでしてウォーデンを！！",
        }
        self.assertFalse(any(action.text in forbidden for action in result.actions))

    def test_entered_mining_fatigue_emits_elder_guardian_hook(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=3,
                active_status_effects=["mining_fatigue"],
                biome="deep_ocean",
            )
        )

        self.assertTrue(any(action.layer == "speech" and action.text == "ん？なんかおかしいで？？？" for action in result.actions))

    def test_deep_dark_ominous_sound_emits_llm_line(self) -> None:
        machine = DogidoStateMachine(
            Settings(audio_enabled=False, decision_policy="py_trees", llm_enabled=True, llm_backend="noop"),
            llm=FakeLLM(),
        )
        result = machine.process(
            make_event(
                sequence=4,
                ominous_sound_kind="sculk_shrieker",
                ominous_sound_recent_ms=100,
            )
        )

        self.assertTrue(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in result.actions))

    def test_hostile_audio_detected_does_not_emit_ominous_line_even_for_warden_sound(self) -> None:
        machine = DogidoStateMachine(
            Settings(audio_enabled=False, decision_policy="py_trees", llm_enabled=True, llm_backend="noop"),
            llm=FakeLLM(),
        )
        event = make_event(
            sequence=8,
            event_name=EventName.HOSTILE_AUDIO_DETECTED,
            biome="plains",
            ominous_sound_kind="warden_presence",
            ominous_sound_recent_ms=100,
        )
        event.auditory_threats = [make_audio_threat("warden", source_id="warden-audio")]
        event.combat.combat_active_hint = True
        result = machine.process(event)

        self.assertFalse(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in result.actions))

    def test_sculk_ominous_sound_is_ignored_outside_deep_dark(self) -> None:
        machine = DogidoStateMachine(
            Settings(audio_enabled=False, decision_policy="py_trees", llm_enabled=True, llm_backend="noop"),
            llm=FakeLLM(),
        )
        result = machine.process(
            make_event(
                sequence=9,
                biome="plains",
                ominous_sound_kind="sculk_shrieker",
                ominous_sound_recent_ms=100,
            )
        )

        self.assertFalse(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in result.actions))

    def test_out_of_context_sculk_latch_does_not_suppress_surface_night_warning(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        machine.state.last_ominous_sound_kind = "sculk_shrieker"
        machine.state.last_ominous_sound_seen_at = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
        event = make_event(sequence=30, biome="plains")
        event.world.ominous_sound_kind = "sculk_shrieker"
        event.world.ominous_sound_recent_ms = 100
        event.world.time_phase = TimePhase.EVENING
        event.world.time_of_day = 13000
        event.world.sky_visible = True
        machine.state.night_warning_pending = True

        result = machine.process(event)

        self.assertFalse(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in result.actions))
        self.assertIsNone(machine.state.last_ominous_sound_kind)

    def test_sculk_ominous_sound_uses_two_minute_cooldown(self) -> None:
        machine = DogidoStateMachine(
            Settings(audio_enabled=False, decision_policy="py_trees", llm_enabled=True, llm_backend="noop"),
            llm=FakeLLM(),
        )
        first = machine.process(
            make_event(
                sequence=10,
                ominous_sound_kind="sculk_shrieker",
                ominous_sound_recent_ms=100,
            )
        )
        second = machine.process(
            make_event(
                sequence=20,
                ominous_sound_kind="sculk_shrieker",
                ominous_sound_recent_ms=100,
            )
        )

        self.assertTrue(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in first.actions))
        self.assertFalse(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in second.actions))

    def test_ominous_sound_cooldown_applies_across_kind_changes(self) -> None:
        machine = DogidoStateMachine(
            Settings(audio_enabled=False, decision_policy="py_trees", llm_enabled=True, llm_backend="noop"),
            llm=FakeLLM(),
        )
        first = machine.process(
            make_event(
                sequence=11,
                ominous_sound_kind="sculk_sensor",
                ominous_sound_recent_ms=100,
            )
        )
        second = machine.process(
            make_event(
                sequence=20,
                ominous_sound_kind="sculk_shrieker",
                ominous_sound_recent_ms=100,
            )
        )

        self.assertTrue(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in first.actions))
        self.assertFalse(any(action.layer == "speech" and action.text == "LLM:deep_dark_ominous_sound" for action in second.actions))

    def test_ominous_sound_suppresses_night_and_biome_flavor_lines(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        event = make_event(sequence=30, biome="plains")
        event.world.time_phase = TimePhase.EVENING
        event.world.time_of_day = 13000
        event.world.sky_visible = True
        machine.state.night_warning_pending = True
        machine.state.last_ominous_sound_kind = "warden_heartbeat"
        machine.state.last_ominous_sound_seen_at = event.observed_at - timedelta(seconds=1)

        result = machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))
        self.assertFalse(machine.state.night_warning_pending)
        self.assertIsNone(machine.state.pending_special_biome_line)

    def test_ominous_sound_rewrites_deep_dark_biome_flavor_line(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        event = make_event(sequence=31, biome="deep_dark")
        event.world.local_light = 12
        event.world.sky_visible = True
        event.world.overhead_cover_type = "none"
        event.world.danger_darkness_score = 0.0
        machine.state.pending_special_biome_line = "しずかやな……"
        machine.state.current_biome = "deep_dark"
        machine.state.last_ominous_sound_kind = "warden_heartbeat"
        machine.state.last_ominous_sound_seen_at = event.observed_at - timedelta(seconds=1)

        result = machine.process(event)

        self.assertTrue(
            any(
                action.layer == "speech"
                and action.text == "静かな分、音が反響して、よー響くな・・・"
                for action in result.actions
            )
        )
        self.assertIsNone(machine.state.pending_special_biome_line)

    def test_warden_reveal_suppresses_followup_ominous_night_and_biome_lines(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        reveal = machine.process(
            make_event(
                sequence=40,
                event_name=EventName.THREAT_APPROACHING,
                visual_threats=[make_visual_threat("warden", 12.0)],
            )
        )
        self.assertTrue(any(action.layer == "callout" and action.text == "ウォーデンや！逃げろ逃げろ！！" for action in reveal.actions))

        event = make_event(sequence=45, biome="deep_dark")
        event.world.time_phase = TimePhase.EVENING
        event.world.time_of_day = 13000
        event.world.ominous_sound_kind = "warden_heartbeat"
        event.world.ominous_sound_recent_ms = 100
        machine.state.night_warning_pending = True
        machine.state.pending_special_biome_line = "しずかやな……"
        result = machine.process(event)

        self.assertFalse(any(action.layer == "speech" for action in result.actions))
        self.assertFalse(machine.state.night_warning_pending)
        self.assertIsNone(machine.state.pending_special_biome_line)

    def test_ender_dragon_arena_hint_emits_from_boss_omen(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=5,
                biome="the_end",
                dimension="minecraft:the_end",
                boss_omen_kind="ender_dragon_arena",
            )
        )

        self.assertTrue(any(action.layer == "speech" and action.text == "これ、やばいやつちゃう？" for action in result.actions))

    def test_wither_assembly_hint_emits_from_boss_omen(self) -> None:
        machine = DogidoStateMachine(Settings(audio_enabled=False, decision_policy="py_trees"))
        result = machine.process(
            make_event(
                sequence=6,
                biome="soul_sand_valley",
                dimension="minecraft:the_nether",
                boss_omen_kind="wither_assembly",
            )
        )

        self.assertTrue(any(action.layer == "speech" and action.text == "えっ・・・何しとるん？" for action in result.actions))


if __name__ == "__main__":
    unittest.main()
