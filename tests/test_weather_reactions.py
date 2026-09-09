# tests/test_weather_reactions.py
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
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
from dogido_server.state_machine import DogidoStateMachine


def make_event(
    *,
    sequence: int,
    biome: str,
    weather: Weather,
    time_phase: TimePhase = TimePhase.DAY,
    sky_visible: bool = True,
    visual_threats: list[VisualThreat] | None = None,
    user_text: str | None = None,
) -> GameEvent:
    threats = visual_threats or []
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=sequence),
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
            time_phase=time_phase,
            time_of_day=6000,
            weather=weather,
            biome=biome,
            local_light=15,
            sky_visible=sky_visible,
            ceiling_height=20.0,
            enclosure_score=0.0,
            overhead_cover_type="none",
            is_submerged=False,
            safe_zone_with_door=False,
            danger_darkness_score=0.0,
        ),
        visual_threats=threats,
        combat=CombatState(
            recent_hostile_visual_ms=0 if threats else None,
            hostiles_within_7=sum(1 for threat in threats if (threat.distance or 999.0) <= 7.0),
            hostiles_within_10=sum(1 for threat in threats if (threat.distance or 999.0) <= 10.0),
            combat_active_hint=bool(threats),
        ),
        meta=MetaState(user_text=user_text),
    )


def make_visual_threat(hostile_type: str, *, distance: float = 12.0) -> VisualThreat:
    return VisualThreat(
        type=hostile_type,
        entity_id=f"{hostile_type}-1",
        distance=distance,
        direction=Direction(horizontal=HorizontalDirection.FRONT),
        certainty=Certainty.HIGH,
    )


