from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from dev_tools.catalog_tools.minecraft_knowledge import (
    DEFAULT_CACHE_ROOT,
    _safe_artifact_path,
    get_minecraft_knowledge,
    load_minecraft_manifest,
    search_minecraft_knowledge,
)
from scripts import build_minecraft_technical_data as minecraft_builder


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference" / "minecraft_technical"


class MinecraftTrackedReferenceTests(unittest.TestCase):
    def test_source_lock_is_official_and_matches_adapter(self) -> None:
        lock = json.loads((REFERENCE_DIR / "source_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["minecraft_version"], "1.21.11")
        self.assertEqual(
            lock["snapshot_id"],
            "mcjava-1.21.11-0d87fe00-64bb6d76-e9eeaade-b19938a0-n3",
        )
        self.assertEqual(lock["normalization_revision"], 3)
        self.assertEqual(
            lock["official_artifacts"]["server_jar"]["sha1"],
            "64bb6d763bed0a9f1d632ec347938594144943ed",
        )
        self.assertEqual(
            lock["official_artifacts"]["ja_jp"]["sha1"],
            "b19938a0f571a0cb964bed0af01bf39c6a95252c",
        )
        allowed_hosts = {
            "piston-meta.mojang.com",
            "piston-data.mojang.com",
            "resources.download.minecraft.net",
            "www.minecraft.net",
        }
        urls = [
            row["url"]
            for row in lock["official_artifacts"].values()
            if "url" in row
        ] + list(lock["official_documents"].values())
        self.assertTrue(urls)
        self.assertTrue(
            all(
                urlparse(url).scheme == "https"
                and urlparse(url).hostname in allowed_hosts
                for url in urls
            )
        )
        properties = {
            key: value
            for key, value in (
                line.split("=", 1)
                for line in (
                    ROOT / "adapter" / "minecraft-fabric" / "gradle.properties"
                ).read_text(encoding="utf-8").splitlines()
                if "=" in line and not line.startswith("#")
            )
        }
        self.assertEqual(properties["minecraft_version"], lock["minecraft_version"])
        self.assertEqual(
            properties["fabric_api_version"],
            lock["adapter_compatibility"]["fabric_api"],
        )

    def test_tracked_schema_uses_established_japanese_classifications(self) -> None:
        schema = json.loads((REFERENCE_DIR / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(
            schema["properties"]["classification_ja"]["enum"],
            ["配布版情報", "公式変更事項", "レジストリ項目", "データパック項目", "タグ定義"],
        )

    def test_official_changes_declare_representative_coverage(self) -> None:
        payload = json.loads(
            (REFERENCE_DIR / "official_changes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["coverage"], "representative_selection")
        self.assertIn("網羅", payload["coverage_note_ja"])
        self.assertEqual(len(payload["records"]), 25)
        game_rules = next(
            row for row in payload["records"] if row["key"] == "game-rules-registry"
        )
        self.assertEqual(
            game_rules["identifier_mapping_scope"], "representative_selection"
        )

    def test_schema_rejects_mismatched_types_and_unsafe_payloads(self) -> None:
        schema = json.loads((REFERENCE_DIR / "schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        base = {
            "schema_version": "dogido.minecraft-java.v1",
            "id": "minecraft.tag.minecraft.item.minecraft.swords",
            "record_type": "tag_definition",
            "classification_ja": "タグ定義",
            "minecraft_version": "1.21.11",
            "snapshot_id": "mcjava-1.21.11-test-n3",
            "title_ja": "#minecraft:swords",
            "search_terms": ["minecraft:swords"],
            "sources": [
                {
                    "source_kind": "official_artifact",
                    "role": "definition",
                    "artifact_sha1": "0" * 40,
                    "relative_path": "generated/server/data/minecraft/tags/item/swords.json",
                    "json_pointer": "",
                }
            ],
            "registry_id": "minecraft:item",
            "tag_id": "minecraft:swords",
            "pack_scope": "vanilla_base",
            "replace": False,
            "members": [],
            "expanded_entry_ids": [],
        }
        self.assertFalse(list(validator.iter_errors(base)))
        for mutate in (
            lambda row: row.update(classification_ja="データパック項目"),
            lambda row: row.update(replace="false"),
            lambda row: row["sources"][0].update(relative_path="../../server.jar"),
        ):
            candidate = json.loads(json.dumps(base))
            mutate(candidate)
            self.assertTrue(list(validator.iter_errors(candidate)))

    def test_full_game_derived_database_is_not_tracked_content(self) -> None:
        ignored = {
            line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        }
        self.assertIn(".dogido_reference/", ignored)
        forbidden = {".jar", ".class", ".nbt", ".mcmeta"}
        self.assertFalse(
            [
                path
                for path in REFERENCE_DIR.rglob("*")
                if path.is_file() and path.suffix.lower() in forbidden
            ]
        )

    def test_builder_rejects_output_outside_gitignored_cache_before_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_base = root / ".dogido_reference"
            cache_base.mkdir()
            outside = root / "reference" / "minecraft_technical"
            with mock.patch.object(minecraft_builder, "LOCAL_CACHE_BASE", cache_base):
                with self.assertRaisesRegex(ValueError, "配下に限ります"):
                    minecraft_builder._validate_cache_root(outside)
            self.assertFalse(outside.exists())

    def test_reader_rejects_traversal_and_symlinked_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot"
            snapshot.mkdir()
            inside = snapshot / "inside.jsonl"
            inside.write_text("{}\n", encoding="utf-8")
            for unsafe in ("../outside.jsonl", "/tmp/outside.jsonl", "a/b.jsonl", "a\\b.jsonl"):
                with self.assertRaises(ValueError):
                    _safe_artifact_path(snapshot, unsafe, suffix=".jsonl")
            link = snapshot / "linked.jsonl"
            link.symlink_to(inside)
            with self.assertRaises(ValueError):
                _safe_artifact_path(snapshot, link.name, suffix=".jsonl")


@unittest.skipUnless(
    (DEFAULT_CACHE_ROOT / "current.json").is_file(),
    "ローカル専用Minecraft DBは生成器を実行した環境だけで検査する",
)
class MinecraftLocalKnowledgeTests(unittest.TestCase):
    def test_local_database_passes_full_validator(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/validate_minecraft_technical_data.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manifest_has_actual_searchable_record_counts(self) -> None:
        manifest = load_minecraft_manifest()
        self.assertEqual(
            {row["id"]: row["record_count"] for row in manifest["datasets"]},
            {
                "version_information": 1,
                "official_changes": 25,
                "registry_entries": 6_726,
                "datapack_entries": 5_671,
                "tag_definitions": 625,
            },
        )
        self.assertFalse(manifest["runtime_network_access"])
        self.assertFalse(manifest["generator_summary"]["raw_generator_outputs_stored"])
        self.assertEqual(manifest["normalization_revision"], 3)
        self.assertNotIn("observed_latest", manifest)

    def test_searches_official_japanese_item_name_and_item_facts(self) -> None:
        rows = search_minecraft_knowledge("ダイヤモンドの剣", limit=5)
        self.assertEqual(len(rows), 1)
        record = rows[0]
        self.assertEqual(record["entry_id"], "minecraft:diamond_sword")
        self.assertEqual(record["registry_id"], "minecraft:item")
        self.assertEqual(record["classification_ja"], "レジストリ項目")
        self.assertEqual(record["title_ja_status"], "official_translation")
        self.assertEqual(record["item_summary"]["max_stack_size"], 1)
        self.assertEqual(record["item_summary"]["max_damage"], 1_561)

    def test_searches_and_expands_the_official_swords_tag(self) -> None:
        rows = search_minecraft_knowledge(
            "minecraft:swords",
            record_types=["tag_definition"],
            registry_ids=["minecraft:item"],
            limit=5,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["classification_ja"], "タグ定義")
        self.assertEqual(
            rows[0]["expanded_entry_ids"],
            [
                "minecraft:copper_sword",
                "minecraft:diamond_sword",
                "minecraft:golden_sword",
                "minecraft:iron_sword",
                "minecraft:netherite_sword",
                "minecraft:stone_sword",
                "minecraft:wooden_sword",
            ],
        )

    def test_searches_official_change_records_and_recipe_references(self) -> None:
        changes = search_minecraft_knowledge(
            "doMobSpawning",
            record_types=["official_change"],
            limit=5,
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["classification_ja"], "公式変更事項")
        self.assertEqual(changes[0]["coverage"], "representative_selection")
        self.assertIn("網羅", changes[0]["coverage_note_ja"])
        self.assertEqual(
            changes[0]["identifier_mapping_scope"], "representative_selection"
        )
        self.assertIn(
            {
                "from": "doMobSpawning",
                "to": "minecraft:spawn_mobs",
                "value_inverted": False,
            },
            changes[0]["identifier_mappings"],
        )

        recipes = search_minecraft_knowledge(
            "minecraft:diamond_sword",
            dataset_ids=["datapack_entries"],
            registry_ids=["minecraft:recipe"],
            limit=5,
        )
        diamond_sword = next(
            record for record in recipes if record["entry_id"] == "minecraft:diamond_sword"
        )
        self.assertIn(
            "#minecraft:diamond_tool_materials",
            diamond_sword["document_summary"]["referenced_resource_ids"],
        )
        self.assertIn(
            "minecraft:stick",
            diamond_sword["document_summary"]["referenced_resource_ids"],
        )

    def test_gets_biome_summary_by_stable_normalized_id(self) -> None:
        record = get_minecraft_knowledge(
            "minecraft.datapack.minecraft.worldgen.biome.minecraft.plains"
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["entry_id"], "minecraft:plains")
        self.assertEqual(record["classification_ja"], "データパック項目")
        self.assertEqual(
            record["biome_summary"],
            {"downfall": 0.4, "has_precipitation": True, "temperature": 0.8},
        )


if __name__ == "__main__":
    unittest.main()
