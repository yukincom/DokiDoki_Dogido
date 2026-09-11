from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from dogido_server.config import Settings
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
    MetaState,
    PlayerState,
    Position,
    PriorityHint,
    SourceKind,
    SmellObservation,
    TimePhase,
    VisualThreat,
    Weather,
    WorldState,
    ZombieScentClue,
)
from dogido_server.service import DogidoService
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.fallback_catalog import fallback_text


BASE = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
SCENT_WARNING = fallback_text("general", "combat", "zombie_scent_nearby")
NO_SCENT_EVIDENCE = fallback_text("general", "chat", "no_scent_evidence")
CHAT_REPLY = fallback_text("general", "chat", "reply")


def scent_clue(
    entity_id: str = "zombie-1",
    *,
    mob_type: str = "zombie",
) -> ZombieScentClue:
    return ZombieScentClue(
        type=mob_type,  # type: ignore[arg-type]
        entity_id=entity_id,
        distance_band=DistanceBand.CLOSE,
        certainty=Certainty.MEDIUM,
    )


def smell_observation(
    smell_id: str = "bread",
    *,
    category: str = "food",
    valence: str = "pleasant",
    specificity: str = "source",
) -> SmellObservation:
    return SmellObservation(
        status="present",
        smell_id=smell_id,
        category=category,  # type: ignore[arg-type]
        valence=valence,  # type: ignore[arg-type]
        source_kind="hotbar" if specificity == "source" else "mixed",
        specificity=specificity,  # type: ignore[arg-type]
        effective_strength=3,
    )


def make_event(
    *,
    sequence: int,
    at_sec: float = 0.0,
    clues: list[ZombieScentClue] | None = None,
    visual: list[VisualThreat] | None = None,
    auditory: list[AuditoryThreat] | None = None,
    smell: SmellObservation | None = None,
    user_text: str | None = None,
) -> GameEvent:
    return GameEvent(
        schema_version="2026-05-24",
        adapter="test-adapter",
        observed_at=BASE + timedelta(seconds=at_sec),
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
            time_phase=TimePhase.DAY,
            time_of_day=6000,
            weather=Weather.CLEAR,
            biome="forest",
            local_light=15,
            sky_visible=True,
            danger_darkness_score=0.0,
        ),
        visual_threats=list(visual or []),
        auditory_threats=list(auditory or []),
        smell_observation=smell,
        zombie_scent_clues=list(clues or []),
        combat=CombatState(combat_active_hint=False),
        meta=MetaState(user_text=user_text),
    )


class RecordingLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def preload(self) -> bool:
        return False

    def generate_leaf_text(self, _request: object) -> str:
        self.calls += 1
        return self.reply


class ZombieScentSchemaTests(unittest.TestCase):
    def test_schema_accepts_only_bounded_zombie_family_clues(self) -> None:
        for mob_type in ("zombie", "zombie_villager", "husk", "drowned"):
            with self.subTest(mob_type=mob_type):
                clue = scent_clue(mob_type=mob_type)
                self.assertEqual(mob_type, clue.type)

        with self.assertRaises(ValidationError):
            scent_clue(mob_type="skeleton")
        with self.assertRaises(ValidationError):
            ZombieScentClue(
                type="zombie",
                entity_id="zombie-far",
                distance_band=DistanceBand.MID,
                certainty=Certainty.MEDIUM,
            )
        with self.assertRaises(ValidationError):
            ZombieScentClue(
                type="zombie",
                entity_id="zombie-high-certainty",
                distance_band=DistanceBand.CLOSE,
                certainty=Certainty.HIGH,
            )


