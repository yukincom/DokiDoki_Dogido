from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dev_tools.catalog_tools.language_knowledge import (
    _safe_dataset_path,
    get_bulk_knowledge,
    get_kanji_profile,
    load_bulk_index,
    search_bulk_knowledge,
    search_japanese_knowledge,
)
from scripts import build_language_knowledge_data as language_builder
from scripts import build_reference_index as reference_index_builder
from scripts.validate_reference_catalog import (
    _normalized_index_entry_issues,
    _normalized_record_issues,
)


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference" / "language_education_and_poetry"


class LanguageKnowledgeTests(unittest.TestCase):
    def test_normalized_data_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/build_language_knowledge_data.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manifest_records_actual_official_datasets(self) -> None:
        manifest = json.loads(
            (REFERENCE_DIR / "data" / "manifest.json").read_text(encoding="utf-8")
        )
        counts = {
            row["id"]: row["record_count"]
            for row in manifest["normalized_datasets"]
        }
        self.assertEqual(
            counts,
            {
                "grammar_patterns": 800,
                "education_basic_vocabulary": 27_234,
                "japanese_education_basic_vocabulary": 11_826,
                "historical_hiragana_unicode": 287,
                "curriculum_japanese": 1_859,
                "joyo_kanji": 2_136,
                "grade_level_kanji_allocation": 1_026,
            },
        )
        self.assertFalse(manifest["runtime_network_access"])
        self.assertTrue(
            all(
                row["license"] == "CC BY 4.0"
                for row in manifest["raw_datasets"]
                if row["source_id"].startswith("src.ninjal.")
            )
        )
        unicode_data = next(
            row
            for row in manifest["raw_datasets"]
            if row["id"] == "unicode_character_database_17_0_0"
        )
        self.assertEqual(unicode_data["license"], "Unicode License v3")
        self.assertEqual(unicode_data["version"], "17.0.0")
        self.assertIn("license_sha256", unicode_data)
        self.assertEqual(
            manifest["editorial_inputs"][0]["id"],
            "grade_level_kanji_allocation_transcription",
        )

    def test_searches_joyo_kanji_and_grade_level_allocation(self) -> None:
        joyo = search_bulk_knowledge(
            "亜",
            dataset_ids=["joyo_kanji"],
            limit=10,
        )
        self.assertGreaterEqual(len(joyo), 1)
        self.assertEqual(joyo[0]["id"], "kanji.joyo.u4e9c")
        self.assertEqual(joyo[0]["character"], "亜")
        self.assertEqual(joyo[0]["codepoint"], "U+4E9C")
        self.assertEqual(joyo[0]["parenthesized_printed_forms"], ["亞"])
        self.assertEqual(
            joyo[0]["readings"],
            [{"order": 1, "reading": "ア", "reading_type_ja": "音読み"}],
        )

        fourth_grade = search_bulk_knowledge(
            "第4学年",
            dataset_ids=["grade_level_kanji_allocation"],
            limit=300,
        )
        self.assertEqual(len(fourth_grade), 202)
        self.assertTrue(
            all(record["school_grade"] == 4 for record in fourth_grade)
        )
        self.assertTrue(
            all(record["allocation_scope"] == "character_only" for record in fourth_grade)
        )

        profile = get_kanji_profile("一")
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["joyo_kanji"]["dataset_id"], "joyo_kanji")
        self.assertEqual(profile["joyo_kanji"]["source_id"], "src.bunka.joyo-kanji-table")
        self.assertEqual(
            profile["grade_level_kanji_allocation"]["school_grade"],
            1,
        )
        self.assertEqual(
            profile["grade_level_kanji_allocation"]["dataset_id"],
            "grade_level_kanji_allocation",
        )
        self.assertEqual(profile["source_boundary"], "separate_official_tables")

    def test_searches_historical_kana_makurakotoba_and_senryu_rules(self) -> None:
        obsolete_kana = search_japanese_knowledge("ゐ", limit=10)
        wi = next(
            row
            for row in obsolete_kana
            if row["id"] == "knowledge.orthography.kana.wi-to-i"
        )
        self.assertEqual(wi["structured_data"]["modern_candidate"], "い")
        self.assertTrue(wi["machine_use"]["candidate_only"])

        makurakotoba = search_japanese_knowledge("枕詞", limit=20)
        self.assertEqual(makurakotoba[0]["id"], "knowledge.rhetoric.makurakotoba")
        self.assertIn(
            "knowledge.rhetoric.makura.ashihikino",
            {row["id"] for row in makurakotoba},
        )

        senryu = search_japanese_knowledge("川柳", limit=10)[0]
        self.assertEqual(senryu["id"], "knowledge.poetry.japanese.senryu")
        self.assertIn(
            "季語の不在だけで川柳と確定する",
            senryu["machine_use"]["prohibited_inferences"],
        )

    def test_searches_curated_grammar_and_matching_curriculum_rows(self) -> None:
        rows = search_japanese_knowledge("主語と述語", limit=10)
        self.assertEqual(rows[0]["id"], "knowledge.grammar.subject-predicate")
        self.assertIn(
            "curriculum_japanese",
            {row["dataset_id"] for row in rows},
        )

    def test_searches_ninjal_grammar_patterns_and_vocabulary(self) -> None:
        patterns = search_bulk_knowledge(
            "〜あげく",
            dataset_ids=["grammar_patterns"],
            limit=10,
        )
        self.assertGreaterEqual(len(patterns), 2)
        self.assertEqual(patterns[0]["kind"], "grammar_pattern")
        self.assertTrue(patterns[0]["senses"])
        self.assertEqual(patterns[0]["license"], "CC BY 4.0")

        vocabulary = search_bulk_knowledge(
            "せんりゅう",
            dataset_ids=["education_basic_vocabulary"],
            limit=10,
        )
        self.assertEqual(len(vocabulary), 1)
        self.assertEqual(vocabulary[0]["headword"], "せんりゅう")

        exact = search_bulk_knowledge(
            "あさ",
            dataset_ids=["education_basic_vocabulary"],
            limit=1,
        )
        self.assertEqual(exact[0]["title_ja"], "あさ")

        deduplicated = search_bulk_knowledge(
            "〜あげく",
            dataset_ids=["grammar_patterns", "grammar_patterns"],
            limit=10,
        )
        self.assertEqual(
            len(deduplicated), len({record["id"] for record in deduplicated})
        )

    def test_kind_generator_filters_both_curated_and_bulk_results(self) -> None:
        rows = search_japanese_knowledge(
            "主語と述語",
            kinds=(value for value in ["grammar_rule"]),
            limit=10,
        )
        self.assertTrue(rows)
        self.assertTrue(all(row["kind"] == "grammar_rule" for row in rows))

    def test_integrated_search_normalizes_dataset_ids(self) -> None:
        rows = search_japanese_knowledge(
            "〜あげく",
            dataset_ids=[" GRAMMAR_PATTERNS "],
            limit=2,
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["dataset_id"] == "grammar_patterns" for row in rows))

    def test_integrated_search_ranks_exact_bulk_title_before_core_partial(self) -> None:
        row = search_japanese_knowledge("音", limit=1)[0]
        self.assertEqual(row["dataset_id"], "curriculum_japanese")
        self.assertEqual(row["title_ja"], "音")

    def test_builder_rejects_symlinked_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with mock.patch.object(language_builder, "ROOT", root):
                with self.assertRaises(ValueError):
                    language_builder._validate_output_path(root / "linked" / "data.jsonl")

    def test_reference_index_builder_rejects_symlinked_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "linked.json").symlink_to(outside / "index.json")
            with mock.patch.object(reference_index_builder, "ROOT", root):
                with self.assertRaises(ValueError):
                    reference_index_builder._validate_output_path(root / "linked.json")

    def test_builder_rejects_duplicate_vocabulary_serials(self) -> None:
        duplicate_a = [
            {"通し番号": "1", "見出し": "あ", "表記": "あ", "品詞": "1"},
            {"通し番号": "1", "見出し": "い", "表記": "い", "品詞": "1"},
        ]
        single_b = [
            {"通し番号": "1", "見出し": "あ", "表記": "あ", "品詞": "1"}
        ]
        with mock.patch.object(
            language_builder,
            "_read_csv",
            side_effect=[duplicate_a, single_b],
        ):
            with self.assertRaisesRegex(ValueError, "通し番号が重複"):
                language_builder._education_vocabulary_records()

    def test_normalized_schema_rejects_unstable_ids_and_kinds(self) -> None:
        entry = {
            "id": "Grammar.pattern.0001",
            "dataset_id": "grammar_patterns",
            "dataset_path": "grammar_patterns.jsonl",
            "byte_offset": 0,
            "byte_length": 10,
            "kind": "invented_kind",
            "title_ja": "例",
            "search_terms": ["例"],
        }
        record = {
            "id": entry["id"],
            "kind": entry["kind"],
            "title_ja": "例",
            "search_terms": ["例"],
            "source_id": "src.ninjal.sentence-patterns",
            "license": "CC BY 4.0",
        }
        self.assertTrue(
            _normalized_index_entry_issues(
                entry,
                expected_dataset_id="grammar_patterns",
            )
        )
        self.assertTrue(
            _normalized_record_issues(
                record,
                expected_dataset_id="grammar_patterns",
            )
        )

    def test_grammar_markup_is_normalized_and_attribution_is_complete(self) -> None:
        path = REFERENCE_DIR / "data" / "normalized" / "grammar_patterns.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        serialized = json.dumps(records, ensure_ascii=False)
        for raw_marker in ("〓", "<s>", "</s>", "</s/>"):
            self.assertNotIn(raw_marker, serialized)

        self.assertTrue(
            any(
                connection["connection_type"].get("deleted_segments")
                for record in records
                for sense in record["senses"]
                for connection in sense["connections"]
            )
        )
        self.assertTrue(
            all(
                record["source_creators"]
                == ["パルデシ, プラシャント", "砂川, 有里子"]
                and record["source_doi"] == "10.15084/0002000610"
                and record["license_url"]
                == "https://creativecommons.org/licenses/by/4.0/"
                for record in records
            )
        )

    def test_searches_actual_hentaigana_codepoints(self) -> None:
        rows = search_bulk_knowledge(
            "𛀂",
            dataset_ids=["historical_hiragana_unicode"],
            limit=10,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["codepoint"], "U+1B002")
        self.assertEqual(rows[0]["unicode_name"], "HENTAIGANA LETTER A-1")
        self.assertEqual(rows[0]["romanized_name_components"], ["A"])
        self.assertEqual(rows[0]["modern_search_candidates"], ["あ"])
        self.assertNotIn("historical_kana_candidates", rows[0])
        self.assertTrue(rows[0]["candidate_only"])

        archaic_wu = search_bulk_knowledge(
            "U+1B11F",
            dataset_ids=["historical_hiragana_unicode"],
            limit=10,
        )
        self.assertEqual(len(archaic_wu), 1)
        self.assertEqual(archaic_wu[0]["modern_search_candidates"], ["う"])

    def test_partial_search_does_not_hide_requested_dataset(self) -> None:
        # 「敬語」は文型の分類語にも存在する。完全一致候補が別データセットに
        # あっても、指定した学習指導要領の部分一致候補を落としてはならない。
        rows = search_bulk_knowledge(
            "敬語",
            dataset_ids=["curriculum_japanese"],
            limit=20,
        )
        self.assertGreaterEqual(len(rows), 4)
        self.assertTrue(all(row["subject"] == "国語" for row in rows))
        self.assertTrue(all("敬語" in row["curriculum_text"] for row in rows))

    def test_bulk_data_file_is_hashed_once_per_search(self) -> None:
        from dev_tools.catalog_tools import language_knowledge

        with mock.patch.object(
            language_knowledge,
            "_sha256_handle",
            wraps=language_knowledge._sha256_handle,
        ) as digest:
            rows = search_bulk_knowledge(
                "",
                dataset_ids=["education_basic_vocabulary"],
                limit=100,
            )
        self.assertEqual(len(rows), 100)
        # 一回はデータセット索引、一回はデータ本体。同じ本体を100回読まない。
        self.assertEqual(digest.call_count, 2)

    def test_byte_locator_lookup_returns_the_indexed_record(self) -> None:
        hit = search_bulk_knowledge(
            "〜あげく",
            dataset_ids=["grammar_patterns"],
            limit=1,
        )[0]
        record = get_bulk_knowledge(hit["id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["id"], hit["id"])
        self.assertEqual(record["dataset_id"], "grammar_patterns")

    def test_normalized_dataset_path_rejects_traversal(self) -> None:
        for unsafe in (
            "../outside.jsonl",
            "/tmp/outside.jsonl",
            "a/b.jsonl",
            "a\\outside.jsonl",
            "outside.txt",
            "grammar_patterns.index.jsonl",
        ):
            with self.assertRaises(ValueError):
                _safe_dataset_path(REFERENCE_DIR, unsafe)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            normalized = base / "data" / "normalized"
            normalized.mkdir(parents=True)
            outside = base / "outside.jsonl"
            outside.write_text("{}\n", encoding="utf-8")
            (normalized / "linked.jsonl").symlink_to(outside)
            with self.assertRaises(ValueError):
                _safe_dataset_path(base, "linked.jsonl")

            inside = normalized / "inside.jsonl"
            inside.write_text("{}\n", encoding="utf-8")
            (normalized / "same-directory-link.jsonl").symlink_to(inside)
            with self.assertRaises(ValueError):
                _safe_dataset_path(base, "same-directory-link.jsonl")

    def test_bulk_index_fails_closed_on_generation_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            normalized = base / "data" / "normalized"
            normalized.mkdir(parents=True)
            (normalized / "sample.jsonl").write_text("{}\n", encoding="utf-8")
            (normalized / "sample.index.jsonl").write_text("{}\n", encoding="utf-8")
            index = {
                "schema_version": 2,
                "datasets": [
                    {
                        "id": "sample",
                        "path": "sample.jsonl",
                        "index_path": "sample.index.jsonl",
                        "record_count": 1,
                        "sha256": "0" * 64,
                        "index_sha256": "0" * 64,
                    }
                ],
            }
            (normalized / "index.json").write_text(
                json.dumps(index),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_bulk_index(base)

    def test_running_process_rejects_mixed_generation_then_reloads_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            normalized = base / "data" / "normalized"
            normalized.mkdir(parents=True)

            def publish(title: str, *, top_index: bool = True) -> None:
                record = {
                    "id": "sample.record",
                    "kind": "sample_kind",
                    "title_ja": title,
                    "search_terms": [title],
                }
                data = (json.dumps(record, ensure_ascii=False) + "\n").encode()
                entry = {
                    "id": record["id"],
                    "dataset_id": "sample",
                    "dataset_path": "sample.jsonl",
                    "byte_offset": 0,
                    "byte_length": len(data),
                    "kind": record["kind"],
                    "title_ja": title,
                    "search_terms": [title],
                }
                index_data = (json.dumps(entry, ensure_ascii=False) + "\n").encode()
                data_path = normalized / "sample.jsonl"
                data_path.write_bytes(data)
                if not top_index:
                    return
                index_path = normalized / "sample.index.jsonl"
                index_path.write_bytes(index_data)
                top = {
                    "schema_version": 2,
                    "datasets": [
                        {
                            "id": "sample",
                            "path": data_path.name,
                            "index_path": index_path.name,
                            "record_count": 1,
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "index_sha256": hashlib.sha256(index_data).hexdigest(),
                        }
                    ],
                }
                top_path = normalized / "index.json"
                temporary = normalized / "index.next.json"
                temporary.write_text(json.dumps(top), encoding="utf-8")
                os.replace(temporary, top_path)

            publish("旧題")
            old = search_bulk_knowledge("旧題", reference_dir=base, limit=1)
            self.assertEqual(old[0]["title_ja"], "旧題")

            # dataだけが次世代へ替わった窓では、cached旧索引で読み進めない。
            publish("新題", top_index=False)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                search_bulk_knowledge("旧題", reference_dir=base, limit=1)

            publish("新題")
            new = search_bulk_knowledge("新題", reference_dir=base, limit=1)
            self.assertEqual(new[0]["title_ja"], "新題")

    def test_core_only_search_does_not_load_bulk_index(self) -> None:
        with mock.patch(
            "dev_tools.catalog_tools.language_knowledge.load_bulk_index",
            side_effect=AssertionError("bulk index must not be loaded"),
        ):
            rows = search_japanese_knowledge(
                "川柳",
                dataset_ids=["japanese_poetry_forms"],
                limit=10,
            )
        self.assertEqual(rows[0]["id"], "knowledge.poetry.japanese.senryu")

    def test_streaming_indexes_cover_and_locate_every_normalized_record(self) -> None:
        normalized_dir = REFERENCE_DIR / "data" / "normalized"
        main_index_path = normalized_dir / "index.json"
        self.assertLess(main_index_path.stat().st_size, 64_000)
        index = json.loads(main_index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["schema_version"], 2)
        source_ids = {
            row["id"]
            for row in json.loads(
                (REFERENCE_DIR / "sources.json").read_text(encoding="utf-8")
            )["sources"]
        }
        seen_ids: set[str] = set()
        total = 0
        required_record_fields = {
            "id",
            "kind",
            "title_ja",
            "search_terms",
            "source_id",
            "license",
        }
        for dataset in index["datasets"]:
            data_path = normalized_dir / dataset["path"]
            dataset_index_path = normalized_dir / dataset["index_path"]
            entries = [
                json.loads(line)
                for line in dataset_index_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(entries), dataset["record_count"])
            with data_path.open("rb") as handle:
                for entry in entries:
                    self.assertEqual(entry["dataset_id"], dataset["id"])
                    self.assertEqual(entry["dataset_path"], dataset["path"])
                    self.assertEqual(
                        len(entry["search_terms"]), len(set(entry["search_terms"]))
                    )
                    handle.seek(entry["byte_offset"])
                    record = json.loads(handle.read(entry["byte_length"]))
                    self.assertEqual(record["id"], entry["id"])
                    self.assertTrue(required_record_fields <= record.keys())
                    self.assertIn(record["source_id"], source_ids)
                    self.assertEqual(
                        len(record["search_terms"]), len(set(record["search_terms"]))
                    )
                    self.assertNotIn(record["id"], seen_ids)
                    seen_ids.add(record["id"])
                    total += 1
        self.assertEqual(total, 45_168)


if __name__ == "__main__":
    unittest.main()
