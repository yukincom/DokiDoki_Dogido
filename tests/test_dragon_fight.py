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
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    VisualThreat,
    Weather,
    WorldState,
)
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.response_catalog import response_text

BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def make_event(
    *,
    sequence: int,
    at_sec: float,
    event_name: EventName = EventName.STATUS_SNAPSHOT,
    dimension: str = "minecraft:the_end",
    biome: str = "the_end",
    dragon_phase: str | None = None,
    dragon_distance: float | None = None,
    dragon_horizontal: str | None = None,
    dragon_vertical: str | None = None,
    dragon_defeat_confirmed: bool | None = None,
    end_crystal_count: int | None = None,
    nearby_portal_type: str | None = None,
    nearby_portal_distance: float | None = None,
    user_text: str | None = None,
    visual_threats: list[VisualThreat] | None = None,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=BASE + timedelta(seconds=at_sec),
        sequence=sequence,
        event=EventDescriptor(
            name=event_name,
            source_kind=SourceKind.SYSTEM,
            priority_hint=PriorityHint.BACKGROUND,
            certainty=Certainty.HIGH,
        ),
        player=PlayerState(
            name="player",
            position=Position(x=0.0, y=70.0, z=0.0),
            dimension=dimension,
            health=20.0,
            hunger=20,
        ),
        world=WorldState(
            weather=Weather.CLEAR,
            biome=biome,
            local_light=15,
            sky_visible=True,
            ceiling_height=64.0,
            enclosure_score=0.0,
            overhead_cover_type="none",
            is_submerged=False,
            danger_darkness_score=0.0,
            nearby_portal_type=nearby_portal_type,
            nearby_portal_distance=nearby_portal_distance,
        ),
        visual_threats=visual_threats or [],
        combat=CombatState(
            dragon_phase=dragon_phase,
            dragon_distance=dragon_distance,
            dragon_horizontal=dragon_horizontal,
            dragon_vertical=dragon_vertical,
            dragon_defeat_confirmed=dragon_defeat_confirmed,
            end_crystal_count=end_crystal_count,
        ),
        meta=MetaState(user_text=user_text),
    )


def dragon_threat(distance: float) -> VisualThreat:
    return VisualThreat(
        type="ender_dragon",
        entity_id="dragon-1",
        distance=distance,
        direction=Direction(horizontal=HorizontalDirection.FRONT),
    )


class DragonFightTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DogidoStateMachine(Settings(decision_policy="py_trees", llm_enabled=False))

    def texts(self, event: GameEvent) -> list[str]:
        result = self.machine.process(event)
        return [action.text for action in result.actions if action.text]


class DragonCrystalTests(DragonFightTestBase):
    def test_first_hint_then_count_only_on_each_break(self) -> None:
        first = self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=10))
        self.assertEqual(
            [response_text("boss", "ender_dragon", "crystal_first_hint", count="10")], first
        )

        unchanged = self.texts(make_event(sequence=2, at_sec=1, dragon_phase="holding_pattern", end_crystal_count=10))
        self.assertEqual([], unchanged)

        broken = self.texts(make_event(sequence=3, at_sec=10, dragon_phase="holding_pattern", end_crystal_count=9))
        self.assertEqual([response_text("boss", "ender_dragon", "crystal_remaining", count="9")], broken)

        # 連続破壊は最新の残数だけ
        burst = self.texts(make_event(sequence=4, at_sec=20, dragon_phase="holding_pattern", end_crystal_count=6))
        self.assertEqual([response_text("boss", "ender_dragon", "crystal_remaining", count="6")], burst)

    def test_zero_crystals_emits_clear_line(self) -> None:
        self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=2))
        cleared = self.texts(make_event(sequence=2, at_sec=10, dragon_phase="holding_pattern", end_crystal_count=0))
        self.assertEqual([response_text("boss", "ender_dragon", "crystal_clear")], cleared)


class DragonCalloutTests(DragonFightTestBase):
    def test_charge_callout_with_cooldown(self) -> None:
        self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=0))

        charge = self.texts(make_event(sequence=2, at_sec=10, dragon_phase="charging_player"))
        self.assertEqual([response_text("boss", "ender_dragon", "approach")], charge)

        still_charging = self.texts(make_event(sequence=3, at_sec=13, dragon_phase="charging_player"))
        self.assertEqual([], still_charging)

    def test_chance_time_once_per_perch(self) -> None:
        self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=0))

        perch = self.texts(make_event(sequence=2, at_sec=10, dragon_phase="sitting_scanning"))
        self.assertEqual([response_text("boss", "ender_dragon", "chance_time")], perch)

        staying = self.texts(make_event(sequence=3, at_sec=12, dragon_phase="sitting_attacking"))
        self.assertEqual([], staying)

        self.texts(make_event(sequence=4, at_sec=30, dragon_phase="takeoff"))
        again = self.texts(make_event(sequence=5, at_sec=60, dragon_phase="landing_approach"))
        self.assertEqual([response_text("boss", "ender_dragon", "chance_time")], again)

    def test_callouts_bypass_player_input_mute(self) -> None:
        self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=3))
        # 話しかけ（入力優先120秒ミュート発動）
        self.texts(
            make_event(
                sequence=2,
                at_sec=5,
                dragon_phase="holding_pattern",
                end_crystal_count=3,
                user_text="がんばるで",
            )
        )

        charge = self.texts(make_event(sequence=3, at_sec=15, dragon_phase="charging_player", end_crystal_count=3))
        self.assertEqual([response_text("boss", "ender_dragon", "approach")], charge)