class ZombieScentStateMachineTests(unittest.TestCase):
    def make_machine(self, **settings: object) -> DogidoStateMachine:
        return DogidoStateMachine(
            Settings(
                decision_policy="py_trees",
                llm_enabled=False,
                audio_enabled=False,
                **settings,
            )
        )

    def test_hidden_unheard_zombie_emits_one_noncombat_warning(self) -> None:
        machine = self.make_machine()

        result = machine.process(make_event(sequence=1, clues=[scent_clue()]))

        scent_actions = [
            action for action in result.actions if action.cue_id == "zombie_scent_warning"
        ]
        self.assertEqual(1, len(scent_actions))
        self.assertEqual(SCENT_WARNING, scent_actions[0].text)
        self.assertFalse(scent_actions[0].interrupt)
        self.assertEqual("normal", result.state.mode)
        self.assertFalse(result.combat_active)

    def test_same_present_zombie_does_not_repeat(self) -> None:
        machine = self.make_machine()

        first = machine.process(make_event(sequence=1, clues=[scent_clue()]))
        second = machine.process(make_event(sequence=2, at_sec=1, clues=[scent_clue()]))

        self.assertTrue(any(action.cue_id == "zombie_scent_warning" for action in first.actions))
        self.assertFalse(any(action.cue_id == "zombie_scent_warning" for action in second.actions))

    def test_reentry_waits_for_global_cooldown_then_warns(self) -> None:
        machine = self.make_machine(zombie_scent_comment_cooldown_ms=120_000)
        machine.process(make_event(sequence=1, clues=[scent_clue()]))
        machine.process(make_event(sequence=2, at_sec=10, clues=[]))

        early = machine.process(make_event(sequence=3, at_sec=60, clues=[scent_clue()]))
        ready = machine.process(make_event(sequence=4, at_sec=121, clues=[scent_clue()]))

        self.assertFalse(any(action.cue_id == "zombie_scent_warning" for action in early.actions))
        self.assertTrue(any(action.cue_id == "zombie_scent_warning" for action in ready.actions))

    def test_visual_or_auditory_observation_suppresses_scent_path(self) -> None:
        visual = VisualThreat(
            type="zombie",
            entity_id="zombie-1",
            distance=5.0,
            direction=Direction(),
            certainty=Certainty.HIGH,
        )
        auditory = AuditoryThreat(
            label="hostile_voice_like",
            source_id="zombie-1",
            distance_band=DistanceBand.CLOSE,
            certainty=Certainty.MEDIUM,
        )

        for kwargs in ({"visual": [visual]}, {"auditory": [auditory]}):
            with self.subTest(kwargs=kwargs):
                result = self.make_machine().process(
                    make_event(sequence=1, clues=[scent_clue()], **kwargs)
                )
                self.assertFalse(
                    any(action.cue_id == "zombie_scent_warning" for action in result.actions)
                )

    def test_other_visible_threat_does_not_reset_same_scent_presence(self) -> None:
        machine = self.make_machine(zombie_scent_comment_cooldown_ms=120_000)
        other_visual = VisualThreat(
            type="skeleton",
            entity_id="skeleton-1",
            distance=12.0,
            direction=Direction(),
            certainty=Certainty.HIGH,
        )

        machine.process(make_event(sequence=1, clues=[scent_clue()]))
        machine.process(
            make_event(
                sequence=2,
                at_sec=60,
                clues=[scent_clue()],
                visual=[other_visual],
            )
        )
        self.assertEqual(
            "source:zombie:decay:unpleasant",
            machine.state.announced_smell_signature,
        )
        still_present = machine.process(
            make_event(sequence=3, at_sec=121, clues=[scent_clue()])
        )

        self.assertFalse(
            any(action.cue_id == "zombie_scent_warning" for action in still_present.actions)
        )

    def test_player_reply_runs_first_and_scent_remains_pending(self) -> None:
        machine = self.make_machine()

        chat = machine.process(
            make_event(sequence=1, clues=[scent_clue()], user_text="森、暗いな")
        )
        scent = machine.process(make_event(sequence=2, at_sec=1, clues=[scent_clue()]))

        self.assertEqual([CHAT_REPLY], [action.text for action in chat.actions if action.text])
        self.assertTrue(any(action.cue_id == "zombie_scent_warning" for action in scent.actions))


