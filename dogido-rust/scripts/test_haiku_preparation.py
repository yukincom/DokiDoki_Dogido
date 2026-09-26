#!/usr/bin/env python3
"""Pure preparation contract tests; --python-root selects the reference checkout."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import importlib
import json
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--python-root", type=Path, default=Path(__file__).resolve().parents[2])
_args, _unittest_args = _parser.parse_known_args()
sys.path.insert(0, str(_args.python_root.resolve()))

from dogido_server.config import Settings
from dogido_server.dialogue.foreground import ForegroundDialogue
from dogido_server.llm import DogidoLLM
from dogido_server.models import GameEvent
from dogido_server.state_machine import DogidoStateMachine
from haiku_preparation import HaikuPreparation, compose_inspiration_speech


def event_payload() -> dict:
    return {
        "schema_version": "2026-05-24", "game": "minecraft-java", "adapter": "test",
        "observed_at": "2026-09-26T10:00:00+09:00", "sequence": 34,
        "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "normal", "certainty": "high"},
        "player": {"name": "player", "dimension": "minecraft:overworld",
                   "position": {"x": 0, "y": 112, "z": 0}, "held_item": "minecraft:cherry_leaves"},
        "world": {"biome": "minecraft:cherry_grove", "time_phase": "morning",
                  "weather": "clear", "sky_visible": True},
        "inventory": {"minecraft:cherry_leaves": 1, "minecraft:netherite_axe": 1},
    }


def irony_payload() -> dict:
    return {"found": True, "kind": "juxtaposition", "description": "サクラの葉と黒い斧の対比。",
            "elements": ["サクラの葉", "ネザライトの斧"], "focus": ["サクラの葉"], "confidence": 0.9}


def scene_payload(request: dict) -> dict:
    atoms = request["details"]["source_atoms"]
    return {"found": True, "clauses": [{"text": "サクラの葉と斧が並ぶ",
            "basis_atom_ids": [row["atom_id"] for row in atoms[:2]], "claim_class": "interpretive"}],
            "motifs": ["サクラの葉", "斧"], "focus": ["サクラの葉"], "confidence": 0.9}


def native_result(materials: dict) -> dict:
    verse = ["さくらのは", "くろいおのへと", "あさのいろ"]
    by_id = {row["atom_id"]: row for row in materials["input"]["source_atoms"]}
    atoms = [by_id[key] for key in ("item:cherry_leaves:japanese", "item:netherite_axe:note:0",
                                   "observation:observed:time_phase")]
    return {
        "accepted": True, "text": "\n".join(verse), "failure_reason": None,
        "generation_strategy": "three_slot", "regeneration_rounds": 2, "prompt_variant": "three_slot",
        "line_sources": [{"line_index": index, "text": line,
                         "atom_ids": [atoms[index]["atom_id"]], "sources": [atoms[index]]}
                        for index, line in enumerate(verse)],
    }


class CannedReference:
    def __init__(self, replies: list[dict]):
        self.replies = deepcopy(replies)
        self.requests = []

    def generate_structured_json(self, request):
        self.requests.append(asdict(request))
        if not self.replies:
            raise AssertionError("unexpected reference inference")
        return self.replies.pop(0)

    def enabled(self):
        return True


class HaikuPreparationTests(unittest.TestCase):
    def setUp(self):
        self.event = event_payload()
        self.settings = {"llm_enabled": True, "tts_reading_engine": "off",
                         "haiku_structured_max_tokens": 192, "haiku_grounding_max_tokens": 512,
                         "haiku_max_regeneration_rounds": 6, "haiku_generation_strategy": "three_slot"}
        self.guard_patches = [
            patch.object(DogidoStateMachine, "process", side_effect=AssertionError("process forbidden")),
            patch.object(DogidoLLM, "__init__", side_effect=AssertionError("frontend forbidden")),
            patch("socket.create_connection", side_effect=AssertionError("network forbidden")),
            patch("dogido_server.state_machine.mixins.haiku.generate_grounded_haiku",
                  side_effect=AssertionError("generation forbidden")),
            patch("dogido_server.memory.MemoryStore.__init__", side_effect=AssertionError("storage forbidden")),
        ]
        for guard in self.guard_patches:
            guard.start()
            self.addCleanup(guard.stop)

    def start(self, **extra):
        helper = HaikuPreparation()
        frame = {"op": "haiku_context", "event": self.event, "settings": self.settings, **extra}
        response = helper.handle(frame)
        return helper, response

    def prepared(self, **extra):
        helper, context = self.start(**extra)
        inspiration = helper.handle({"op": "haiku_inspiration", "payload": irony_payload()})
        materials = helper.handle({"op": "haiku_materials", "payload": scene_payload(inspiration["request"])})
        return helper, context, inspiration, materials

    def test_request_defaults_and_generated_prelude(self):
        helper, context = self.start()
        self.assertIsNone(context["fixed_text"])
        self.assertEqual(context["request"]["kind"], "haiku_irony")
        self.assertEqual(context["request"]["route"], "chat")
        self.assertEqual(context["request"]["temperature"], 0.15)
        self.assertEqual(context["request"]["max_tokens"], 192)
        inspiration = helper.handle({"op": "haiku_inspiration", "payload": irony_payload()})
        self.assertEqual(inspiration["text"], "サクラの葉と黒い斧の対比。")
        self.assertEqual(inspiration["spoken_text"], "サクラの葉と黒い斧の対比、なんか浮かんできたわ。")
        self.assertNotIn("ちょっと待って", inspiration["spoken_text"])
        self.assertEqual(inspiration["request"]["kind"], "haiku_scene")
        self.assertEqual(inspiration["request"]["temperature"], 0.2)

    def test_current_python_preparation_and_emission_parity(self):
        helper, context, inspiration, materials = self.prepared(
            dialogue_material={"summary": "プレイヤーは枝の話をしていた", "motifs": ["枝"], "source_turn_ids": ["t1"]},
            lessons=[{"note": "自然な語順が好き", "polarity": "tighten"}],
        )
        event = GameEvent.model_validate(self.event)
        llm = CannedReference([irony_payload(), scene_payload(inspiration["request"])])
        reference = DogidoStateMachine(Settings(_env_file=None, audio_enabled=False, memory_enabled=False,
                                                decision_policy="legacy", **self.settings), llm=llm)
        reference.haiku_lessons_provider = lambda: [{"note": "自然な語順が好き", "polarity": "tighten"}]
        reference.foreground_dialogue_provider = lambda: {"route": "casual", "casual_haiku_material": {
            "summary": "プレイヤーは枝の話をしていた", "motifs": ["枝"], "source_turn_ids": ["t1"]}}
        # Clean HEAD predates the generated-prelude composer; all other material
        # selection, source binding, and emission projection remain reference code.
        reference._compose_haiku_preface_speech = lambda irony: compose_inspiration_speech(
            found=irony.found, description=irony.description)
        with patch("dogido_server.state_machine.mixins.haiku.CASUAL_HAIKU_PREFACE",
                   inspiration["spoken_text"], create=True):
            reference._begin_prefaced_haiku(event, event.observed_at)
        reference._prepare_pending_haiku_generation(event)
        self.assertEqual(llm.requests, [context["request"], inspiration["request"]])
        self.assertEqual(materials["input"]["details"], reference._pending_haiku_prompt_details)
        self.assertEqual(materials["input"]["source_atoms"],
                         [atom.to_prompt_dict() for atom in reference._pending_haiku_source_atoms])
        self.assertEqual(materials["materials"], reference._pending_haiku_materials)
        result = native_result(materials)
        reference._pending_haiku_materials.update({key: deepcopy(result[key]) for key in (
            "line_sources", "generation_strategy", "regeneration_rounds", "prompt_variant")})
        reference._remember_haiku_emission(event, event.observed_at, result["text"], route="haiku")
        expected = asdict(reference.emitted_haiku)
        del expected["created_at"]
        expected["lines"] = [line.to_dict() for line in reference.emitted_haiku.lines]
        self.assertEqual(helper.handle({"op": "haiku_emission", "result": result}), expected)

    def test_one_snapshot_and_original_event_through_emission(self):
        original = DogidoStateMachine._haiku_context
        observed = []
        def capture(machine, event):
            observed.append(event.model_dump(mode="json"))
            return original(machine, event)
        with patch.object(DogidoStateMachine, "_haiku_context", capture):
            helper, context = self.start()
            self.event["sequence"] = 999
            self.event["player"]["dimension"] = "minecraft:the_nether"
            self.event["world"]["biome"] = "minecraft:desert"
            context["request"]["details"]["source_atoms"].clear()
            inspiration = helper.handle({"op": "haiku_inspiration", "payload": irony_payload()})
            materials = helper.handle({"op": "haiku_materials", "payload": {"found": False}})
            emission = helper.handle({"op": "haiku_emission", "result": native_result(materials)})
        self.assertEqual(len(observed), 1)
        self.assertTrue(inspiration["request"]["details"]["source_atoms"])
        self.assertEqual(emission["event_sequence"], 34)
        self.assertEqual(emission["dimension"], "minecraft:overworld")
        self.assertEqual(emission["biome"], "cherry_grove")
        self.assertNotIn("created_at", emission)

    def test_invalid_domains_never_switch_to_fixed_catalog(self):
        for irony in (None, [], {}, {"found": False}, {"found": True, "elements": 7}):
            for scene in (None, [], {}, {"found": False}, {"found": True, "motifs": 7},
                          {"text": "雨が降る", "basis_atom_ids": ["invented"], "claim_class": "factual"}):
                with self.subTest(irony=irony, scene=scene):
                    helper, context = self.start()
                    helper.handle({"op": "haiku_inspiration", "payload": irony})
                    materials = helper.handle({"op": "haiku_materials", "payload": scene})
                    self.assertIsNone(context["fixed_text"])
                    self.assertTrue(materials["input"]["llm_enabled"])
                    self.assertTrue(materials["input"]["source_atoms"])
                    self.assertIsNone(materials["input"]["details"]["scene"])

    def test_spoken_irony_is_rebound_to_primary_sources(self):
        _, _, inspiration, materials = self.prepared()
        self.assertEqual(materials["interpretation_origin"], "spoken_preface")
        self.assertEqual(materials["materials"]["preface_spoken"], inspiration["spoken_text"])
        scene = materials["input"]["details"]["scene"]
        self.assertEqual(scene["spoken_text"], "サクラの葉と黒い斧の対比。")
        atoms = materials["input"]["source_atoms"]
        by_id = {row["atom_id"]: row for row in atoms}
        derived = [row for row in atoms if row["basis_atom_ids"]]
        self.assertTrue(derived)
        for row in derived:
            for basis in row["basis_atom_ids"]:
                self.assertIn(basis, by_id)
                self.assertFalse(by_id[basis]["basis_atom_ids"])

    def test_unspoken_scene_does_not_claim_spoken_origin(self):
        helper, _ = self.start()
        inspiration = helper.handle({"op": "haiku_inspiration", "payload": {"found": False}})
        materials = helper.handle({"op": "haiku_materials", "payload": scene_payload(inspiration["request"])})
        self.assertEqual(materials["interpretation_origin"], "generated_unspoken")
        self.assertFalse(any(row["basis_atom_ids"] for row in materials["input"]["source_atoms"]))
        self.assertIsNotNone(materials["input"]["details"]["scene"])

    def test_disabled_llm_skips_all_requests_and_requires_its_fixed_text(self):
        self.settings["llm_enabled"] = False
        helper, context = self.start()
        self.assertIsNone(context["request"])
        self.assertTrue(context["fixed_text"])
        with self.assertRaises(ValueError):
            helper.handle({"op": "haiku_inspiration", "payload": irony_payload()})
        with self.assertRaises(ValueError):
            helper.handle({"op": "haiku_emission", "result": {"accepted": True, "text": "別の句"}})
        emission = helper.handle({"op": "haiku_emission", "result": {
            "accepted": True, "text": context["fixed_text"]}})
        self.assertTrue(emission["materials"]["source_atoms"])
        self.assertNotIn("created_at", emission)

    def test_failed_native_result_cannot_emit_or_fall_back(self):
        helper, context, _, materials = self.prepared()
        result = native_result(materials)
        for wrong in ({**result, "accepted": False}, {**result, "accepted": 1},
                      {**result, "text": ""}, {**result, "text": context["fallback_text"]}):
            with self.subTest(result=wrong):
                with self.assertRaises(ValueError):
                    helper.handle({"op": "haiku_emission", "result": wrong})
        self.assertTrue(helper.handle({"op": "haiku_emission", "result": result})["lines"])

    def test_sources_reading_and_fragment_links_survive_projection(self):
        helper, _, _, materials = self.prepared()
        result = native_result(materials)
        emission = helper.handle({"op": "haiku_emission", "result": result})
        self.assertEqual(len(emission["lines"]), 3)
        self.assertEqual(emission["reading_text"], result["text"])
        self.assertEqual(emission["surface_text"], result["text"])
        self.assertEqual(emission["materials"]["regeneration_rounds"], 2)
        self.assertIn("fragment_links", emission["materials"])
        for index, row in enumerate(emission["lines"]):
            self.assertEqual(row["source_atom_ids"], result["line_sources"][index]["atom_ids"])
            self.assertEqual(row["source_atoms"], result["line_sources"][index]["sources"])
            self.assertEqual(row["line_id"], f"line_{index + 1}")
        json.dumps(emission, ensure_ascii=False)

    def test_soft_lessons_and_dialogue_do_not_become_hard_constraints(self):
        _, _, _, materials = self.prepared(
            lessons=[{"note": "雨は使わないで", "forbidden_fragments": ["あめ"], "polarity": "tighten"},
                     {"note": "解除済み", "polarity": "loosen"}],
            dialogue_material={"summary": "プレイヤーが雨の話をしていた", "motifs": ["雨"], "source_turn_ids": ["t1"]})
        constraints = materials["input"]["details"]["haiku_constraints"]
        self.assertEqual(constraints["player_lessons"], ["雨は使わないで"])
        self.assertNotIn("あめ", constraints["forbidden_terms"])
        dialogue_atoms = [row for row in materials["input"]["source_atoms"] if row["kind"] == "dialogue_material"]
        self.assertTrue(dialogue_atoms)
        self.assertTrue(all(row["claim_scopes"] == ["player_reported_context"] for row in dialogue_atoms))
        self.assertEqual(materials["materials"]["player_dialogue_material"]["constraint"], "soft")

    def test_completed_turn_material_matches_foreground_and_explicit_override(self):
        turns = [{"turn_id": f"t{i}", "player_text": f"今日は雨{i}についてずっと考えていましたね", "dogido_text": "そうやな"}
                 for i in range(4)]
        reference = ForegroundDialogue()
        event = GameEvent.model_validate(self.event)
        for turn in turns:
            reference.note_completed_turn(**turn, route="casual", at=event.observed_at)
        reference.activate("casual", now=event.observed_at)
        _, _, _, materials = self.prepared(completed_turns=turns)
        self.assertEqual(materials["input"]["details"]["player_dialogue_material"], reference.casual_haiku_material())
        self.assertEqual(materials["materials"]["player_dialogue_material"]["source_turn_ids"], ["t1", "t2", "t3"])
        _, _, _, explicit = self.prepared(completed_turns=turns, dialogue_material={})
        self.assertNotIn("player_dialogue_material", explicit["input"]["details"])

    def test_lifecycle_and_returned_data_are_isolated(self):
        helper = HaikuPreparation()
        for op in ("haiku_inspiration", "haiku_materials", "haiku_emission", "unknown"):
            with self.assertRaises(ValueError):
                helper.handle({"op": op})
        helper, _, _, materials = self.prepared()
        materials["materials"]["preface_spoken"] = "caller mutation"
        result = native_result(materials)
        emission = helper.handle({"op": "haiku_emission", "result": result})
        self.assertNotEqual(emission["materials"]["preface_spoken"], "caller mutation")
        for op in ("haiku_context", "haiku_inspiration", "haiku_materials", "haiku_emission"):
            with self.assertRaises(ValueError):
                helper.handle({"op": op, "event": self.event, "result": result})

    def test_strategy_settings_forward_to_native_input(self):
        self.settings.update(haiku_structured_max_tokens=300, haiku_grounding_max_tokens=640,
                             haiku_max_regeneration_rounds=8, haiku_generation_strategy="one_plus_two")
        _, context, _, materials = self.prepared()
        self.assertEqual(context["request"]["max_tokens"], 300)
        self.assertEqual(materials["input"]["max_tokens"], 300)
        self.assertEqual(materials["input"]["grounding_max_tokens"], 640)
        self.assertEqual(materials["input"]["max_regeneration_rounds"], 8)
        self.assertEqual(materials["input"]["generation_strategy"], "one_plus_two")

    def test_composer_matches_current_prelude_if_available(self):
        cases = [(True, "サクラがきれいや。"), (True, "なんか思いついたわ"),
                 (True, "浮かんだ！"), (False, "使わない"), (True, ""), (True, "あめ!? ")]
        try:
            current = importlib.import_module("dogido_server.haiku.prelude").compose_inspiration_speech
        except ModuleNotFoundError:
            current = None
        for found, description in cases:
            output = compose_inspiration_speech(found=found, description=description)
            self.assertNotIn("ちょっと待って", output)
            if current is not None:
                self.assertEqual(output, current(found=found, description=description))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(argv=[sys.argv[0], *_unittest_args])
