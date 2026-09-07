"""Minecraft Java 公式技術データベースの完全性と利用境界を検証する。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator, FormatChecker

from dogido_server import minecraft_knowledge


REFERENCE_DIR = ROOT / "reference" / "minecraft_technical"
SCHEMA_PATH = REFERENCE_DIR / "schema.json"
GRADLE_PROPERTIES = ROOT / "adapter" / "minecraft-fabric" / "gradle.properties"
EXPECTED_COUNTS = {
    "version_information": 1,
    "official_changes": 25,
    "registry_entries": 6_726,
    "datapack_entries": 5_671,
    "tag_definitions": 625,
}
EXPECTED_CLASSIFICATIONS = {
    "distribution_version": "配布版情報",
    "official_change": "公式変更事項",
    "registry_entry": "レジストリ項目",
    "datapack_entry": "データパック項目",
    "tag_definition": "タグ定義",
}
OFFICIAL_HOSTS = {
    "piston-meta.mojang.com",
    "piston-data.mojang.com",
    "resources.download.minecraft.net",
    "www.minecraft.net",
}
TRACKED_EXTENSIONS_FORBIDDEN = {".jar", ".class", ".nbt", ".mcmeta"}


def _adapter_property(name: str) -> str:
    prefix = name + "="
    for line in GRADLE_PROPERTIES.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    raise ValueError(f"gradle.propertiesに{name}がありません")


def _safe_source_path(value: object) -> bool:
    raw = str(value)
    path = PurePosixPath(raw)
    return bool(
        raw
        and "\\" not in raw
        and not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts)
    )


def _strict_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, 1):
            value = minecraft_knowledge._loads_json(
                line,
                label=f"{path.name}:{line_number}",
            )
            if not isinstance(value, dict):
                raise ValueError(f"JSONL行がオブジェクトではありません: {path}:{line_number}")
            records.append(value)
    return records


def _official_url_issues(lock: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    urls: list[tuple[str, str]] = []
    for name, row in lock.get("official_artifacts", {}).items():
        if isinstance(row, dict) and isinstance(row.get("url"), str):
            urls.append((f"official_artifacts.{name}", row["url"]))
    for name, value in lock.get("official_documents", {}).items():
        if isinstance(value, str):
            urls.append((f"official_documents.{name}", value))
    for label, url in urls:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
            issues.append(f"公式管理下でないURLです: {label}: {url}")
    return issues


def _tracked_storage_issues() -> list[str]:
    issues: list[str] = []
    for path in REFERENCE_DIR.rglob("*"):
        if path.is_symlink():
            issues.append(f"追跡対象参照資料にシンボリックリンクがあります: {path}")
        if path.is_file() and path.suffix.lower() in TRACKED_EXTENSIONS_FORBIDDEN:
            issues.append(f"ゲーム配布物らしいファイルを追跡対象へ置いています: {path}")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if ".dogido_reference/" not in {line.strip() for line in gitignore}:
        issues.append(".dogido_reference/ が.gitignoreにありません")
    return issues


def _record_dataset_matches(dataset_id: str, record_type: object) -> bool:
    return {
        "version_information": "distribution_version",
        "official_changes": "official_change",
        "registry_entries": "registry_entry",
        "datapack_entries": "datapack_entry",
        "tag_definitions": "tag_definition",
    }.get(dataset_id) == record_type


def validate(cache_root: Path | None = None) -> list[str]:
    issues: list[str] = []
    try:
        context = minecraft_knowledge._load_context(cache_root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [str(error)]
    lock = context["source_lock"]
    manifest = context["manifest"]
    schema = minecraft_knowledge._load_json(SCHEMA_PATH)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    if _adapter_property("minecraft_version") != lock.get("minecraft_version"):
        issues.append("Fabricアダプターとsource_lockのMinecraft版が一致しません")
    compatibility = lock.get("adapter_compatibility", {})
    for property_name, lock_name in (
        ("yarn_mappings", "yarn_mappings"),
        ("loader_version", "fabric_loader"),
        ("fabric_api_version", "fabric_api"),
    ):
        if _adapter_property(property_name) != compatibility.get(lock_name):
            issues.append(f"Fabricアダプターとsource_lockの{property_name}が一致しません")
    issues.extend(_official_url_issues(lock))
    issues.extend(_tracked_storage_issues())

    normalization_inputs = lock.get("normalization_inputs", {})
    expected_normalization_files = {
        "record_schema_path": ROOT / "reference" / "minecraft_technical" / "schema.json",
        "official_changes_path": ROOT
        / "reference"
        / "minecraft_technical"
        / "official_changes.json",
        "generator_path": ROOT / "scripts" / "build_minecraft_technical_data.py",
    }
    for path_key, path in expected_normalization_files.items():
        hash_key = path_key.removesuffix("_path") + "_sha256"
        if (
            normalization_inputs.get(path_key) != path.relative_to(ROOT).as_posix()
            or minecraft_knowledge._sha256(path) != normalization_inputs.get(hash_key)
        ):
            issues.append(f"正規化入力の固定値が不一致です: {path_key}")
    if (
        manifest.get("normalization_revision") != lock.get("normalization_revision")
        or manifest.get("normalization_inputs") != normalization_inputs
        or manifest.get("source_manifest_state") != lock.get("manifest_state")
    ):
        issues.append("manifestの正規化版または固定manifest観測値がsource_lockと不一致です")

    actual_counts = {
        row.get("id"): row.get("record_count")
        for row in manifest.get("datasets", [])
        if isinstance(row, dict)
    }
    if actual_counts != EXPECTED_COUNTS:
        issues.append(f"データセット件数が想定と異なります: {actual_counts}")
    summary = manifest.get("generator_summary", {})
    expected_summary = {
        "report_file_count": 9,
        "registry_count": 95,
        "official_change_count": 25,
        "registry_entry_count": 6_726,
        "datapack_entry_count": 5_671,
        "tag_count": 625,
        "direct_tag_reference_count": 4_377,
        "locale_key_count": 8_524,
        "raw_generator_outputs_stored": False,
        "generator_cache_included": False,
    }
    if summary != expected_summary:
        issues.append(f"生成集計が想定と異なります: {summary}")

    expected_files = {"manifest.json"}
    seen_ids: set[str] = set()
    known_entries: dict[str, set[str]] = {}
    static_registry_ids: set[str] = set()
    protocol_ids: dict[str, set[int]] = {}
    representative: dict[tuple[str, str], dict[str, Any]] = {}
    official_sha1s = {
        row["sha1"]
        for row in lock.get("official_artifacts", {}).values()
        if isinstance(row, dict) and isinstance(row.get("sha1"), str)
    }

    for dataset_id in minecraft_knowledge.EXPECTED_DATASET_IDS:
        dataset = context["datasets"][dataset_id]
        data_path: Path = dataset["_data_path"]
        index_path: Path = dataset["_index_path"]
        expected_files.update({data_path.name, index_path.name})
        try:
            index_records = _strict_json_lines(index_path)
        except (OSError, ValueError) as error:
            issues.append(str(error))
            continue
        if len(index_records) != dataset["record_count"]:
            issues.append(f"索引件数がmanifestと一致しません: {dataset_id}")
        expected_offset = 0
        with data_path.open("rb") as data_handle:
            for position, index_entry in enumerate(index_records, 1):
                entry_id = index_entry.get("id")
                offset = index_entry.get("byte_offset")
                length = index_entry.get("byte_length")
                label = f"{dataset_id}:{position}:{entry_id}"
                if (
                    index_entry.get("dataset_id") != dataset_id
                    or index_entry.get("dataset_path") != dataset["path"]
                    or not isinstance(offset, int)
                    or not isinstance(length, int)
                    or offset != expected_offset
                    or length < 2
                ):
                    issues.append(f"索引行または連続バイト位置が不正です: {label}")
                    continue
                data_handle.seek(offset)
                raw = data_handle.read(length)
                expected_offset += length
                try:
                    record = minecraft_knowledge._loads_json(raw, label=label)
                except ValueError as error:
                    issues.append(str(error))
                    continue
                if not isinstance(record, dict) or record.get("id") != entry_id:
                    issues.append(f"索引と本体のIDが一致しません: {label}")
                    continue
                if entry_id in seen_ids:
                    issues.append(f"レコードIDが重複しています: {entry_id}")
                elif isinstance(entry_id, str):
                    seen_ids.add(entry_id)
                if not _record_dataset_matches(dataset_id, record.get("record_type")):
                    issues.append(f"データセットとレコード種別が一致しません: {label}")
                if record.get("classification_ja") != EXPECTED_CLASSIFICATIONS.get(
                    record.get("record_type")
                ):
                    issues.append(f"分類名が正規値ではありません: {label}")
                if (
                    index_entry.get("record_type") != record.get("record_type")
                    or index_entry.get("registry_id") != record.get("registry_id")
                    or index_entry.get("title_ja") != record.get("title_ja")
                    or index_entry.get("search_terms") != record.get("search_terms")
                ):
                    issues.append(f"索引フィールドと本体が一致しません: {label}")
                errors = sorted(
                    validator.iter_errors(record), key=lambda error: list(error.path)
                )
                if errors:
                    issues.append(f"スキーマ違反です: {label}: {errors[0].message}")
                sources = record.get("sources")
                if not isinstance(sources, list) or not sources:
                    issues.append(f"出典一覧が不正です: {label}")
                    sources = []
                source_roles: set[str] = set()
                for source in sources:
                    if not isinstance(source, dict):
                        issues.append(f"出典がオブジェクトではありません: {label}")
                        continue
                    role = source.get("role")
                    if isinstance(role, str):
                        source_roles.add(role)
                    if source.get("source_kind") == "official_artifact":
                        if (
                            source.get("artifact_sha1") not in official_sha1s
                            or not _safe_source_path(source.get("relative_path"))
                        ):
                            issues.append(f"配布物の出典位置が不正です: {label}")
                    elif source.get("source_kind") == "official_web_page":
                        parsed = urlparse(str(source.get("url")))
                        if (
                            parsed.scheme != "https"
                            or parsed.hostname != "www.minecraft.net"
                            or source.get("checked_at") != lock.get("checked_at")
                        ):
                            issues.append(f"公式ウェブページの出典が不正です: {label}")
                    else:
                        issues.append(f"出典種別が不正です: {label}")
                required_roles = {
                    "distribution_version": {
                        "distribution_metadata",
                        "runtime_versions",
                        "locale_key_count",
                        "release_notes",
                    },
                    "official_change": {"change_statement"},
                    "registry_entry": {"registration"},
                    "datapack_entry": {"definition"},
                    "tag_definition": {"definition"},
                }.get(str(record.get("record_type")), set())
                if (
                    record.get("record_type") == "registry_entry"
                    and record.get("registry_id") == "minecraft:block"
                ):
                    required_roles.add("block_definition")
                if record.get("registry_id") == "minecraft:item" and record.get(
                    "record_type"
                ) == "registry_entry":
                    required_roles.add("item_definition")
                if record.get("title_ja_status") == "official_translation":
                    required_roles.add("japanese_title")
                if not required_roles <= source_roles:
                    issues.append(
                        f"フィールド別出典が不足しています: {label}: "
                        f"{sorted(required_roles - source_roles)}"
                    )
                if record.get("snapshot_id") != lock.get("snapshot_id"):
                    issues.append(f"スナップショットIDが不一致です: {label}")
                if record.get("minecraft_version") != lock.get("minecraft_version"):
                    issues.append(f"Minecraft版が不一致です: {label}")

                registry_id = record.get("registry_id")
                entry_id_value = record.get("entry_id")
                if (
                    record.get("record_type") == "registry_entry"
                    and isinstance(registry_id, str)
                ):
                    static_registry_ids.add(registry_id)
                if isinstance(registry_id, str) and isinstance(entry_id_value, str):
                    known_entries.setdefault(registry_id, set()).add(entry_id_value)
                    representative[(registry_id, entry_id_value)] = record
                protocol_id = record.get("protocol_id")
                if record.get("record_type") == "registry_entry" and isinstance(protocol_id, int):
                    values = protocol_ids.setdefault(str(registry_id), set())
                    if protocol_id in values:
                        issues.append(f"同一レジストリ内のprotocol_idが重複しています: {label}")
                    values.add(protocol_id)
                tag_id = record.get("tag_id")
                if isinstance(registry_id, str) and isinstance(tag_id, str):
                    representative[(registry_id, f"#{tag_id}")] = record
            data_handle.seek(0, 2)
            if expected_offset != data_handle.tell():
                issues.append(f"索引がデータ本体の全バイトを覆っていません: {dataset_id}")

    snapshot_files = {
        path.name for path in context["snapshot_dir"].iterdir() if path.is_file()
    }
    if snapshot_files != expected_files:
        issues.append(
            "ローカルスナップショットに想定外ファイルがあります: "
            f"{sorted(snapshot_files - expected_files)}"
        )
    if len(static_registry_ids) != 95:
        issues.append(
            f"レジストリ種別数が想定と異なります: {len(static_registry_ids)}"
        )

    diamond = representative.get(("minecraft:item", "minecraft:diamond_sword"))
    if (
        not diamond
        or diamond.get("title_ja") != "ダイヤモンドの剣"
        or diamond.get("title_ja_status") != "official_translation"
        or diamond.get("item_summary", {}).get("max_damage") != 1_561
    ):
        issues.append("ダイヤモンドの剣の代表レコードが不正です")
    stone = representative.get(("minecraft:block", "minecraft:stone"))
    if not stone or stone.get("block_summary", {}).get("state_count") != 1:
        issues.append("石ブロックの代表レコードが不正です")
    zombie = representative.get(("minecraft:entity_type", "minecraft:zombie"))
    if not zombie or zombie.get("title_ja") != "ゾンビ":
        issues.append("ゾンビの代表レコードが不正です")
    plains = representative.get(("minecraft:worldgen/biome", "minecraft:plains"))
    if (
        not plains
        or plains.get("biome_summary", {}).get("has_precipitation") is not True
        or plains.get("biome_summary", {}).get("temperature") != 0.8
    ):
        issues.append("平原バイオームの代表レコードが不正です")
    swords = representative.get(("minecraft:item", "#minecraft:swords"))
    if not swords or not {
        "minecraft:copper_sword",
        "minecraft:diamond_sword",
    } <= set(swords.get("expanded_entry_ids", [])):
        issues.append("剣タグの展開結果が不正です")
    diamond_recipe = representative.get(("minecraft:recipe", "minecraft:diamond_sword"))
    if not diamond_recipe or not {
        "minecraft:stick",
        "#minecraft:diamond_tool_materials",
        "minecraft:diamond_sword",
    } <= set(
        diamond_recipe.get("document_summary", {}).get("referenced_resource_ids", [])
    ):
        issues.append("ダイヤモンドの剣のレシピ要約が不正です")

    change_records = _strict_json_lines(
        context["datasets"]["official_changes"]["_data_path"]
    )
    if any(
        record.get("coverage") != "representative_selection"
        or not isinstance(record.get("coverage_note_ja"), str)
        or not record["coverage_note_ja"].strip()
        for record in change_records
    ):
        issues.append("公式変更事項の収録範囲表示が不正です")
    game_rule_change = next(
        (
            record
            for record in change_records
            if record.get("id") == "minecraft.change.1.21.11.game-rules-registry"
        ),
        None,
    )
    if (
        not game_rule_change
        or game_rule_change.get("identifier_mapping_scope")
        != "representative_selection"
        or not any(
            mapping.get("from") == "doMobSpawning"
            and mapping.get("to") == "minecraft:spawn_mobs"
            for mapping in game_rule_change.get("identifier_mappings", [])
        )
    ):
        issues.append("ゲームルール変更事項の識別子対応が不正です")

    for (registry_id, key), record in representative.items():
        if not key.startswith("#"):
            continue
        missing = set(record.get("expanded_entry_ids", [])) - known_entries.get(
            registry_id, set()
        )
        if missing:
            issues.append(
                f"タグ展開結果に存在しない項目があります: {registry_id}{key}: "
                f"{sorted(missing)[:5]}"
            )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minecraft Java公式技術DBを検証")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=minecraft_knowledge.DEFAULT_CACHE_ROOT,
    )
    args = parser.parse_args(argv)
    issues = validate(args.cache_root.resolve())
    if issues:
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("Minecraft technical database is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