class GeneralSmellStateMachineTests(unittest.TestCase):
    def make_machine(self, **settings: object) -> DogidoStateMachine:
        return DogidoStateMachine(
            Settings(
                decision_policy="py_trees",
                llm_enabled=False,
                audio_enabled=False,
                **settings,
            )
        )

    def test_present_shape_is_closed_and_non_present_rejects_source_fields(self) -> None:
        self.assertEqual("present", smell_observation().status)
        with self.assertRaises(ValidationError):
            SmellObservation(status="present", smell_id="bread")
        with self.assertRaises(ValidationError):
            SmellObservation(status="none", smell_id="bread")
        with self.assertRaises(ValidationError):
            SmellObservation(status="suppressed")
        with self.assertRaises(ValidationError):
            SmellObservation(
                status="present",
                smell_id="unknown_smell",
                category="food",
                valence="pleasant",
                source_kind="hotbar",
                specificity="source",
                effective_strength=3,
            )
        with self.assertRaises(ValidationError):
            SmellObservation.model_validate(
                {
                    **smell_observation().model_dump(),
                    "source_id": "must-not-cross-adapter-boundary",
                }
            )
        with self.assertRaises(ValidationError):
            SmellObservation.model_validate(
                {
                    **smell_observation("bread").model_dump(),
                    "category": "decay",
                }
            )
        with self.assertRaises(ValidationError):
            SmellObservation.model_validate(
                {
                    **smell_observation("bread").model_dump(),
                    "smell_id": "zombie",
                    "category": "decay",
                    "valence": "unpleasant",
                    "source_kind": "hotbar",
                }
            )
        with self.assertRaises(ValidationError):
            SmellObservation(
                status="present",
                smell_id="rain_after",
                category="rain_after",
                valence="pleasant",
                source_kind="block",
                specificity="source",
                effective_strength=1,
            )

    def test_new_observation_requires_two_matching_snapshots(self) -> None:
        machine = self.make_machine()
        first = machine.process(
            make_event(sequence=1, smell=smell_observation("bread"))
        )
        second = machine.process(
            make_event(sequence=2, at_sec=1, smell=smell_observation("bread"))
        )
        third = machine.process(
            make_event(sequence=3, at_sec=2, smell=smell_observation("bread"))
        )

        self.assertFalse(any(action.cue_id == "smell_bread" for action in first.actions))
        self.assertEqual(
            [fallback_text("general", "smell", "bread")],
            [action.text for action in second.actions if action.cue_id == "smell_bread"],
        )
        self.assertFalse(any(action.cue_id == "smell_bread" for action in third.actions))

    def test_one_frame_winner_flap_never_speaks(self) -> None:
        machine = self.make_machine()
        bread = machine.process(
            make_event(sequence=1, smell=smell_observation("bread"))
        )
        cookie = machine.process(
            make_event(sequence=2, at_sec=1, smell=smell_observation("cookie"))
        )

        self.assertFalse(
            any((action.cue_id or "").startswith("smell_") for action in bread.actions)
        )
        self.assertFalse(
            any((action.cue_id or "").startswith("smell_") for action in cookie.actions)
        )

    def test_non_zombie_smell_waits_behind_player_input_priority(self) -> None:
        machine = self.make_machine()
        observation = smell_observation("bread")

        chat = machine.process(
            make_event(
                sequence=1,
                smell=observation,
                user_text="森、暗いな",
            )
        )
        muted = machine.process(make_event(sequence=2, at_sec=1, smell=observation))
        ready = machine.process(make_event(sequence=3, at_sec=21, smell=observation))

        self.assertEqual([CHAT_REPLY], [action.text for action in chat.actions if action.text])
        self.assertFalse(any(action.cue_id == "smell_bread" for action in muted.actions))
        self.assertTrue(any(action.cue_id == "smell_bread" for action in ready.actions))

    def test_none_and_suppressed_clear_presence_after_two_without_speech(self) -> None:
        for observation in (
            SmellObservation(status="none"),
            SmellObservation(status="suppressed", suppression_reason="rain"),
        ):
            with self.subTest(status=observation.status):
                machine = self.make_machine()
                machine.process(make_event(sequence=1, smell=smell_observation()))
                machine.process(make_event(sequence=2, at_sec=1, smell=smell_observation()))
                first_absent = machine.process(
                    make_event(sequence=3, at_sec=2, smell=observation)
                )
                self.assertIsNotNone(machine.state.active_smell_signature)
                result = machine.process(
                    make_event(sequence=4, at_sec=3, smell=observation)
                )
                self.assertIsNone(machine.state.active_smell_signature)
                self.assertFalse(
                    any(
                        (action.cue_id or "").startswith("smell_")
                        for action in first_absent.actions + result.actions
                    )
                )

    def test_category_and_cross_category_ties_use_fixed_general_speech(self) -> None:
        cases = (
            (
                smell_observation(
                    "flower",
                    category="flower",
                    valence="mixed",
                    specificity="category",
                ),
                "smell_flower_mixed",
                fallback_text("general", "smell", "flower_mixed"),
            ),
            (
                smell_observation(
                    "mixed",
                    category="mixed",
                    valence="mixed",
                    specificity="mixed",
                ),
                "smell_mixed",
                fallback_text("general", "smell", "mixed"),
            ),
        )
        for observation, cue_id, expected_text in cases:
            with self.subTest(smell_id=observation.smell_id):
                machine = self.make_machine()
                machine.process(make_event(sequence=1, smell=observation))
                result = machine.process(make_event(sequence=2, at_sec=1, smell=observation))
                self.assertEqual(
                    [expected_text],
                    [action.text for action in result.actions if action.cue_id == cue_id],
                )

    def test_same_smell_reentry_waits_for_general_cooldown(self) -> None:
        machine = self.make_machine(smell_comment_cooldown_ms=120_000)
        observation = smell_observation("bread")
        machine.process(make_event(sequence=1, smell=observation))
        machine.process(make_event(sequence=2, at_sec=1, smell=observation))
        machine.process(make_event(sequence=3, at_sec=10, smell=SmellObservation(status="none")))
        machine.process(make_event(sequence=4, at_sec=11, smell=SmellObservation(status="none")))
        machine.process(make_event(sequence=5, at_sec=60, smell=observation))
        early = machine.process(make_event(sequence=6, at_sec=61, smell=observation))
        machine.process(make_event(sequence=7, at_sec=122, smell=SmellObservation(status="none")))
        machine.process(make_event(sequence=8, at_sec=123, smell=SmellObservation(status="none")))
        machine.process(make_event(sequence=9, at_sec=124, smell=observation))
        ready = machine.process(make_event(sequence=10, at_sec=125, smell=observation))

        self.assertFalse(any(action.cue_id == "smell_bread" for action in early.actions))
        self.assertTrue(any(action.cue_id == "smell_bread" for action in ready.actions))


