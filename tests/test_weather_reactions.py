# tests/test_weather_reactions.py
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dogido_server.config import Settings
from dogido_server.models import (
    Certainty,
    CombatState,
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


def make_event(
    *,
    sequence: int,
    biome: str,
    weather: Weather,
    time_phase: TimePhase = TimePhase.DAY,
    sky_visible: bool = True,
    visual_threats: list[VisualThreat] | None = None,
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
    )


def make_visual_threat(hostile_type: str, *, distance: float = 12.0) -> VisualThreat:
    return VisualThreat(
        type=hostile_type,
        entity_id=f"{hostile_type}-1",
        distance=distance,
        direction=Direction(horizontal=HorizontalDirection.FRONT),
        certainty=Certainty.HIGH,
    )


class DryBiomeWeatherReactionTests(unittest.TestCase):
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

    def test_underground_thunder_transition_with_thunder_sound_uses_low_certainty_line(self) -> None:
        machine = self.make_machine()
        machine.process(make_event(sequence=30, biome="deep_dark", weather=Weather.RAIN, sky_visible=False))
        event = make_event(sequence=31, biome="deep_dark", weather=Weather.THUNDER, sky_visible=False)
        event.world.thunder_sound_recent_ms = 900

        result = machine.process(event)

        speech_texts = [action.text for action in result.actions if action.layer == "speech" and action.text]
        self.assertEqual(["なんかゴロゴロ聞こえるな……雷きとるんか？"], speech_texts)

    def test_nearby_lightning_strike_emits_gasp_and_callout(self) -> None:
        machine = self.make_machine()
        event = make_event(sequence=40, biome="plains", weather=Weather.THUNDER, sky_visible=True)
        event.world.nearby_lightning_strike_recent_ms = 300
        event.world.nearby_lightning_strike_distance = 12.0

        result = machine.process(event)

        self.assertTrue(any(action.layer == "panic_cue" and action.text == "ひいっ！" for action in result.actions))
        self.assertTrue(any(action.layer == "speech" and action.text == "今、落ちたで！" for action in result.actions))


if __name__ == "__main__":
    unittest.main()
