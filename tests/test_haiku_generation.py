from __future__ import annotations

import copy
import unittest

from dogido_server.entry_catalog import block_entry, item_entry, mob_entry
from dogido_server.haiku.generation import generate_grounded_haiku
from dogido_server.haiku.source_atoms import (
    HaikuSourceAtom,
    atoms_from_catalog_sources,
    catalog_source_snapshot,
    split_note_sentences,
)
from dogido_server.llm.haiku import is_haiku_line_usable
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import StructuredGenerationRequest


def source_atoms(count: int = 4) -> tuple[HaikuSourceAtom, ...]:
    texts = ("春の風", "歩く羊", "夜の月", "強い雨")
    return tuple(
        HaikuSourceAtom(
            atom_id=f"observation:test:{index}",
            text=texts[index],
            source_ref=f"observation:test:{index}",
            field_path="observed_label",
            observation_role="test",
            kind="observation",
        )
        for index in range(count)
    )


class ScriptedLLM:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[StructuredGenerationRequest] = []

    def preload(self) -> bool:
        return False

    def generate_leaf_text(self, request):  # pragma: no cover - 呼ばれたら設計違反
        raise AssertionError(f"legacy leaf called: {request.kind}")

    def generate_structured_json(self, request: StructuredGenerationRequest) -> dict[str, object]:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request: {request.kind}")
        return self.responses.pop(0)


def grounding(*rows: tuple[int, str, bool, bool]) -> dict[str, object]:
    return {
        "assessments": [
            {
                "line_index": index,
                "atom_ids": [atom_id],
                "meaning_retained": retained,
                "natural_japanese": natural,
            }
            for index, atom_id, retained, natural in rows
        ]
    }


class SourceAtomTest(unittest.TestCase):
    def test_ancient_debris_note_is_split_without_changing_catalog_source(self) -> None:
        entry = item_entry("ancient_debris")
        assert entry is not None
        original = copy.deepcopy(entry)
        expected_note = (
            "ネザーに生成される珍しい鉱石。茶色で、ひび割れている。"
            "しかし、非常に高い爆発耐久値を持っており熱に強い。"
        )

        source = catalog_source_snapshot(
            catalog_type="item",
            catalog_id="ancient_debris",
            entry=entry,
            observation_role="selected_item",
        )

        assert source is not None
        self.assertEqual(source.note_raw, expected_note)
        self.assertEqual(
            split_note_sentences(source.note_raw),
            (
                "ネザーに生成される珍しい鉱石。",
                "茶色で、ひび割れている。",
                "しかし、非常に高い爆発耐久値を持っており熱に強い。",
            ),
        )
        atoms = atoms_from_catalog_sources((source,))
        self.assertEqual(atoms[0].atom_id, "item:ancient_debris:japanese")
        self.assertEqual([atom.field_path for atom in atoms[1:]], ["note[0]", "note[1]", "note[2]"])
        self.assertEqual(entry, original)
        self.assertEqual(item_entry("ancient_debris"), original)

    def test_block_reference_keeps_block_source_ref_and_resolved_original_note(self) -> None:
        entry = block_entry("ancient_debris")
        assert entry is not None
        source = catalog_source_snapshot(
            catalog_type="block",
            catalog_id="ancient_debris",
            entry=entry,
            observation_role="nearby_block",
        )

        assert source is not None
        self.assertEqual(source.source_ref, "block:ancient_debris")
        self.assertIn("爆発耐久値", source.note_raw)

    def test_mob_poetic_fields_keep_exact_catalog_paths(self) -> None:
        source = catalog_source_snapshot(
            catalog_type="mob",
            catalog_id="sheep",
            entry=mob_entry("sheep"),
            observation_role="passive_mob",
        )

        assert source is not None
        atoms = atoms_from_catalog_sources((source,))
        poetic = {atom.field_path: atom.text for atom in atoms if atom.kind == "catalog_field"}
        self.assertEqual(poetic["poetic.role"], "平原ののどかさ担当")
        self.assertEqual(poetic["poetic.visual_tags[0]"], "白い")