class PlayerChatOlfactoryGroundingTests(unittest.TestCase):
    def make_machine(self, llm: RecordingLLM) -> DogidoStateMachine:
        return DogidoStateMachine(
            Settings(decision_policy="py_trees", llm_enabled=True, audio_enabled=False),
            llm=llm,  # type: ignore[arg-type]
        )

    def test_generic_chat_rejects_spontaneous_smell_invention(self) -> None:
        llm = RecordingLLM("石の切れ端とか、変な匂いがするわ。")
        result = self.make_machine(llm).process(
            make_event(sequence=1, user_text="ここにはいろんなものがあるな")
        )

        self.assertEqual(1, llm.calls)
        self.assertEqual([CHAT_REPLY], [action.text for action in result.actions if action.text])

        adjective_llm = RecordingLLM("なんやここ、生臭いな。")
        adjective_result = self.make_machine(adjective_llm).process(
            make_event(sequence=1, user_text="ここにはいろんなものがあるな")
        )
        self.assertEqual(
            [CHAT_REPLY],
            [action.text for action in adjective_result.actions if action.text],
        )

    def test_smell_question_without_clue_returns_fixed_unknown_without_model(self) -> None:
        llm = RecordingLLM("湿った土と腐った葉っぱの匂いや。")
        result = self.make_machine(llm).process(
            make_event(sequence=1, user_text="さっきの臭いって何？")
        )

        self.assertEqual(0, llm.calls)
        self.assertEqual(
            [NO_SCENT_EVIDENCE],
            [action.text for action in result.actions if action.text],
        )

    def test_explicit_none_and_suppressed_are_distinct_fixed_answers(self) -> None:
        llm = RecordingLLM("モデルには聞かない")
        none = self.make_machine(llm).process(
            make_event(
                sequence=1,
                user_text="今ここ、何の匂い？",
                smell=SmellObservation(status="none"),
            )
        )
        submerged = self.make_machine(llm).process(
            make_event(
                sequence=1,
                user_text="今ここ、何の匂い？",
                smell=SmellObservation(
                    status="suppressed",
                    suppression_reason="submerged",
                ),
            )
        )

        self.assertEqual(0, llm.calls)
        self.assertEqual(
            [fallback_text("general", "smell", "none")],
            [action.text for action in none.actions if action.text],
        )
        self.assertEqual(
            [fallback_text("general", "smell", "suppressed_submerged")],
            [action.text for action in submerged.actions if action.text],
        )

    def test_present_smell_answer_is_fixed_and_new_observation_wins_over_legacy(self) -> None:
        llm = RecordingLLM("モデルには聞かない")
        machine = self.make_machine(llm)
        observation = smell_observation("bread")

        answer = machine.process(
            make_event(
                sequence=1,
                user_text="今ここ、何の匂い？",
                smell=observation,
                clues=[scent_clue()],
            )
        )
        followup = machine.process(
            make_event(
                sequence=2,
                at_sec=1,
                smell=observation,
                clues=[scent_clue()],
            )
        )

        self.assertEqual(0, llm.calls)
        self.assertEqual(
            [fallback_text("general", "smell", "bread")],
            [action.text for action in answer.actions if action.text],
        )
        self.assertFalse(
            any(
                action.cue_id in {"smell_bread", "zombie_scent_warning"}
                for action in followup.actions
            )
        )

    def test_player_composter_guess_does_not_license_assistant_smell(self) -> None:
        llm = RecordingLLM("コンポスターの匂いやと思うで。")
        result = self.make_machine(llm).process(
            make_event(sequence=1, user_text="コンポスターかもしれんな")
        )

        self.assertEqual([CHAT_REPLY], [action.text for action in result.actions if action.text])

    def test_hypothetical_or_poetic_smell_talk_is_not_treated_as_sensor_claim(self) -> None:
        hypothetical = RecordingLLM("ゾンビはたしかに臭そうやな。")
        hypothetical_result = self.make_machine(hypothetical).process(
            make_event(sequence=1, user_text="ゾンビって臭そうやな")
        )
        self.assertEqual(
            [hypothetical.reply],
            [action.text for action in hypothetical_result.actions if action.text],
        )

        invented_current = RecordingLLM("今ここにゾンビの匂いがするで。")
        invented_current_result = self.make_machine(invented_current).process(
            make_event(sequence=1, user_text="ゾンビって臭そうやな")
        )
        self.assertEqual(
            [CHAT_REPLY],
            [action.text for action in invented_current_result.actions if action.text],
        )

        poetic = RecordingLLM("この句は土の匂いがするな。")
        poetic_result = self.make_machine(poetic).process(
            make_event(sequence=1, user_text="この句、土の匂いがするな")
        )
        self.assertEqual(
            [poetic.reply],
            [action.text for action in poetic_result.actions if action.text],
        )

        poetic_with_clue = RecordingLLM("この句は土の匂いがするな。")
        poetic_with_clue_result = self.make_machine(poetic_with_clue).process(
            make_event(
                sequence=1,
                clues=[scent_clue()],
                user_text="この句、土の匂いがするな",
            )
        )
        self.assertEqual(
            [poetic_with_clue.reply],
            [action.text for action in poetic_with_clue_result.actions if action.text],
        )

    def test_scent_question_with_clue_uses_fixed_zombie_reply_without_model(self) -> None:
        llm = RecordingLLM("腐った葉っぱとゾンビピグリンの匂いや。")
        result = self.make_machine(llm).process(
            make_event(
                sequence=1,
                clues=[scent_clue()],
                user_text="この臭いって何？",
            )
        )
        self.assertEqual(0, llm.calls)
        self.assertEqual(
            [SCENT_WARNING],
            [action.text for action in result.actions if action.text],
        )

        deictic = self.make_machine(llm).process(
            make_event(
                sequence=2,
                clues=[scent_clue("zombie-2")],
                user_text="この匂い、どんな感じ？",
            )
        )
        self.assertEqual(
            [SCENT_WARNING],
            [action.text for action in deictic.actions if action.text],
        )

    def test_scent_answer_marks_clue_announced_without_followup_repeat(self) -> None:
        llm = RecordingLLM("ゾンビの匂いや。近くにおるで。")
        machine = self.make_machine(llm)

        answer = machine.process(
            make_event(
                sequence=1,
                clues=[scent_clue()],
                user_text="この臭いって何？",
            )
        )
        followup = machine.process(
            make_event(sequence=2, at_sec=1, clues=[scent_clue()])
        )

        self.assertEqual(
            [SCENT_WARNING],
            [action.text for action in answer.actions if action.text],
        )
        self.assertEqual(0, llm.calls)
        self.assertFalse(
            any(action.cue_id == "zombie_scent_warning" for action in followup.actions)
        )

    def test_unrelated_chat_cannot_consume_clue_through_model(self) -> None:
        llm = RecordingLLM("ゾンビの匂いがするで。")
        machine = self.make_machine(llm)

        chat = machine.process(
            make_event(
                sequence=1,
                clues=[scent_clue()],
                user_text="森って落ち着くな",
            )
        )
        followup = machine.process(
            make_event(sequence=2, at_sec=1, clues=[scent_clue()])
        )

        self.assertEqual([CHAT_REPLY], [action.text for action in chat.actions if action.text])
        self.assertTrue(
            any(action.cue_id == "zombie_scent_warning" for action in followup.actions)
        )

    def test_prompt_states_olfactory_boundary(self) -> None:
        no_clue = "\n".join(
            message["content"]
            for message in build_messages(
                LeafGenerationRequest(
                    kind="player_chat",
                    fallback_text=CHAT_REPLY,
                    details={"user_text": "こんにちは", "mode": "normal"},
                )
            )
        )
        injected_clue = "\n".join(
            message["content"]
            for message in build_messages(
                LeafGenerationRequest(
                    kind="player_chat",
                    fallback_text=SCENT_WARNING,
                    details={
                        "user_text": "この臭い何？",
                        "mode": "normal",
                        "zombie_scent_clue_types": ["zombie"],
                    },
                )
            )
        )

        self.assertIn("嗅覚観測へ昇格させない", no_clue)
        self.assertNotIn("【限定嗅覚手掛かり】", no_clue)
        self.assertIn("嗅覚観測へ昇格させない", injected_clue)
        self.assertNotIn("【限定嗅覚手掛かり】", injected_clue)


class ZombieScentEpisodeTests(unittest.TestCase):
    def test_episode_records_scent_observation_separately_from_combat(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = DogidoService(
                Settings(
                    audio_enabled=False,
                    llm_enabled=False,
                    memory_enabled=True,
                    memory_dir=root,
                    decision_policy="py_trees",
                )
            )

            processed = service.process_event(
                make_event(sequence=1, clues=[scent_clue()])
            )

            row = json.loads(
                (root / "eval" / "episodes.jsonl").read_text(encoding="utf-8").strip()
            )
            observation = row["observation"]
            self.assertEqual(1, observation["zombie_scent_clues"]["count"])
            self.assertEqual("zombie", observation["zombie_scent_clues"]["items"][0]["type"])
            self.assertEqual("normal", row["decision"]["mode_after"])
            self.assertFalse(row["decision"]["combat_active"])
            self.assertTrue(
                any(
                    item.get("cue_id") == "zombie_scent_warning"
                    for item in row["action"]["items"]
                )
            )
            self.assertTrue(processed.actions)


if __name__ == "__main__":
    unittest.main()