class WeatherReactionTests(unittest.TestCase):
    def make_machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def test_dry_biome_weather_transition_uses_overcast_line_instead_of_rain(self) -> None:
        machine = self.make_machine()
        machine.process(make_event(sequence=1, biome="desert", weather=Weather.CLEAR))

        result = machine.process(make_event(sequence=2, biome="desert", weather=Weather.RAIN))

        speech_texts = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual(
            ["うわっ……空がどんよりしてきたで！ 暗うなったら敵湧きやすなるし、怖いわぁ！"],
            speech_texts,
        )

    def test_daylight_bad_weather_callout_uses_dry_biome_wording(self) -> None:
        machine = self.make_machine()
        threat = make_visual_threat("zombie")
        event = make_event(
            sequence=1,
            biome="desert",
            weather=Weather.RAIN,
            visual_threats=[threat],
        )

        line = machine._daylight_rain_callout(event, [threat], event.observed_at)

        self.assertEqual("あ！？なんでここで空がどんよりすんねん！！燃えてくれやーー！！", line)

    def test_underground_rain_transition_without_rain_sound_is_suppressed(self) -> None:
        machine = self.make_machine()
        machine.process(make_event(sequence=10, biome="deep_dark", weather=Weather.CLEAR, sky_visible=False))

        result = machine.process(make_event(sequence=11, biome="deep_dark", weather=Weather.RAIN, sky_visible=False))

        speech_texts = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual([], speech_texts)

    def test_underground_rain_transition_with_rain_sound_uses_low_certainty_line(self) -> None:
        machine = self.make_machine()
        machine.process(make_event(sequence=20, biome="deep_dark", weather=Weather.CLEAR, sky_visible=False))
        event = make_event(sequence=21, biome="deep_dark", weather=Weather.RAIN, sky_visible=False)
        event.world.rain_sound_recent_ms = 1200

        result = machine.process(event)

        speech_texts = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual(["なんか雨の音する気が・・・"], speech_texts)

    def test_underground_thunder_sound_uses_frightened_reaction(self) -> None:
        machine = self.make_machine()
        machine.process(make_event(sequence=30, biome="deep_dark", weather=Weather.RAIN, sky_visible=False))
        event = make_event(sequence=31, biome="deep_dark", weather=Weather.THUNDER, sky_visible=False)
        event.world.thunder_sound_recent_ms = 900

        result = machine.process(event)

        speech_texts = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual(["また鳴った……ほんま雷だけは落ち着かへんな。"], speech_texts)

    def test_nearby_lightning_strike_emits_gasp_and_callout(self) -> None:
        machine = self.make_machine()
        event = make_event(sequence=40, biome="plains", weather=Weather.THUNDER, sky_visible=True)
        event.world.nearby_lightning_strike_recent_ms = 300
        event.world.nearby_lightning_strike_distance = 12.0

        result = machine.process(event)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in result.actions))
        self.assertTrue(any(action.layer == "speech" and action.text == "今、落ちたで！" for action in result.actions))

    def test_heard_thunder_emits_frightened_reaction_without_weather_transition(self) -> None:
        machine = self.make_machine()
        event = make_event(sequence=50, biome="plains", weather=Weather.THUNDER)
        event.world.thunder_sound_recent_ms = 500

        result = machine.process(event)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ヒイ！" for action in result.actions))
        self.assertTrue(
            any(
                action.layer == "speech"
                and action.text == "また鳴った……ほんま雷だけは落ち着かへんな。"
                for action in result.actions
            )
        )

    def test_heard_thunder_reaction_has_cooldown(self) -> None:
        machine = self.make_machine()
        first = make_event(sequence=60, biome="plains", weather=Weather.THUNDER)
        first.world.thunder_sound_recent_ms = 300
        repeated = make_event(sequence=61, biome="plains", weather=Weather.THUNDER)
        repeated.world.thunder_sound_recent_ms = 1300

        first_result = machine.process(first)
        repeated_result = machine.process(repeated)

        self.assertTrue(any(action.text for action in first_result.actions))
        self.assertFalse(any(action.text for action in repeated_result.actions))

    def test_nearby_lightning_is_more_specific_than_generic_thunder(self) -> None:
        machine = self.make_machine()
        event = make_event(sequence=70, biome="plains", weather=Weather.THUNDER)
        event.world.thunder_sound_recent_ms = 300
        event.world.nearby_lightning_strike_recent_ms = 300
        event.world.nearby_lightning_strike_distance = 12.0

        result = machine.process(event)
        speech = [action.text for action in result.actions if action.layer == "speech" and action.text]

        self.assertEqual(["今、落ちたで！"], speech)

    def test_thunder_message_and_panic_cue_have_separate_cooldowns(self) -> None:
        machine = self.make_machine()
        first = make_event(sequence=100, biome="plains", weather=Weather.THUNDER)
        after_three_minutes = make_event(
            sequence=281,
            biome="plains",
            weather=Weather.THUNDER,
        )
        after_ten_minutes = make_event(
            sequence=700,
            biome="plains",
            weather=Weather.THUNDER,
        )
        for event in (first, after_three_minutes, after_ten_minutes):
            event.world.thunder_sound_recent_ms = 300

        first_result = machine.process(first)
        three_minute_result = machine.process(after_three_minutes)
        ten_minute_result = machine.process(after_ten_minutes)

        self.assertTrue(any(action.layer == "panic_cue" for action in first_result.actions))
        self.assertTrue(any(action.layer == "speech" for action in first_result.actions))
        self.assertFalse(
            any(action.layer == "panic_cue" for action in three_minute_result.actions)
        )
        self.assertTrue(any(action.layer == "speech" for action in three_minute_result.actions))
        self.assertTrue(any(action.layer == "panic_cue" for action in ten_minute_result.actions))
        self.assertTrue(any(action.layer == "speech" for action in ten_minute_result.actions))

    def test_thunder_message_uses_llm_weather_route(self) -> None:
        class CaptureLLM:
            def __init__(self) -> None:
                self.requests = []

            def preload(self) -> bool:
                return False

            def generate_leaf_text(self, request):  # type: ignore[no-untyped-def]
                self.requests.append(request)
                return "またゴロゴロ言うとる……落ち着かへんな。"

            def generate_structured_json(self, request):  # type: ignore[no-untyped-def]
                return {}

        llm = CaptureLLM()
        machine = DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=llm,
        )
        event = make_event(sequence=800, biome="plains", weather=Weather.THUNDER)
        event.world.thunder_sound_recent_ms = 300

        result = machine.process(event)

        speech = [action.text for action in result.actions if action.layer == "speech"]
        self.assertEqual(["またゴロゴロ言うとる……落ち着かへんな。"], speech)
        self.assertEqual("weather_transition", llm.requests[0].kind)
        self.assertEqual("thunder_heard", llm.requests[0].details.get("scene"))
        self.assertTrue(llm.requests[0].details.get("thunder_reaction"))

    def test_thunder_llm_prompt_is_a_mutter_not_an_extra_scream(self) -> None:
        messages = build_messages(
            LeafGenerationRequest(
                kind="weather_transition",
                fallback_text="fallback",
                details={
                    "scene": "thunder_heard",
                    "thunder_reaction": True,
                    "nearby_lightning": False,
                    "weather_from": "thunder",
                    "weather_to": "thunder",
                },
            )
        )
        prompt = "\n".join(message["content"] for message in messages)

        self.assertIn("悲鳴を文字で重ねず小さく怖がる", prompt)
        self.assertIn("近くへ落雷したとは断定しない", prompt)
        self.assertIn("ぶつぶつ漏れる", prompt)

    def test_thunder_preempts_player_reply_then_uses_existing_cooldowns(
        self,
    ) -> None:
        machine = self.make_machine()
        chat = make_event(
            sequence=900,
            biome="plains",
            weather=Weather.THUNDER,
            user_text="まだ雷すごいな",
        )
        chat.world.thunder_sound_recent_ms = 300
        within_three_minutes = make_event(
            sequence=1000,
            biome="plains",
            weather=Weather.THUNDER,
        )
        within_three_minutes.world.thunder_sound_recent_ms = 300
        after_three_minutes = make_event(
            sequence=1081,
            biome="plains",
            weather=Weather.THUNDER,
        )
        after_three_minutes.world.thunder_sound_recent_ms = 300

        chat_result = machine.process(chat)
        muted_result = machine.process(within_three_minutes)
        resumed_result = machine.process(after_three_minutes)

        self.assertTrue(any(action.layer == "speech" for action in chat_result.actions))
        self.assertTrue(any(action.layer == "panic_cue" for action in chat_result.actions))
        self.assertTrue(any(action.interrupt for action in chat_result.actions))
        self.assertTrue(all(action.defer_player_input for action in chat_result.actions))
        self.assertEqual([], [action.text for action in muted_result.actions if action.text])
        self.assertFalse(any(action.layer == "panic_cue" for action in resumed_result.actions))
        self.assertTrue(any(action.layer == "speech" for action in resumed_result.actions))
        self.assertFalse(any(action.interrupt for action in resumed_result.actions))
        self.assertTrue(all(action.defer_player_input for action in resumed_result.actions))

    def test_surface_thunder_suppresses_passive_mob_comment_but_cave_keeps_it(self) -> None:
        machine = self.make_machine()
        surface = make_event(sequence=1100, biome="plains", weather=Weather.THUNDER)
        surface.passive_mobs = [
            PassiveMob(
                type="cow",
                distance=4.0,
                direction=Direction(horizontal=HorizontalDirection.FRONT),
            )
        ]
        cave = make_event(
            sequence=1101,
            biome="dripstone_caves",
            weather=Weather.THUNDER,
            sky_visible=False,
        )
        cave.passive_mobs = list(surface.passive_mobs)

        self.assertFalse(machine._should_emit_ambient_mob_comment(surface, surface.observed_at))
        self.assertTrue(machine._should_emit_ambient_mob_comment(cave, cave.observed_at))

    def test_surface_thunder_does_not_suppress_creeper_warning(self) -> None:
        machine = self.make_machine()
        creeper = make_visual_threat("creeper", distance=12.0)
        creeper.approaching = True
        event = make_event(
            sequence=1200,
            biome="plains",
            weather=Weather.THUNDER,
            visual_threats=[creeper],
        )

        result = machine.process(event)

        self.assertTrue(
            any("クリーパー" in (action.text or "") for action in result.actions)
        )


if __name__ == "__main__":
    unittest.main()
