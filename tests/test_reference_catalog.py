from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dev_tools.catalog_tools.reference_catalog import (
    get_reference,
    load_reference_index,
    search_references,
)
from scripts.validate_reference_catalog import (
    _duplicate_json_key_issues,
    validate_against_schema,
)


ROOT = Path(__file__).resolve().parents[1]


class ReferenceCatalogTests(unittest.TestCase):
    def test_top_level_catalog_index_points_to_both_searchable_databases(self) -> None:
        index = json.loads((ROOT / "reference" / "catalogs.json").read_text(encoding="utf-8"))
        rows = {row["id"]: row for row in index["catalogs"]}
        self.assertEqual(
            set(rows),
            {"language_education_and_poetry", "minecraft_technical"},
        )
        for row in rows.values():
            self.assertTrue((ROOT / "reference" / row["readme"]).is_file())
            self.assertIsNotNone(importlib.util.find_spec(row["search_module"]))
        self.assertIsNotNone(
            importlib.util.find_spec(
                rows["language_education_and_poetry"]["classification_search_module"]
            )
        )
        self.assertTrue(
            (ROOT / "reference" / rows["language_education_and_poetry"]["index"]).is_file()
        )
        self.assertEqual(
            rows["minecraft_technical"]["local_current"],
            ".dogido_reference/minecraft_technical/current.json",
        )

    def test_catalog_validator_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_reference_catalog.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_index_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/build_reference_index.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "duplicate.json"
            path.write_text('{"fixed_order": true, "fixed_order": false}', encoding="utf-8")
            with mock.patch(
                "scripts.validate_reference_catalog.REFERENCE_DIR",
                path.parent,
            ):
                issues = _duplicate_json_key_issues(path)
        self.assertTrue(issues)

    def test_explicit_term_search_and_lookup(self) -> None:
        for query in ("学習指導要領コード", "学習指導要領 コード"):
            ids = [row["id"] for row in search_references(query)]
            self.assertIn("jp.mext.curriculum-codes", ids)
        for query in ("日本語文型", "日本語 文型"):
            ids = [row["id"] for row in search_references(query)]
            self.assertIn("jp.ninjal.sentence-patterns", ids)
        five_seven_five = {row["id"] for row in search_references("五 七 五")}
        self.assertIn("poetry.jp.haiku", five_seven_five)
        self.assertIn("poetry.jp.senryu", five_seven_five)
        self.assertNotIn("poetry.zh.jueju", five_seven_five)
        self.assertNotIn("poetry.zh.lushi", five_seven_five)

        record = get_reference("jp.mext.curriculum-codes")
        self.assertIsNotNone(record)
        self.assertEqual(record["policy_role"], "advisory_only")

    def test_exact_title_precedes_other_exact_search_terms(self) -> None:
        result = search_references(
            "枕詞",
            dataset_ids=["makurakotoba"],
            limit=1,
        )
        self.assertEqual(result[0]["id"], "knowledge.rhetoric.makurakotoba")

    def test_lookup_rejects_dataset_path_escape_from_tampered_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            reference_dir = root / "reference"
            reference_dir.mkdir()

            record = {"id": "tampered.record"}
            payload = json.dumps({"entries": [record]}, ensure_ascii=False)
            outside = root / "outside.json"
            outside.write_text(payload, encoding="utf-8")
            nested = reference_dir / "nested"
            nested.mkdir()
            (nested / "inside.json").write_text(payload, encoding="utf-8")
            (reference_dir / "not-json.txt").write_text(payload, encoding="utf-8")
            (reference_dir / "linked.json").symlink_to(outside)
            (reference_dir / "real.json").write_text(payload, encoding="utf-8")
            (reference_dir / "same-directory-link.json").symlink_to(
                reference_dir / "real.json"
            )

            unsafe_paths = (
                "../outside.json",
                str(outside.resolve()),
                "nested/inside.json",
                "not-json.txt",
                "linked.json",
                "same-directory-link.json",
            )
            for dataset_path in unsafe_paths:
                with self.subTest(dataset_path=dataset_path):
                    index = {
                        "entries": {
                            record["id"]: {
                                "id": record["id"],
                                "dataset_path": dataset_path,
                                "record_key": "entries",
                                "record_index": 0,
                            }
                        }
                    }
                    (reference_dir / "index.json").write_text(
                        json.dumps(index, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        get_reference(record["id"], reference_dir=reference_dir)

    def test_runtime_digest_rejects_same_size_atomic_replacement(self) -> None:
        source = ROOT / "reference" / "language_education_and_poetry"
        source_index = json.loads((source / "index.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary_dir:
            reference_dir = Path(temporary_dir) / "reference"
            reference_dir.mkdir()
            shutil.copy2(source / "index.json", reference_dir / "index.json")
            for row in source_index["datasets"]:
                shutil.copy2(source / row["path"], reference_dir / row["path"])

            loaded = load_reference_index(reference_dir)
            dataset = reference_dir / loaded["datasets"][0]["path"]
            before = dataset.stat()
            original = dataset.read_bytes()
            if b'"schema_version":1' in original:
                tampered = original.replace(b'"schema_version":1', b'"schema_version":2', 1)
            else:
                tampered = original.replace(b'"schema_version": 1', b'"schema_version": 2', 1)
            self.assertNotEqual(original, tampered)
            replacement = dataset.with_suffix(".replacement")
            replacement.write_bytes(tampered)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, dataset)
            os.utime(dataset, ns=(before.st_atime_ns, before.st_mtime_ns))

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_reference_index(reference_dir)

    def test_structured_facets_cover_both_datasets(self) -> None:
        world_ids = {
            row["id"]
            for row in search_references(
                dataset_ids=["world_poetry"],
                entity_kinds=["form"],
                prosodic_bases=["mora"],
            )
        }
        self.assertIn("poetry.jp.senryu", world_ids)
        self.assertNotIn("poetry.general.lyric", world_ids)

        official_api_ids = {
            row["id"]
            for row in search_references(
                dataset_ids=["japanese_language_education"],
                acquisition_modes=["official_api"],
                local_runtime_allowed=["true"],
            )
        }
        self.assertIn("jp.ndl.search-api", official_api_ids)
        self.assertIn("jp.ndl.authorities", official_api_ids)

    def test_world_poetry_japanese_axis_labels_are_searchable(self) -> None:
        expected = {
            "poetry.general.free-verse",
            "poetry.jp.renga",
            "poetry.mi.waiata",
            "poetry.yo.oriki",
        }
        by_query = {
            row["id"]
            for row in search_references(
                "共同制作",
                dataset_ids=["world_poetry"],
                limit=100,
            )
        }
        by_facet = {
            row["id"]
            for row in search_references(
                dataset_ids=["world_poetry"],
                composition_modes=["共同制作"],
                limit=100,
            )
        }
        self.assertEqual(by_query, expected)
        self.assertEqual(by_facet, expected)

        regulated = {
            row["id"]
            for row in search_references(
                dataset_ids=["world_poetry"],
                formal_constraints=["規則形式"],
                limit=100,
            )
        }
        self.assertIn("poetry.jp.renga", regulated)
        self.assertIn("poetry.zh.lushi", regulated)

    def test_multiple_dataset_ids_are_or_within_the_dataset_facet(self) -> None:
        results = search_references(
            dataset_ids=["world_poetry", "japanese_language_education"],
            limit=10_000,
        )
        result_ids = {record["id"] for record in results}
        self.assertIn("poetry.jp.senryu", result_ids)
        self.assertIn("jp.ndl.search-api", result_ids)

        forms = search_references(
            dataset_ids=["world_poetry", "japanese_language_education"],
            entity_kinds=["form"],
            limit=10_000,
        )
        form_ids = {record["id"] for record in forms}
        self.assertIn("poetry.jp.senryu", form_ids)
        self.assertNotIn("jp.ndl.search-api", form_ids)

    def test_world_poetry_guardrails_are_structured(self) -> None:
        haiku = get_reference("poetry.jp.haiku")
        self.assertEqual(haiku["structure"]["prosodic_unit"], "mora")
        self.assertIn("poetry.jp.senryu", haiku["related_ids"])

        senryu = get_reference("poetry.jp.senryu")
        self.assertIn("季語の不在だけで川柳と判定しない", senryu["dogido_guardrails"])

        pantun = get_reference("poetry.ms.pantun")
        pantoum = get_reference("poetry.fr.pantoum")
        self.assertNotEqual(pantun["id"], pantoum["id"])
        self.assertIn(pantoum["id"], pantun["related_ids"])

        self.assertEqual(
            pantoum["relations"],
            [{"type": "adaptation_of", "target_id": pantun["id"]}],
        )

    def test_genres_are_not_misclassified_as_free_form(self) -> None:
        form_independent = {
            row["id"]
            for row in search_references(
                dataset_ids=["world_poetry"],
                formal_constraints=["form_independent"],
                limit=100,
            )
        }
        self.assertTrue(
            {
                "poetry.general.lyric",
                "poetry.general.epic",
                "poetry.general.dramatic-monologue",
            }
            <= form_independent
        )
        open_form = {
            row["id"]
            for row in search_references(
                dataset_ids=["world_poetry"],
                formal_constraints=["open"],
                limit=100,
            )
        }
        self.assertFalse(form_independent & open_form)

    def test_established_japanese_labels_and_scoped_constraints(self) -> None:
        world = json.loads(
            (ROOT / "reference/language_education_and_poetry/world_poetry.json").read_text(
                encoding="utf-8"
            )
        )
        entity_labels = {
            row["id"]: row["label_ja"]
            for row in world["classification_axes"]["entity_kinds"]
        }
        self.assertEqual(entity_labels["genre"], "詩種")
        self.assertEqual(entity_labels["form"], "詩形")
        self.assertEqual(entity_labels["tradition_family"], "詩歌の伝統")

        suspect = re.compile(r"型譜|別字段|規則詩|口演・歌唱詩形|詩歌伝統|profile|validator|fail-open")
        for value in _strings(world):
            if re.search(r"[ぁ-んァ-ヶ一-龯]", value):
                self.assertIsNone(suspect.search(value), value)

        for record_id in (
            "poetry.general.ballad",
            "poetry.general.ode",
            "poetry.general.elegy",
            "poetry.jp.tanka",
            "poetry.jp.haiku",
            "poetry.jp.senryu",
        ):
            record = get_reference(record_id)
            self.assertTrue({"fixed", "open"} <= set(record["classification"]["formal_constraints"]))
            self.assertGreaterEqual(len(record["structure"]["constraint_profiles"]), 2)

    def test_schema_rejects_invalid_classification(self) -> None:
        invalid = copy.deepcopy(_world_payload())
        invalid["entries"][0]["entity_kind"] = "invented_kind"
        self.assertTrue(validate_against_schema(invalid, "invalid-classification"))

    def test_schema_rejects_top_level_poem_content(self) -> None:
        invalid = copy.deepcopy(_world_payload())
        invalid["entries"][0]["poem_text"] = "保存してはいけない本文"
        self.assertTrue(validate_against_schema(invalid, "top-level-content"))

    def test_schema_rejects_unknown_structure_field(self) -> None:
        invalid = copy.deepcopy(_world_payload())
        invalid["entries"][0]["structure"]["invented_structure_field"] = "不明な項目"
        self.assertTrue(validate_against_schema(invalid, "unknown-structure"))

    def test_schema_rejects_nested_poem_content(self) -> None:
        invalid = copy.deepcopy(_world_payload())
        haiku = next(row for row in invalid["entries"] if row["id"] == "poetry.jp.haiku")
        haiku["structure"]["constraint_profiles"][0]["poem_text"] = "保存してはいけない本文"
        self.assertTrue(validate_against_schema(invalid, "nested-content"))

    def test_schema_rejects_invalid_constraint_profile(self) -> None:
        invalid = copy.deepcopy(_world_payload())
        haiku = next(row for row in invalid["entries"] if row["id"] == "poetry.jp.haiku")
        haiku["structure"]["constraint_profiles"][0]["formal_constraints"] = ["invented"]
        self.assertTrue(validate_against_schema(invalid, "invalid-profile"))

    def test_manual_research_resources_are_not_runtime_inputs(self) -> None:
        assessment = get_reference("jp.nier.national-assessment-japanese")
        self.assertEqual(assessment["acquisition_mode"], "manual_research_only")
        self.assertFalse(assessment["local_runtime_allowed"])
        self.assertFalse(assessment["external_runtime_fetch_allowed"])

    def test_editorial_school_grammar_claims_are_explicitly_marked(self) -> None:
        grammar = json.loads(
            (ROOT / "reference/language_education_and_poetry/japanese_grammar.json").read_text(
                encoding="utf-8"
            )
        )
        curriculum_records = [
            record
            for record in grammar["entries"]
            if record["structured_data"].get("source_scope")
            == "curriculum_statement"
        ]
        self.assertEqual(len(curriculum_records), 10)
        self.assertTrue(
            all(
                record["definition_status"] == "editorial_synthesis"
                and all(
                    rule["claim_status"] == "editorial_guardrail"
                    for rule in record["rules"]
                )
                for record in curriculum_records
            )
        )

    def test_linked_verse_claims_have_direct_institutional_sources(self) -> None:
        poetry = json.loads(
            (ROOT / "reference/language_education_and_poetry/japanese_poetry_forms.json").read_text(
                encoding="utf-8"
            )
        )
        by_id = {record["id"]: record for record in poetry["entries"]}
        self.assertIn(
            "src.tmd.renga-renku",
            {row["source_id"] for row in by_id["knowledge.poetry.japanese.renku"]["source_refs"]},
        )
        self.assertIn(
            "src.iwate.haikai",
            {row["source_id"] for row in by_id["knowledge.poetry.japanese.hokku"]["source_refs"]},
        )
        self.assertNotIn(
            "五十韻",
            json.dumps(by_id["knowledge.poetry.japanese.renga"], ensure_ascii=False),
        )
        self.assertNotIn(
            "季・月・花・恋",
            json.dumps(by_id["knowledge.poetry.japanese.renku"], ensure_ascii=False),
        )
        self.assertNotIn(
            "俳文",
            json.dumps(by_id["knowledge.poetry.japanese.haikai"], ensure_ascii=False),
        )
        self.assertNotIn(
            "internal_rhythm_common",
            by_id["knowledge.poetry.japanese.dodoitsu"]["structured_data"],
        )
        self.assertEqual(
            by_id["knowledge.poetry.japanese.kyoka"]["rules"][0]["claim_status"],
            "editorial_synthesis",
        )


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return [value] if isinstance(value, str) else []


def _world_payload() -> dict[str, object]:
    return json.loads(
        (ROOT / "reference/language_education_and_poetry/world_poetry.json").read_text(
            encoding="utf-8"
        )
    )


if __name__ == "__main__":
    unittest.main()