class DragonDirectionQuestionTests(DragonFightTestBase):
    def test_direction_answer_with_above(self) -> None:
        self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=0))
        answer = self.texts(
            make_event(
                sequence=2,
                at_sec=10,
                dragon_phase="holding_pattern",
                dragon_horizontal="back_left",
                dragon_vertical="above",
                user_text="ドラゴンどこ？",
            )
        )
        self.assertEqual(
            [response_text("boss", "ender_dragon", "direction_above", direction="左後ろ")], answer
        )

    def test_direction_answer_same_height(self) -> None:
        self.texts(make_event(sequence=1, at_sec=0, dragon_phase="holding_pattern", end_crystal_count=0))
        answer = self.texts(
            make_event(
                sequence=2,
                at_sec=10,
                dragon_phase="sitting_scanning",
                dragon_horizontal="front",
                dragon_vertical="same",
                user_text="ドラゴンどっち？",
            )
        )
        # 着地アナウンスより質問への回答を優先する
        self.assertEqual(
            [response_text("boss", "ender_dragon", "direction_answer", direction="前")], answer
        )

    def test_direction_unknown_without_dragon_info(self) -> None:
        answer = self.texts(
            make_event(sequence=1, at_sec=0, dimension="minecraft:overworld", biome="plains",
                       user_text="ドラゴンどこ？")
        )
        self.assertEqual([response_text("boss", "ender_dragon", "direction_unknown")], answer)


class DragonRevealAndSilenceTests(DragonFightTestBase):
    def test_first_sight_reveals_then_no_periodic_position_callouts(self) -> None:
        spoken: list[str] = []
        spoken.extend(
            self.texts(
                make_event(
                    sequence=1,
                    at_sec=0,
                    event_name=EventName.THREAT_APPROACHING,
                    dragon_phase="holding_pattern",
                    visual_threats=[dragon_threat(25.0)],
                )
            )
        )
        self.assertEqual([response_text("boss", "ender_dragon", "reveal")], spoken)

        # 以降90秒、視界に居続けても自発の方角通知はゼロ
        later: list[str] = []
        for index, at_sec in enumerate(range(5, 95, 5), start=2):
            later.extend(
                self.texts(
                    make_event(
                        sequence=index,
                        at_sec=float(at_sec),
                        event_name=EventName.THREAT_APPROACHING,
                        dragon_phase="holding_pattern",
                        visual_threats=[dragon_threat(20.0)],
                    )
                )
            )
        self.assertEqual([], later)


class DragonDefeatTests(DragonFightTestBase):
    def test_defeat_confirmed_emits_dragon_defeated_line(self) -> None:
        self.texts(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                dragon_phase="holding_pattern",
                visual_threats=[dragon_threat(20.0)],
            )
        )
        result = self.machine.process(
            make_event(
                sequence=2,
                at_sec=20,
                event_name=EventName.COMBAT_ENDED,
                dragon_phase="dying",
                dragon_defeat_confirmed=True,
            )
        )
        self.assertEqual(result.state.mode, "aftermath")
        texts = [action.text for action in result.actions if action.text]
        self.assertIn(response_text("boss", "ender_dragon", "defeated"), texts)

    def test_combat_ended_without_confirmation_stays_quiet(self) -> None:
        self.texts(
            make_event(
                sequence=1,
                at_sec=0,
                event_name=EventName.THREAT_APPROACHING,
                dragon_phase="holding_pattern",
                visual_threats=[dragon_threat(20.0)],
            )
        )
        result = self.machine.process(
            make_event(sequence=2, at_sec=20, event_name=EventName.COMBAT_ENDED)
        )
        texts = [action.text for action in result.actions if action.text]
        self.assertNotIn(response_text("boss", "ender_dragon", "defeated"), texts)


class ReturnPortalTests(DragonFightTestBase):
    def test_end_portal_reacts_again_after_dimension_change(self) -> None:
        # オーバーワールド（要塞）でエンドポータル起動を見る
        self.texts(make_event(sequence=1, at_sec=0, dimension="minecraft:overworld", biome="plains"))
        stronghold = self.texts(
            make_event(
                sequence=2,
                at_sec=10,
                dimension="minecraft:overworld",
                biome="plains",
                nearby_portal_type="end_portal",
                nearby_portal_distance=3.0,
            )
        )
        self.assertEqual(
            [response_text("exploration", "portal", "appearance_fallbacks", "end_portal")],
            stronghold,
        )

        # エンドへ移動（リセット＋ベースライン取り直し）
        self.texts(make_event(sequence=3, at_sec=30))
        self.texts(make_event(sequence=4, at_sec=31))

        # 討伐後、帰還ポータルにend_portalブロックが出現
        return_portal = self.texts(
            make_event(
                sequence=5,
                at_sec=60,
                nearby_portal_type="end_portal",
                nearby_portal_distance=4.0,
            )
        )
        self.assertEqual(
            [response_text("exploration", "portal", "appearance_fallbacks", "end_portal")],
            return_portal,
        )


if __name__ == "__main__":
    unittest.main()
