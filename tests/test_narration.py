from __future__ import annotations

import unittest

from dogido_server.config import Settings
from dogido_server.llm import DogidoLLM
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.models import (
    Certainty,
    CombatState,
    Direction,
    EventDescriptor,
    EventName,
    GameEvent,
    PassiveMob,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    WorldState,
)
from dogido_server.service import DogidoService
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.types import DerivedSignals


class CaptureAmbientLLM(DogidoLLM):
    def __init__(self) -> None:
        super().__init__(Settings(audio_enabled=False, llm_enabled=True, llm_backend="noop"))
        self.requests: list[LeafGenerationRequest] = []

    def generate_leaf_text(self, request):  # type: ignore[override]
        self.requests.append(request)
        return f"LLM:{request.kind}"


class NarrationMixinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DogidoStateMachine(Settings(llm_enabled=False, decision_policy="py_trees"))

    def _event(self, inventory: dict[str, int] | None = None) -> GameEvent:
        return GameEvent(
            schema_version="2026-05-24",
            adapter="test-adapter",
            observed_at="2026-06-01T12:00:00+09:00",
            event=EventDescriptor(
                name=EventName.AMBIENT_MOB_DETECTED,
                source_kind=SourceKind.VISUAL,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(name="player", position=Position(x=0, y=64, z=0)),
            world=WorldState(time_phase="day", biome="plains"),
            inventory=inventory or {},
        )

    def test_ambient_mob_line_is_generic_for_rabbit(self) -> None:
        rabbit = PassiveMob(type="rabbit", distance=3.0)
        self.assertEqual(
            self.machine._ambient_mob_line(self._event(), [rabbit]),
            "お！ ウサギおるやん！",
        )

    def test_ambient_mob_line_prefers_specific_catalog_line(self) -> None:
        wolf = PassiveMob(type="wolf", distance=6.0)
        self.assertEqual(
            self.machine._ambient_mob_line(self._event(), [wolf]),
            "可愛いからって叩いたらアカンで！仲間呼ばれてボコボコにされるわ！",
        )

    def test_ambient_mob_line_uses_conditional_specific_line(self) -> None:
        dolphin = PassiveMob(type="dolphin", distance=8.0)
        self.assertEqual(
            self.machine._ambient_mob_line(self._event({"salmon": 1}), [dolphin]),
            "餌やろうや！",
        )

    def test_ambient_mob_line_prefers_specific_catalog_line_for_zombified_piglin(self) -> None:
        piglin = PassiveMob(type="zombified_piglin", distance=7.0, temperament="neutral", caution_reason="provoked_only")
        self.assertEqual(
            self.machine._ambient_mob_line(self._event(), [piglin]),
            "ゾンピグや！絶対手ぇ出したらアカンで！束になって来よるからな！",
        )

    def test_ambient_mob_line_prefers_specific_catalog_line_for_fox(self) -> None:
        fox = PassiveMob(type="fox", distance=5.0, temperament="friendly")
        self.assertEqual(
            self.machine._ambient_mob_line(self._event(), [fox]),
            "おっ、キツネや。なんだかあいつらを見ると共感を感じるんや…",
        )

    def test_render_ambient_mob_line_uses_mobs_catalog_tags_for_llm(self) -> None:
        llm = CaptureAmbientLLM()
        machine = DogidoStateMachine(Settings(audio_enabled=False), llm=llm)
        bee = PassiveMob(
            type="bee",
            distance=4.0,
            direction=Direction(horizontal="front", vertical="same"),
            temperament="neutral",
            caution_reason="swarm",
        )

        line = machine._render_ambient_mob_line(self._event(), [bee])

        self.assertEqual(line, "LLM:ambient")
        self.assertEqual(len(llm.requests), 1)
        self.assertEqual(llm.requests[0].kind, "ambient")
        self.assertIn("花", llm.requests[0].details["mob_tags"])
        self.assertEqual(llm.requests[0].details["mob_role"], "手順を間違えると怖い")
        self.assertEqual(llm.requests[0].details["mob_temperament"], "neutral")
        self.assertEqual(llm.requests[0].details["mob_caution_reason"], "swarm")

    def test_occluded_entry_with_light_uses_catalog_fallback(self) -> None:
        event = GameEvent(
            schema_version="2026-05-24",
            adapter="test-adapter",
            observed_at="2026-06-01T12:00:00+09:00",
            event=EventDescriptor(
                name=EventName.STATUS_SNAPSHOT,
                source_kind=SourceKind.SYSTEM,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(name="player", position=Position(x=0, y=64, z=0)),
            world=WorldState(time_phase="day", biome="plains", local_light=7),
            combat=CombatState(),
        )
        signals = DerivedSignals(torch_available=True)
        self.assertEqual(
            self.machine._render_occluded_entry_line(event, signals),
            "え、ここ急に暗ない？ ん……あかりは持っとるな!早速、設置や！",
        )


class ServiceFallbackCatalogTest(unittest.TestCase):
    def test_fallback_speech_catalog_includes_narration_and_ambient_lines(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False))
        texts = service._fallback_speech_catalog("メルちゃん")
        self.assertIn("え、メルちゃん、ここ急に暗ない？ ん……あかりは持っとるな!早速、設置や！", texts)
        self.assertIn("お！ ウサギおるやん！", texts)
        self.assertNotIn("さ。ねよねよ！", texts)
        self.assertIn("おいおいおい！水、入ったら燃えへんやろ！！燃えるとこ行けや！！！", texts)
        self.assertIn("うわっ……雨降ってきたで！くろぉなったらまた敵湧きやすなるで……怖いわぁ！", texts)
        self.assertIn("メルちゃんうしろ！うしろ〜！", texts)
        self.assertIn("志村！うしろ！うしろ〜！", texts)

    def test_service_skips_out_of_order_lower_sequence_event(self) -> None:
        service = DogidoService(Settings(audio_enabled=False, llm_enabled=False))

        newer = GameEvent(
            schema_version="2026-05-24",
            adapter="test-adapter",
            observed_at="2026-06-04T12:00:00+09:00",
            sequence=2,
            event=EventDescriptor(
                name=EventName.STATUS_SNAPSHOT,
                source_kind=SourceKind.SYSTEM,
                priority_hint=PriorityHint.BACKGROUND,
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(
                name="player",
                position=Position(x=0, y=64, z=0),
                dimension="minecraft:overworld",
            ),
            world=WorldState(time_phase="day", biome="plains"),
            combat=CombatState(),
        )
        older = GameEvent(
            schema_version="2026-05-24",
            adapter="test-adapter",
            observed_at="2026-06-04T12:00:01+09:00",
            sequence=1,
            event=EventDescriptor(
                name=EventName.THREAT_APPROACHING,
                source_kind=SourceKind.VISUAL,
                priority_hint=PriorityHint.URGENT,
                certainty=Certainty.HIGH,
            ),
            player=PlayerState(
                name="player",
                position=Position(x=0, y=64, z=0),
                dimension="minecraft:the_nether",
            ),
            world=WorldState(time_phase="night", biome="nether_wastes", sky_visible=False, danger_darkness_score=1.0),
            combat=CombatState(combat_active_hint=True),
        )

        first = service.process_event(newer)
        second = service.process_event(older)

        self.assertFalse(first.response.deduplicated)
        self.assertTrue(second.response.deduplicated)
        self.assertEqual(second.actions, [])
        session = next(iter(service.sessions.values()))
        self.assertEqual(session.last_sequence, 2)
        self.assertEqual(session.machine.state.current_dimension, "minecraft:overworld")


if __name__ == "__main__":
    unittest.main()
