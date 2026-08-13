from __future__ import annotations

import copy
import unittest

from dogido_server.entry_catalog import block_entry, item_entry, mob_entry
from dogido_server.haiku.generation import generate_grounded_haiku, generate_workshop_revision
from dogido_server.haiku.lexical_correction import correct_grounded_catalog_kana
from dogido_server.haiku.preface import validate_preface_clauses
from dogido_server.haiku.source_atoms import (
    HaikuSourceAtom,
    atoms_from_preface_clauses,
    atoms_from_catalog_sources,
    catalog_source_snapshot,
    preface_clauses_from_payload,
    split_note_sentences,
    line_source_ids_from_materials,
    source_atoms_from_materials,
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
            claim_class="factual",
            claim_scopes=("observed_state",),
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
    def test_catalog_kana_typo_correction_uses_only_claimed_label_without_requiring_full_name(self) -> None:
        atom = HaikuSourceAtom(
            atom_id="item:birch_stairs:japanese",
            text="シラカバの階段",
            source_ref="item:birch_stairs",
            field_path="japanese",
            observation_role="selected_item",
            kind="catalog_label",
            claim_class="factual",
            claim_scopes=("identity_only",),
        )

        correction = correct_grounded_catalog_kana(
            "しろかばの",
            atom_ids=(atom.atom_id,),
            atom_by_id={atom.atom_id: atom},
        )

        assert correction is not None
        self.assertEqual(correction.corrected, "しらかばの")
        self.assertNotIn("かいだん", correction.corrected)

    def test_catalog_kana_typo_correction_ignores_unclaimed_short_and_ambiguous_terms(self) -> None:
        birch = HaikuSourceAtom(
            atom_id="item:birch_stairs:japanese",
            text="シラカバの階段",
            source_ref="item:birch_stairs",
            field_path="japanese",
            observation_role="selected_item",
            kind="catalog_label",
            claim_class="factual",
            claim_scopes=("identity_only",),
        )
        similar = HaikuSourceAtom(
            atom_id="test:similar:japanese",
            text="シロカナ",
            source_ref="test:similar",
            field_path="japanese",
            observation_role="test",
            kind="catalog_label",
            claim_class="factual",
            claim_scopes=("identity_only",),
        )
        short = HaikuSourceAtom(
            atom_id="test:short:japanese",
            text="アメ",
            source_ref="test:short",
            field_path="japanese",
            observation_role="test",
            kind="catalog_label",
            claim_class="factual",
            claim_scopes=("identity_only",),
        )
        atoms = {atom.atom_id: atom for atom in (birch, similar, short)}

        self.assertIsNone(
            correct_grounded_catalog_kana(
                "しろかばの",
                atom_ids=(short.atom_id,),
                atom_by_id=atoms,
            )
        )
        self.assertIsNone(
            correct_grounded_catalog_kana(
                "しろかば",
                atom_ids=(birch.atom_id, similar.atom_id),
                atom_by_id=atoms,
            )
        )

    def test_preface_clauses_keep_basis_class_and_code_assigned_scope(self) -> None:
        bases = source_atoms(3)
        clauses = preface_clauses_from_payload(
            [
                {
                    "text": "春の風が吹いている",
                    "basis_atom_ids": [bases[0].atom_id],
                    "claim_class": "factual",
                },
                {
                    "text": "羊と月が静かに向き合う",
                    "basis_atom_ids": [bases[1].atom_id, bases[2].atom_id],
                    "claim_class": "interpretive",
                },
            ],
            source_atoms=bases,
        )
        assert clauses is not None
        atoms = atoms_from_preface_clauses(clauses)

        self.assertEqual(
            [atom.text for atom in atoms],
            [
                "春の風が吹いている",
                "羊と月が静かに向き合う",
            ],
        )
        self.assertEqual(atoms[0].claim_class, "factual")
        self.assertEqual(atoms[0].claim_scopes, ("observed_state",))
        self.assertEqual(atoms[1].claim_class, "interpretive")
        self.assertEqual(atoms[1].claim_scopes, ("poetic_interpretation",))
        self.assertEqual(atoms[1].basis_atom_ids, (bases[1].atom_id, bases[2].atom_id))
        self.assertTrue(all(atom.kind == "preface_clause" for atom in atoms))
        self.assertTrue(all(atom.source_ref == "preface:spoken" for atom in atoms))

    def test_preface_factual_clause_cannot_promote_interpretive_source(self) -> None:
        base = HaikuSourceAtom(
            atom_id="catalog:test:poetic",
            text="のどかさ担当",
            source_ref="catalog:test",
            field_path="poetic.role",
            observation_role="test",
            kind="catalog_field",
            claim_class="interpretive",
            claim_scopes=("source_meaning",),
        )

        clauses = preface_clauses_from_payload(
            [{
                "text": "ここはのどかな場所だ",
                "basis_atom_ids": [base.atom_id],
                "claim_class": "factual",
            }],
            source_atoms=(base,),
        )

        self.assertIsNone(clauses)

    def test_preface_scope_evaluator_rejection_fails_closed(self) -> None:
        bases = source_atoms(2)
        clauses = preface_clauses_from_payload(
            [{
                "text": "雪の積もるタイガだ",
                "basis_atom_ids": [bases[0].atom_id],
                "claim_class": "factual",
            }],
            source_atoms=bases,
        )
        assert clauses is not None
        llm = ScriptedLLM([{
            "assessments": [{
                "clause_index": 0,
                "basis_atom_ids": [bases[0].atom_id],
                "claim_class": "factual",
                "meaning_retained": False,
                "class_correct": True,
                "within_claim_scope": False,
                "natural_japanese": True,
            }],
        }])

        accepted = validate_preface_clauses(
            llm,
            clauses=clauses,
            source_atoms=bases,
            max_tokens=192,
        )

        self.assertFalse(accepted)
        self.assertEqual(llm.requests[0].kind, "haiku_preface_grounding")

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
    def test_grounded_catalog_label_gets_unique_one_kana_typo_corrected(self) -> None:
        birch = HaikuSourceAtom(
            atom_id="item:birch_stairs:japanese",
            text="シラカバの階段",
            source_ref="item:birch_stairs",
            field_path="japanese",
            observation_role="selected_item",
            kind="catalog_label",
            claim_class="factual",
            claim_scopes=("identity_only",),
        )
        atoms = (birch, *source_atoms(2))
        llm = ScriptedLLM(
            [
                {"lines": ["しろかばの", "ひつじがあるく", "よるのつき"]},
                grounding(
                    (0, birch.atom_id, True, True),
                    (1, "observation:test:0", True, True),
                    (2, "observation:test:1", True, True),
                ),
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=atoms,
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.text, "しらかばの\nひつじがあるく\nよるのつき")
        self.assertEqual(result.line_sources[0]["text"], "しらかばの")

    def test_all_new_structured_kinds_have_registered_prompts(self) -> None:
        details = {
            "source_atoms": [atom.to_prompt_dict() for atom in source_atoms()],
            "preface_clauses": [{
                "text": "春の風が吹いている",
                "basis_atom_ids": ["observation:test:0"],
                "claim_class": "factual",
                "claim_scopes": ["observed_state"],
            }],
            "grounding_lines": [{"line_index": 0, "text": "はるのかぜ"}],
            "current_lines": [
                {"line_index": 0, "text": "はるのかぜ", "frozen": True},
                {
                    "line_index": 1,
                    "text": "ひつじがあるく",
                    "frozen": False,
                    "sound_count": 7,
                    "target_sound_count": 7,
                    "allowed_sound_min": 6,
                    "allowed_sound_max": 8,
                    "meter_status": "within_range",
                },
                {"line_index": 2, "text": "よるのつき", "frozen": True},
            ],
            "failed_line_indices": [1],
        }

        for kind in (
            "haiku_draft",
            "haiku_line_grounding",
            "haiku_line_regeneration",
            "haiku_preface_grounding",
            "haiku_workshop_revision",
        ):
            messages = build_messages(
                StructuredGenerationRequest(
                    kind=kind,
                    fallback_value={},
                    details=details,
                )
            )
            self.assertEqual([message["role"] for message in messages], ["system", "user"])
            self.assertIn("JSON", messages[1]["content"])

        workshop_prompt = build_messages(
            StructuredGenerationRequest(
                kind="haiku_workshop_revision",
                fallback_value={},
                details=details,
            )
        )[1]["content"]
        self.assertIn("expected_text", workshop_prompt)
        self.assertIn("replacement_text", workshop_prompt)

        regeneration = build_messages(
            StructuredGenerationRequest(
                kind="haiku_line_regeneration",
                fallback_value={},
                details=details,
            )
        )[1]["content"]
        self.assertIn("現在7音 → 目標7音、許容6〜8音", regeneration)
        preface = build_messages(
            StructuredGenerationRequest(
                kind="haiku_preface_grounding",
                fallback_value={},
                details=details,
            )
        )[1]["content"]
        self.assertIn("identity_only=名称そのものだけ", preface)
        self.assertIn("within_claim_scope", preface)

    def test_material_selection_prompts_do_not_request_warm_or_gentle_wording(self) -> None:
        details = {"source_atoms": [atom.to_prompt_dict() for atom in source_atoms()]}

        for kind in ("haiku_irony", "haiku_scene"):
            prompt = build_messages(
                StructuredGenerationRequest(
                    kind=kind,
                    fallback_value={},
                    details=details,
                )
            )[1]["content"]
            self.assertIn("材料の意味を変えず", prompt)
            self.assertNotIn("あたたかく", prompt)
            self.assertNotIn("やさしいことば", prompt)

    def test_workshop_revision_changes_only_target_line_on_haiku_route(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": [{
                    "line_index": 1,
                    "expected_text": "ひつじがあるく",
                    "replacement_text": "あめつよくふる",
                    "atom_ids": ["observation:test:3"],
                }]},
                grounding((1, "observation:test:3", True, True)),
            ]
        )
        result = generate_workshop_revision(
            llm,
            original_text="はるのかぜ\nひつじがあるく\nよるのつき",
            target_indices=(1,),
            findings=({
                "line_index": 1,
                "fragment": "ひつじがあるく",
                "problem": "preference",
                "note": "雨を残したい",
                "confidence": 0.9,
            },),
            source_atoms=source_atoms(),
            original_line_sources={
                0: ("observation:test:0",),
                1: ("observation:test:1",),
                2: ("observation:test:2",),
            },
            details={},
            max_tokens=192,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.text, "はるのかぜ\nあめつよくふる\nよるのつき")
        self.assertEqual((llm.requests[0].kind, llm.requests[0].route), ("haiku_workshop_revision", "haiku"))
        self.assertEqual((llm.requests[1].kind, llm.requests[1].route), ("haiku_line_grounding", "chat"))
        self.assertEqual(result.base_text, "はるのかぜ\nひつじがあるく\nよるのつき")
        self.assertEqual(result.edits[0].expected_text, "ひつじがあるく")
        self.assertEqual(result.edits[0].replacement_text, "あめつよくふる")
        self.assertEqual(llm.requests[0].details["edit_contract"], "line_compare_and_swap_v1")
        frozen = [row["text"] for row in llm.requests[0].details["current_lines"] if row["frozen"]]
        self.assertEqual(frozen, ["はるのかぜ", "よるのつき"])

    def test_workshop_revision_rejects_false_known_atom_attribution(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": [{
                    "line_index": 1,
                    "expected_text": "ひつじがあるく",
                    "replacement_text": "あめつよくふる",
                    "atom_ids": ["observation:test:1"],
                }]},
                grounding((1, "observation:test:1", False, True)),
                {"lines": [{
                    "line_index": 1,
                    "expected_text": "ひつじがあるく",
                    "replacement_text": "あめつよくふる",
                    "atom_ids": ["observation:test:1"],
                }]},
                grounding((1, "observation:test:1", False, True)),
            ]
        )

        result = generate_workshop_revision(
            llm,
            original_text="はるのかぜ\nひつじがあるく\nよるのつき",
            target_indices=(1,),
            findings=(),
            source_atoms=source_atoms(),
            original_line_sources={
                0: ("observation:test:0",),
                1: ("observation:test:1",),
                2: ("observation:test:2",),
            },
            details={},
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(
            [request.kind for request in llm.requests],
            [
                "haiku_workshop_revision",
                "haiku_line_grounding",
                "haiku_workshop_revision",
                "haiku_line_grounding",
            ],
        )

    def test_workshop_revision_rejects_when_a_frozen_line_has_no_source(self) -> None:
        llm = ScriptedLLM([])

        result = generate_workshop_revision(
            llm,
            original_text="はるのかぜ\nひつじがあるく\nよるのつき",
            target_indices=(1,),
            findings=(),
            source_atoms=source_atoms(),
            original_line_sources={2: ("observation:test:2",)},
            details={},
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.failure_reason, "missing_frozen_line_sources")
        self.assertEqual(llm.requests, [])

    def test_workshop_revision_keeps_saved_hard_constraints(self) -> None:
        llm = ScriptedLLM([
            {"lines": [{
                "line_index": 2,
                "expected_text": "よるのつき",
                "replacement_text": "つるはし",
                "atom_ids": ["observation:test:3"],
            }]},
            {"lines": [{
                "line_index": 2,
                "expected_text": "よるのつき",
                "replacement_text": "つるはし",
                "atom_ids": ["observation:test:3"],
            }]},
        ])

        result = generate_workshop_revision(
            llm,
            original_text="はるのかぜ\nひつじがあるく\nよるのつき",
            target_indices=(2,),
            findings=(),
            source_atoms=source_atoms(),
            original_line_sources={0: ("observation:test:0",), 1: ("observation:test:1",)},
            details={"haiku_constraints": {"forbidden_terms": ["つるはし"]}},
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertEqual([request.kind for request in llm.requests], ["haiku_workshop_revision"] * 2)

    def test_workshop_revision_rejects_unknown_atom_without_touching_original(self) -> None:
        llm = ScriptedLLM([
            {"lines": [{"line_index": 1, "expected_text": "ひつじがあるく", "replacement_text": "あめつよくふる", "atom_ids": ["invented"]}]},
            {"lines": [{"line_index": 1, "expected_text": "ひつじがあるく", "replacement_text": "あめつよくふる", "atom_ids": ["invented"]}]},
        ])
        result = generate_workshop_revision(
            llm,
            original_text="はるのかぜ\nひつじがあるく\nよるのつき",
            target_indices=(1,),
            findings=(),
            source_atoms=source_atoms(),
            original_line_sources={0: ("observation:test:0",), 2: ("observation:test:2",)},
            details={},
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertIsNone(result.text)
        self.assertEqual(len(llm.requests), 2)

    def test_workshop_revision_rejects_stale_or_legacy_edit_contract(self) -> None:
        llm = ScriptedLLM([
            {"lines": [{
                "line_index": 1,
                "expected_text": "べつのもとのぎょう",
                "replacement_text": "あめつよくふる",
                "atom_ids": ["observation:test:3"],
            }]},
            # 旧 {line_index, text} 形式も互換受理しない。
            {"lines": [{
                "line_index": 1,
                "text": "あめつよくふる",
                "atom_ids": ["observation:test:3"],
            }]},
        ])

        result = generate_workshop_revision(
            llm,
            original_text="はるのかぜ\nひつじがあるく\nよるのつき",
            target_indices=(1,),
            findings=(),
            source_atoms=source_atoms(),
            original_line_sources={
                0: ("observation:test:0",),
                2: ("observation:test:2",),
            },
            details={},
            max_tokens=192,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.failure_reason, "invalid_revision")
        self.assertEqual(
            [request.kind for request in llm.requests],
            ["haiku_workshop_revision", "haiku_workshop_revision"],
        )

    def test_saved_material_sources_are_validated_against_the_current_verse(self) -> None:
        atoms = source_atoms(3)
        materials = {
            "source_atoms": [atom.to_prompt_dict() for atom in atoms],
            "line_sources": [
                {"line_index": 0, "text": "はるのかぜ", "atom_ids": [atoms[0].atom_id]},
                {"line_index": 1, "text": "別の行", "atom_ids": [atoms[1].atom_id]},
            ],
        }
        restored = source_atoms_from_materials(materials)
        line_ids = line_source_ids_from_materials(
            materials,
            verse_lines=["はるのかぜ", "ひつじがあるく", "よるのつき"],
            allowed_atom_ids={atom.atom_id for atom in restored},
        )

        self.assertEqual(restored, atoms)
        self.assertEqual(line_ids, {0: (atoms[0].atom_id,)})

    def test_old_source_atom_shape_is_not_restored(self) -> None:
        old_atom = {
            "atom_id": "observation:test:0",
            "text": "春の風",
            "source_ref": "observation:test:0",
            "field_path": "observed_label",
            "observation_role": "test",
            "kind": "observation",
        }

        self.assertEqual(source_atoms_from_materials({"source_atoms": [old_atom]}), ())

    def test_saved_preface_scope_is_recomputed_from_basis(self) -> None:
        base = source_atoms(1)[0]
        tampered = HaikuSourceAtom(
            atom_id="preface:spoken:clause:0",
            text="春の風が吹く",
            source_ref="preface:spoken",
            field_path="clause[0]",
            observation_role="spoken_preface",
            kind="preface_clause",
            claim_class="factual",
            claim_scopes=("identity_only",),
            basis_atom_ids=(base.atom_id,),
        )

        restored = source_atoms_from_materials({
            "source_atoms": [base.to_prompt_dict(), tampered.to_prompt_dict()],
        })

        self.assertEqual(restored, (base,))

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
        self.assertEqual(result.generation_strategy, "three_slot")
        self.assertEqual(result.regeneration_rounds, 0)

    def test_four_generation_strategies_retry_their_own_slot_and_share_validation(self) -> None:
        cases = {
            "whole_poem": ([0, 1, 2], []),
            "three_slot": ([1], [0, 2]),
            "one_plus_two": ([1, 2], [0]),
            "two_plus_one": ([0, 1], [2]),
        }
        initial_lines = ["はるのかぜ", "ひつじがあるく", "よるのつき"]
        replacement_lines = ["あめのかぜ", "あめつよくふる", "あめのつき"]
        for strategy, (retry_indices, frozen_indices) in cases.items():
            with self.subTest(strategy=strategy):
                regenerated_rows = [
                    {"line_index": index, "text": replacement_lines[index]}
                    for index in retry_indices
                ]
                retried_grounding = grounding(
                    *(
                        (index, f"observation:test:{index}", True, True)
                        for index in retry_indices
                    )
                )
                llm = ScriptedLLM(
                    [
                        {"lines": initial_lines},
                        grounding(
                            (0, "observation:test:0", True, True),
                            (1, "observation:test:1", True, False),
                            (2, "observation:test:2", True, True),
                        ),
                        {"lines": regenerated_rows},
                        retried_grounding,
                    ]
                )

                result = generate_grounded_haiku(
                    llm,
                    details={},
                    source_atoms=source_atoms(),
                    fallback_text="まとまらんかった。。。",
                    max_tokens=192,
                    generation_strategy=strategy,
                )

                self.assertTrue(result.accepted)
                self.assertEqual(result.generation_strategy, strategy)
                self.assertEqual(result.regeneration_rounds, 1)
                retry = llm.requests[2]
                self.assertEqual(retry.details["failed_line_indices"], retry_indices)
                self.assertEqual(
                    [
                        row["line_index"]
                        for row in retry.details["current_lines"]
                        if row["frozen"]
                    ],
                    frozen_indices,
                )
                draft_prompt = build_messages(llm.requests[0])[1]["content"]
                retry_prompt = build_messages(retry)[1]["content"]
                self.assertIn("【今回の生成単位】", draft_prompt)
                self.assertIn("【今回の生成単位】", retry_prompt)

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

    def test_preface_atom_and_its_basis_cannot_be_used_on_separate_lines(self) -> None:
        bases = source_atoms()
        clauses = preface_clauses_from_payload(
            [{
                "text": "春の風が残る",
                "basis_atom_ids": [bases[0].atom_id],
                "claim_class": "interpretive",
            }],
            source_atoms=bases,
        )
        assert clauses is not None
        preface = atoms_from_preface_clauses(clauses)[0]
        atoms = (*bases, preface)
        llm = ScriptedLLM(
            [
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                grounding(
                    (0, bases[0].atom_id, True, True),
                    (1, preface.atom_id, True, True),
                    (2, bases[2].atom_id, True, True),
                ),
                {"lines": [{"line_index": 1, "text": "あめつよくふる"}]},
                grounding((1, bases[3].atom_id, True, True)),
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=atoms,
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
        )

        self.assertTrue(result.accepted)
        retry = llm.requests[2]
        failed = next(
            row for row in retry.details["current_lines"]
            if row["line_index"] == 1
        )
        self.assertEqual(failed["failure_reasons"], ["source_reused"])
        remaining_ids = {atom["atom_id"] for atom in retry.details["source_atoms"]}
        self.assertNotIn(bases[0].atom_id, remaining_ids)
        self.assertNotIn(preface.atom_id, remaining_ids)

    def test_regeneration_receives_code_counted_meter_feedback(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": ["あたたかきくさち", "ひつじがあるく", "よるのつき"]},
                grounding(
                    (0, "observation:test:0", True, True),
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
        retry = llm.requests[2]
        failed_row = next(
            row for row in retry.details["current_lines"]
            if row["line_index"] == 0
        )
        self.assertEqual(
            {
                key: failed_row[key]
                for key in (
                    "sound_count",
                    "target_sound_count",
                    "allowed_sound_min",
                    "allowed_sound_max",
                    "meter_status",
                )
            },
            {
                "sound_count": 8,
                "target_sound_count": 5,
                "allowed_sound_min": 4,
                "allowed_sound_max": 6,
                "meter_status": "too_long",
            },
        )
        prompt = build_messages(retry)[1]["content"]
        self.assertIn("音数が長い: 現在8音 → 目標5音、許容4〜6音", prompt)
        self.assertEqual(failed_row["failure_reasons"], ["meter_too_long"])
        self.assertIn("失敗理由: 音数が長い", prompt)

    def test_incomplete_assessment_array_retries_only_missing_checks_one_by_one(self) -> None:
        llm = ScriptedLLM(
            [
                {"lines": ["はるのかぜ", "ひつじがあるく", "よるのつき"]},
                grounding((0, "observation:test:0", True, True)),
                grounding((1, "observation:test:1", True, True)),
                grounding((2, "observation:test:2", True, True)),
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
        self.assertEqual(
            [request.details["grounding_lines"] for request in llm.requests[1:]],
            [
                [
                    {"line_index": 0, "text": "はるのかぜ"},
                    {"line_index": 1, "text": "ひつじがあるく"},
                    {"line_index": 2, "text": "よるのつき"},
                ],
                [{"line_index": 1, "text": "ひつじがあるく"}],
                [{"line_index": 2, "text": "よるのつき"}],
            ],
        )

    def test_duplicate_regenerations_are_rejected_before_another_grounding_call(self) -> None:
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
                    {"line_index": 0, "text": "ハルノカゼ"},
                    {"line_index": 1, "text": "ヒツジガアルク"},
                    {"line_index": 2, "text": "ヨルノツキ"},
                ]},
                {"lines": [
                    {"line_index": 0, "text": "はるのかぜ"},
                    {"line_index": 1, "text": "ひつじがあるく"},
                    {"line_index": 2, "text": "よるのつき"},
                ]},
            ]
        )

        result = generate_grounded_haiku(
            llm,
            details={},
            source_atoms=source_atoms(),
            fallback_text="まとまらんかった。。。",
            max_tokens=192,
            max_regeneration_rounds=2,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.text, "まとまらんかった。。。")
        self.assertEqual(
            [request.kind for request in llm.requests],
            [
                "haiku_draft",
                "haiku_line_grounding",
                "haiku_line_regeneration",
                "haiku_line_regeneration",
            ],
        )
        second_retry = llm.requests[3]
        self.assertTrue(
            all(
                row.get("failure_reasons") == ["duplicate_candidate"]
                for row in second_retry.details["current_lines"]
                if not row["frozen"]
            )
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
