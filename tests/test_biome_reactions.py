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
    time_phase: TimePhase,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
    visual_threats: list[VisualThreat] | None = None,
    local_light: int = 15,
    sky_visible: bool = True,
    ceiling_height: float = 20.0,
    enclosure_score: float = 0.0,
) -> GameEvent:
    threats = visual_threats or []
    source_kind = SourceKind.SYSTEM if event_name == EventName.STATUS_SNAPSHOT else SourceKind.VISUAL
    priority = PriorityHint.BACKGROUND if event_name == EventName.STATUS_SNAPSHOT else PriorityHint.URGENT
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=sequence),
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
            dimension="minecraft:overworld",
            health=20.0,
            hunger=20,
            held_item="minecraft:torch",
        ),
        world=WorldState(
            time_phase=time_phase,
            time_of_day=6000 if time_phase == TimePhase.DAY else 18000,
            weather=Weather.CLEAR,
            biome=biome,
            local_light=local_light,
            sky_visible=sky_visible,
            ceiling_height=ceiling_height,
            enclosure_score=enclosure_score,
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


def make_visual_threat(hostile_type: str, distance: float) -> VisualThreat:
    return VisualThreat(
        type=hostile_type,
        entity_id=f"{hostile_type}-1",
        distance=distance,
        direction=Direction(horizontal=HorizontalDirection.FRONT),
        certainty=Certainty.HIGH,
    )


class SpecialBiomeReactionTests(unittest.TestCase):
    def make_machine(self) -> DogidoStateMachine:
        return DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def speech_texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.layer == "speech" and action.text]

    def test_special_biome_entry_comment_emits_once_until_biome_changes(self) -> None:
        self.machine = self.make_machine()

        first_entry = self.speech_texts(
            make_event(sequence=1, biome="mushroom_fields", time_phase=TimePhase.DAY)
        )
        self.assertEqual(["ほんに、妙なところやなぁ。敵の気配がせえへんわ。"], first_entry)

        same_biome = self.speech_texts(
            make_event(sequence=2, biome="mushroom_fields", time_phase=TimePhase.DAY)
        )
        self.assertEqual([], same_biome)

        other_biome = self.speech_texts(make_event(sequence=3, biome="plains", time_phase=TimePhase.DAY))
        self.assertEqual([], other_biome)

        reentry_at_night = self.speech_texts(
            make_event(sequence=601, biome="mushroom_fields", time_phase=TimePhase.NIGHT)
        )
        self.assertEqual(
            ["夜やのに全然敵おらん……安全すぎて逆に怖いわ〜。上から来たりせんかなー"],
            reentry_at_night,
        )

    def test_special_biome_comment_is_deferred_until_threats_clear(self) -> None:
        self.machine = self.make_machine()

        threat_event = self.machine.process(
            make_event(
                sequence=1,
                event_name=EventName.THREAT_APPROACHING,
                biome="dripstone_caves",
                time_phase=TimePhase.DAY,
                visual_threats=[make_visual_threat("zombie", 2.0)],
            )
        )
        self.assertFalse(any(action.layer == "speech" for action in threat_event.actions))

        cleared = self.speech_texts(
            make_event(sequence=2, biome="dripstone_caves", time_phase=TimePhase.DAY)
        )
        self.assertEqual(["このたけのこ、落ちてきそうでちょっとこわいわ……"], cleared)

    def test_deep_dark_suppresses_panic_cue_but_keeps_callout(self) -> None:
        self.machine = self.make_machine()

        result = self.machine.process(
            make_event(
                sequence=1,
                event_name=EventName.THREAT_APPROACHING,
                biome="deep_dark",
                time_phase=TimePhase.DAY,
                visual_threats=[make_visual_threat("creeper", 2.0)],
                local_light=0,
                sky_visible=False,
                ceiling_height=4.0,
                enclosure_score=0.3,
            )
        )

        self.assertFalse(any(action.layer == "panic_cue" for action in result.actions))
        self.assertTrue(any(action.layer == "callout" and action.text for action in result.actions))


if __name__ == "__main__":
    unittest.main()
