"""look_target（クロスヘア）観測と STT 文脈補正。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dogido_server.models import (
    Certainty,
    EventDescriptor,
    EventName,
    GameEvent,
    LookTarget,
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    TimePhase,
    Weather,
    WorldState,
)
from dogido_server.player_input.asr_fixes import (
    apply_contextual_asr_fixes,
    has_pressure_plate_context,
)
from dogido_server.state_machine.mixins.narration import NarrationMixin


def _event(*, look: LookTarget | None = None, held: str | None = None, user_text: str | None = None) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test",
        observed_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        sequence=1,
        event=EventDescriptor(
            name=EventName.STATUS_SNAPSHOT,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="p",
            position=Position(x=0, y=64, z=0),
            dimension="minecraft:overworld",
            held_item=held,
        ),
        world=WorldState(
            time_phase=TimePhase.DAY,
            weather=Weather.CLEAR,
            biome="plains",
            local_light=15,
            sky_visible=True,
        ),
        look_target=look,
        meta=MetaState(user_text=user_text),
    )


class LookTargetModelTests(unittest.TestCase):
    def test_parse_look_target(self) -> None:
        event = _event(
            look=LookTarget(kind="block", name="poppy", distance=2.0),
        )
        self.assertIsNotNone(event.look_target)
        assert event.look_target is not None
        self.assertEqual(event.look_target.name, "poppy")
        self.assertEqual(event.look_target.kind, "block")

    def test_optional_miss(self) -> None:
        event = _event()
        self.assertIsNone(event.look_target)


class LookTargetLabelTests(unittest.TestCase):
    def test_block_label_pressure_plate(self) -> None:
        # NarrationMixin のラベル解決だけ使う（薄いスタブ）
        class _N(NarrationMixin):
            pass

        n = _N()
        # mixins は machine 前提のメソッドもあるが _block_label は world_analysis 側
        from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin

        class _W(WorldAnalysisMixin):
            pass

        w = _W()
        label = w._block_label("oak_pressure_plate")
        self.assertIn("感圧板", label)

    def test_observation_includes_look(self) -> None:
        from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin

        class _Stub(NarrationMixin, WorldAnalysisMixin):
            pass

        stub = _Stub()
        event = _event(look=LookTarget(kind="block", name="poppy", distance=1.5))
        look_label = stub._look_target_label(event)
        summary = stub._player_chat_observation_summary(
            event,
            threat_summary="",
            hearing_summary="",
            passive_types=(),
            look_target_label=look_label,
        )
        self.assertIn("視線先", summary)
        self.assertTrue(look_label)


class ContextualAsrLookTests(unittest.TestCase):
    def test_context_detects_look_pressure_plate(self) -> None:
        self.assertTrue(
            has_pressure_plate_context(look_name="oak_pressure_plate", held_item=None, inventory=None)
        )
        self.assertFalse(
            has_pressure_plate_context(look_name="poppy", held_item=None, inventory=None)
        )

    def test_extra_fix_when_looking_at_plate(self) -> None:
        # 固定表に無いが文脈があるとき
        fixed, applied = apply_contextual_asr_fixes(
            "間圧板だよ",
            look_name="stone_pressure_plate",
        )
        self.assertEqual(fixed, "感圧板だよ")
        self.assertTrue(applied)

    def test_no_extra_without_context(self) -> None:
        fixed, applied = apply_contextual_asr_fixes("間圧板だよ", look_name="poppy")
        self.assertEqual(fixed, "間圧板だよ")
        self.assertEqual(applied, [])


if __name__ == "__main__":
    unittest.main()