class GroundedGenerationTest(unittest.TestCase):
    def test_all_new_structured_kinds_have_registered_prompts(self) -> None:
        details = {
            "source_atoms": [atom.to_prompt_dict() for atom in source_atoms()],
            "grounding_lines": [{"line_index": 0, "text": "はるのかぜ"}],
            "current_lines": [
                {"line_index": 0, "text": "はるのかぜ", "frozen": True},
                {"line_index": 1, "text": "ひつじがあるく", "frozen": False},
                {"line_index": 2, "text": "よるのつき", "frozen": True},
            ],
            "failed_line_indices": [1],
        }

        for kind in ("haiku_draft", "haiku_line_grounding", "haiku_line_regeneration"):
            messages = build_messages(
                StructuredGenerationRequest(
                    kind=kind,
                    fallback_value={},
                    details=details,
                )
            )
            self.assertEqual([message["role"] for message in messages], ["system", "user"])
            self.assertIn("JSON", messages[1]["content"])

    def test_accepts_three_grounded_lines_at_lower_temperature(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                grounding(
                    (0, "observation:test:0", True, True),
                    (1, "observation:test:1", True, True),
                    (2, "observation:test:2", True, True),
                ),
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=source_atoms(),
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.text, "はるのかぜ\nひつじがあるく\nよるのつき")
        self.assertEqual([request.kind for request in llm.requests], ["haiku_draft", "haiku_line_grounding"])
        self.assertEqual((llm.requests[0].temperature, llm.requests[0].route), (0.60, "haiku"))
        self.assertEqual((llm.requests[1].temperature, llm.requests[1].route), (0.0, "chat"))
        self.assertEqual(result.line_sources[1]["atom_ids"], ["observation:test:1"])

    def test_duplicate_atom_regenerates_only_later_line_and_excludes_reserved_atoms(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                grounding(
                    (0, "observation:test:0", True, True),
                    (1, "observation:test:0", True, True),
                    (2, "observation:test:2", True, True),
                ),
                {"lines": [{"line_index": 1, "text": "あめつよくふる"}]},
                grounding((1, "observation:test:3", True, True)),
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=source_atoms(),
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.text, "はるのかぜ\nあめつよくふる\nよるのつき")
        retry = llm.requests[2]
        self.assertEqual(retry.kind, "haiku_line_regeneration")
        self.assertEqual(retry.details["failed_line_indices"], [1])
        self.assertEqual(
            [row["text"] for row in retry.details["current_lines"] if row["frozen"]],
            ["はるのかぜ", "よるのつき"],
        )
        remaining_ids = {atom["atom_id"] for atom in retry.details["source_atoms"]}
        self.assertNotIn("observation:test:0", remaining_ids)
        self.assertNotIn("observation:test:2", remaining_ids)

    def test_unnatural_line_fails_closed_after_exactly_two_regeneration_rounds(self) -> None:
        bad = grounding(
            (0, "observation:test:0", True, False),
            (1, "observation:test:1", True, False),
            (2, "observation:test:2", True, False),
        )
        llm = ScriptedLLM(
            [
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                bad,
                {"lines": [
                    {"line_index": 0, "text": "はるのかぜ"},
                    {"line_index": 1, "text": "ひつじがあるく"},
                    {"line_index": 2, "text": "よるのつき"},
                ]},
                bad,
                {"lines": [
                    {"line_index": 0, "text": "はるのかぜ"},
                    {"line_index": 1, "text": "ひつじがあるく"},
                    {"line_index": 2, "text": "よるのつき"},
                ]},
                bad,
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=source_atoms(),
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.text, "まとまらんかった。。。")
        self.assertEqual(
            [request.kind for request in llm.requests],
            [
                "haiku_draft",
                "haiku_line_grounding",
                "haiku_line_regeneration",
                "haiku_line_grounding",
                "haiku_line_regeneration",
                "haiku_line_grounding",
            ],
        )

    def test_unknown_atom_id_is_not_accepted(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                grounding(
                    (0, "invented:atom", True, True),
                    (1, "observation:test:1", True, True),
                    (2, "observation:test:2", True, True),
                ),
                {"lines": [{"line_index": 0, "text": "あめのかぜ"}]},
                grounding((0, "observation:test:3", True, True)),
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=source_atoms(),
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.text.splitlines()[0], "あめのかぜ")

    def test_fewer_than_three_atoms_fails_before_calling_llm(self) -> None:
        llm = ScriptedLLM([])

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=source_atoms(2),
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(llm.requests, [])

    def test_line_form_gate_rejects_gibberish_kanji_and_hard_forbidden_term(self) -> None:
        constraints = {"haiku_constraints": {"forbidden_terms": ["つるはし"]}}

        self.assertFalse(is_haiku_line_usable("あいうえお", 0))
        self.assertFalse(is_haiku_line_usable("春のかぜ", 0))
        self.assertFalse(is_haiku_line_usable("つるはし", 2, constraints))
        self.assertTrue(is_haiku_line_usable("はるのかぜ", 0))


if __name__ == "__main__":
    unittest.main()
