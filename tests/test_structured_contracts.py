from __future__ import annotations

import ast
import unittest
from pathlib import Path

from dogido_server.llm.prompts import build_messages
from dogido_server.llm.structured_contracts import (
    STRUCTURED_CONTRACT_KINDS,
    validate_structured_payload,
)
from dogido_server.llm.types import StructuredGenerationRequest


class StructuredContractTests(unittest.TestCase):
    def test_every_production_structured_request_has_a_registered_contract(self) -> None:
        root = Path(__file__).resolve().parents[1] / "dogido_server"
        request_kinds: set[str] = set()
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else ""
                )
                if name != "StructuredGenerationRequest":
                    continue
                kind_keyword = next(
                    (keyword for keyword in node.keywords if keyword.arg == "kind"),
                    None,
                )
                if (
                    kind_keyword is not None
                    and isinstance(kind_keyword.value, ast.Constant)
                    and isinstance(kind_keyword.value.value, str)
                ):
                    request_kinds.add(kind_keyword.value.value)

        self.assertEqual(request_kinds, set(STRUCTURED_CONTRACT_KINDS))
        self.assertNotIn("haiku_workshop_material_pick", request_kinds)

    def test_every_registered_contract_has_a_prompt_builder(self) -> None:
        for kind in sorted(STRUCTURED_CONTRACT_KINDS):
            with self.subTest(kind=kind):
                messages = build_messages(
                    StructuredGenerationRequest(
                        kind=kind,
                        fallback_value={},
                        details={},
                    )
                )
                self.assertEqual(
                    [message["role"] for message in messages],
                    ["system", "user"],
                )
                self.assertTrue(all(message["content"].strip() for message in messages))

    def test_current_payloads_cover_all_registered_contracts(self) -> None:
        atom = {"atom_id": "observation:test:0", "text": "春の風"}
        close = {
            "found": False,
            "scope": "unknown",
            "evidence": "",
            "confidence": 0.0,
        }
        cases: dict[str, tuple[dict[str, object], dict[str, object]]] = {
            "language_dialogue_interpretation": (
                {"dialogue_act": "information_request", "topic": "language",
                 "relation": "new", "question": "読みを知りたい",
                 "target": "語", "facet": "reading", "target_status": "explicit",
                 "alternatives": [], "evidence": [{"turn_id": "t1", "quote": "読み"}],
                 "search_terms": ["語"], "clarification": ""}, {},
            ),
            "language_dialogue_reply": (
                {"status": "unsupported", "text": "資料で確かめよか。", "fact_ids": [],
                 "application": "", "missing": "読みの根拠"}, {},
            ),
            "language_participation_forecast": (
                {
                    "reaction": "",
                    "patterns": [
                        {"pattern_id": f"p{index}", "description": f"次の発話型{index}"}
                        for index in range(1, 6)
                    ],
                },
                {},
            ),
            "language_participation_assessment": (
                {
                    "relation": "expected",
                    "matched_pattern_ids": ["p1"],
                    "topic_changed": False,
                    "clear_question": False,
                    "minecraft_topic": False,
                    "evidence": "続き",
                    "confidence": 0.9,
                },
                {"expected_continuations": [
                    {"pattern_id": f"p{index}", "description": f"次の発話型{index}"}
                    for index in range(1, 6)
                ]},
            ),
            "language_research_intent": (
                {"intent": "uncertain", "evidence": "まだ分からない"}, {},
            ),
            "language_research_reading": (
                {"perspective": "", "quotes": []}, {},
            ),
            "language_web_consent": (
                {"intent": "uncertain", "evidence": "ちょっと待って", "confidence": 0.9}, {},
            ),
            "assist_select_sword_intent": (
                {
                    "intent": "other",
                    "weapon_kind": "unknown",
                    "is_request": False,
                    "evidence": "",
                    "confidence": 0.0,
                },
                {},
            ),
            "player_chat_plan": (
                {
                    "action": "continue_conversation",
                    "focus": "直近の会話への相槌",
                    "entity_query": "",
                    "evidence": [
                        {"turn_id": "current", "quote": "大丈夫そう"}
                    ],
                    "confidence": 0.9,
                },
                {
                    "allowed_actions": ["continue_conversation"],
                    "history": [],
                    "current": {
                        "turn_id": "current",
                        "role": "user",
                        "text": "大丈夫そうやな",
                    },
                },
            ),
            "haiku_draft": (
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                {},
            ),
            "haiku_irony": (
                {
                    "found": False,
                    "kind": "none",
                    "description": "",
                    "elements": [],
                    "focus": [],
                    "confidence": 0.0,
                },
                {},
            ),
            "haiku_line_grounding": (
                {
                    "assessments": [
                        {
                            "line_index": 0,
                            "atom_ids": [atom["atom_id"]],
                            "meaning_retained": True,
                            "natural_japanese": True,
                            "reason": "意味が残っている",
                        }
                    ]
                },
                {"grounding_lines": [{"line_index": 0}], "source_atoms": [atom]},
            ),
            "haiku_line_regeneration": (
                {"lines": [{"line_index": 1, "text": "ひつじがあるく"}]},
                {"failed_line_indices": [1]},
            ),
            "haiku_scene": (
                {
                    "found": True,
                    "clauses": [
                        {
                            "text": "春の風が吹く",
                            "basis_atom_ids": [atom["atom_id"]],
                            "claim_class": "factual",
                        }
                    ],
                    "motifs": ["春"],
                    "focus": ["風"],
                    "confidence": 0.8,
                },
                {"source_atoms": [atom]},
            ),
            "haiku_workshop_combat_input": (
                {"action": "uncertain", "confidence": 0.0, "evidence": ""},
                {"allowed_actions": ["uncertain"]},
            ),
            "haiku_workshop_evaluation": (
                {
                    "found": False,
                    "sentiment": "unknown",
                    "scope": "unknown",
                    "evidence": "",
                    "confidence": 0.0,
                },
                {},
            ),
            "haiku_workshop_intent": (
                {
                    "intent": "soft_default",
                    "confidence": 0.0,
                    "repair_requested": False,
                    "findings": [],
                    "evaluation": {
                        "found": False,
                        "sentiment": "unknown",
                        "scope": "unknown",
                        "evidence": "",
                        "confidence": 0.0,
                    },
                    "close_request": close,
                    "line_reference": {
                        "found": False,
                        "concept_id": "unknown",
                        "evidence": "",
                        "confidence": 0.0,
                    },
                    "line_proposal": {
                        "found": False,
                        "target_fragment": "",
                        "replacement_text": "",
                        "evidence": "",
                        "confidence": 0.0,
                    },
                },
                {"allowed_intents": ["soft_default"]},
            ),
            "haiku_workshop_pending_decision": (
                {
                    "action": "uncertain",
                    "confidence": 0.0,
                    "evidence": "",
                    "close_request": close,
                },
                {"allowed_actions": ["uncertain"]},
            ),
            "haiku_workshop_revision": (
                {
                    "lines": [
                        {
                            "line_index": 1,
                            "expected_text": "ひつじがあるく",
                            "replacement_text": "こひつじあるく",
                            "atom_ids": [atom["atom_id"]],
                        }
                    ]
                },
                {
                    "target_line_indices": [1],
                    "current_lines": [
                        {"line_index": 1, "text": "ひつじがあるく"}
                    ],
                    "source_atoms": [atom],
                },
            ),
        }

        self.assertEqual(set(cases), set(STRUCTURED_CONTRACT_KINDS))
        for kind, (payload, details) in cases.items():
            with self.subTest(kind=kind):
                result = validate_structured_payload(kind, payload, details=details)
                self.assertTrue(result.accepted, result.summary)

    def test_legacy_flattened_scene_and_grounding_are_rejected(self) -> None:
        scene = validate_structured_payload(
            "haiku_scene",
            {
                "text": "春の風が吹く",
                "basis_atom_ids": ["observation:test:0"],
                "claim_class": "factual",
            },
            details={"source_atoms": [{"atom_id": "observation:test:0"}]},
        )
        grounding = validate_structured_payload(
            "haiku_line_grounding",
            {
                "line_index": 0,
                "atom_ids": ["observation:test:0"],
                "meaning_retained": True,
                "natural_japanese": True,
                "reason": "一致",
            },
            details={
                "grounding_lines": [{"line_index": 0}],
                "source_atoms": [{"atom_id": "observation:test:0"}],
            },
        )

        self.assertFalse(scene.accepted)
        self.assertFalse(grounding.accepted)

    def test_participation_contract_rejects_duplicate_or_unknown_pattern_ids(self) -> None:
        duplicate = validate_structured_payload(
            "language_participation_forecast",
            {
                "reaction": "",
                "patterns": [
                    {"pattern_id": "p1", "description": f"候補{index}"}
                    for index in range(5)
                ],
            },
        )
        unknown = validate_structured_payload(
            "language_participation_assessment",
            {
                "relation": "expected",
                "matched_pattern_ids": ["p5"],
                "topic_changed": False,
                "clear_question": False,
                "minecraft_topic": False,
                "evidence": "続き",
                "confidence": 0.9,
            },
            details={"expected_continuations": [
                {"pattern_id": f"p{index}", "description": f"候補{index}"}
                for index in range(1, 5)
            ]},
        )
        self.assertFalse(duplicate.accepted)
        self.assertFalse(unknown.accepted)


if __name__ == "__main__":
    unittest.main()
