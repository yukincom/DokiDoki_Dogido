"""Minecraft Java公式配布物からローカル専用の検索DBを生成する。

ゲームJAR、公式言語ファイル、data generatorの原出力は公開リポジトリへ
保存せず、Git管理外の .dogido_reference 配下に正規化結果だけを置く。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference" / "minecraft_technical"
LOCK_PATH = REFERENCE_DIR / "source_lock.json"
SCHEMA_PATH = REFERENCE_DIR / "schema.json"
DEFAULT_CACHE_ROOT = ROOT / ".dogido_reference" / "minecraft_technical"
LOCAL_CACHE_BASE = ROOT / ".dogido_reference"
GRADLE_PROPERTIES = ROOT / "adapter" / "minecraft-fabric" / "gradle.properties"
RECORD_SCHEMA_VERSION = "dogido.minecraft-java.v1"
ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
RESOURCE_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
EXPECTED_REPORTS = {
    "reports/biome_parameters/minecraft/nether.json",
    "reports/biome_parameters/minecraft/overworld.json",
    "reports/blocks.json",
    "reports/commands.json",
    "reports/datapack.json",
    "reports/items.json",
    "reports/json-rpc-api-schema.json",
    "reports/packets.json",
    "reports/registries.json",
}
DATASETS = (
    ("version_information", "version_information.jsonl"),
    ("official_changes", "official_changes.jsonl"),
    ("registry_entries", "registry_entries.jsonl"),
    ("datapack_entries", "datapack_entries.jsonl"),
    ("tag_definitions", "tag_definitions.jsonl"),
)
OFFICIAL_CHANGES_PATH = REFERENCE_DIR / "official_changes.json"


def _duplicate_checked_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSONオブジェクトのキーが重複しています: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"JSONに非有限数が含まれています: {value}")


def _loads_json(data: str | bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_duplicate_checked_object,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"JSONを厳格に読み取れません: {label}: {error}") from error


def _load_json(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"通常ファイルではありません: {path}")
    return _loads_json(path.read_bytes(), label=path.name)


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _verify_artifact(path: Path, definition: dict[str, Any], label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label}が通常ファイルではありません: {path}")
    if path.stat().st_size != definition["size"]:
        raise ValueError(
            f"{label}のサイズが公式ロックと一致しません: "
            f"{path.stat().st_size} != {definition['size']}"
        )
    actual = _hash_file(path, "sha1")
    if actual != definition["sha1"]:
        raise ValueError(
            f"{label}のSHA-1が公式ロックと一致しません: "
            f"{actual} != {definition['sha1']}"
        )


def _adapter_minecraft_version() -> str:
    return _adapter_property("minecraft_version")


def _adapter_property(name: str) -> str:
    prefix = name + "="
    for line in GRADLE_PROPERTIES.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    raise ValueError(f"gradle.propertiesに{name}がありません")


def _validate_official_urls(lock: dict[str, Any]) -> None:
    artifact_hosts = {
        "version_manifest": "piston-meta.mojang.com",
        "version_json": "piston-meta.mojang.com",
        "client_jar": "piston-data.mojang.com",
        "server_jar": "piston-data.mojang.com",
        "asset_index": "piston-meta.mojang.com",
        "ja_jp": "resources.download.minecraft.net",
    }
    for name, expected_host in artifact_hosts.items():
        url = lock.get("official_artifacts", {}).get(name, {}).get("url")
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            raise ValueError(f"公式配布URLのホストが不正です: {name}: {url}")
    for name, url in lock.get("official_documents", {}).items():
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.hostname != "www.minecraft.net":
            raise ValueError(f"公式文書URLのホストが不正です: {name}: {url}")


def _with_namespace(value: str, default_namespace: str = "minecraft") -> str:
    return value if ":" in value else f"{default_namespace}:{value}"


def _split_resource_id(value: str) -> tuple[str, str]:
    normalized = _with_namespace(value)
    if not RESOURCE_ID_RE.fullmatch(normalized):
        raise ValueError(f"不正なMinecraftリソースIDです: {value!r}")
    return tuple(normalized.split(":", 1))  # type: ignore[return-value]


def _id_component(value: str) -> str:
    namespace, path = _split_resource_id(value)
    return f"{namespace}.{path.replace('/', '.')}"


def _json_pointer_component(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _unique_terms(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _artifact_source(
    role: str,
    artifact_sha1: str,
    relative_path: str,
    json_pointer: str,
) -> dict[str, str]:
    return {
        "source_kind": "official_artifact",
        "role": role,
        "artifact_sha1": artifact_sha1,
        "relative_path": relative_path,
        "json_pointer": json_pointer,
    }


def _web_source(
    role: str,
    url: str,
    section: str,
    checked_at: str,
) -> dict[str, str]:
    return {
        "source_kind": "official_web_page",
        "role": role,
        "url": url,
        "section": section,
        "checked_at": checked_at,
    }


def _translation_key(
    registry_id: str,
    entry_id: str,
    *,
    item_report: dict[str, Any] | None = None,
) -> str | None:
    namespace, path = _split_resource_id(entry_id)
    dotted_path = path.replace("/", ".")
    if registry_id == "minecraft:item" and item_report is not None:
        components = item_report.get("components", {})
        item_name = (
            components.get("minecraft:item_name", {})
            if isinstance(components, dict)
            else {}
        )
        if isinstance(item_name, dict) and isinstance(item_name.get("translate"), str):
            return item_name["translate"]
    prefixes = {
        "minecraft:block": "block",
        "minecraft:item": "item",
        "minecraft:entity_type": "entity",
        "minecraft:mob_effect": "effect",
        "minecraft:worldgen/biome": "biome",
    }
    prefix = prefixes.get(registry_id)
    return f"{prefix}.{namespace}.{dotted_path}" if prefix else None


def _official_title(
    registry_id: str,
    entry_id: str,
    locale: dict[str, Any],
    *,
    item_report: dict[str, Any] | None = None,
) -> tuple[str, str | None, str]:
    key = _translation_key(
        registry_id,
        entry_id,
        item_report=item_report,
    )
    translated = locale.get(key) if key else None
    if isinstance(translated, str) and translated.strip():
        return translated, key, "official_translation"
    return entry_id, key, "identifier_fallback"


def _run_data_generator(
    *,
    java: str,
    server_jar: Path,
    bundler_dir: Path,
    output_dir: Path,
    mode: str,
    working_dir: Path,
) -> None:
    command = [
        java,
        f"-DbundlerRepoDir={bundler_dir}",
        "-DbundlerMainClass=net.minecraft.data.Main",
        "-jar",
        str(server_jar),
        mode,
        "--output",
        str(output_dir),
    ]
    result = subprocess.run(
        command,
        cwd=working_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Minecraft公式data generatorが失敗しました ({mode})\n"
            f"{result.stdout}\n{result.stderr}"
        )


def _server_version_info(
    bundler_dir: Path,
    minecraft_version: str,
) -> dict[str, Any]:
    inner = (
        bundler_dir
        / "versions"
        / minecraft_version
        / f"server-{minecraft_version}.jar"
    )
    if not inner.is_file():
        raise ValueError(f"展開済み公式server JARが見つかりません: {inner}")
    with zipfile.ZipFile(inner) as archive:
        return _loads_json(
            archive.read("version.json"),
            label="server.jar!/version.json",
        )


def _validate_source_chain(
    *,
    lock: dict[str, Any],
    version_manifest: dict[str, Any],
    version_json: dict[str, Any],
    asset_index: dict[str, Any],
    locale: dict[str, Any],
) -> dict[str, Any]:
    minecraft_version = lock["minecraft_version"]
    locked = lock["official_artifacts"]
    manifest_row = next(
        (
            row
            for row in version_manifest.get("versions", [])
            if row.get("id") == minecraft_version
        ),
        None,
    )
    if not isinstance(manifest_row, dict):
        raise ValueError(f"公式version manifestに{minecraft_version}がありません")
    locked_manifest_state = lock["manifest_state"]
    if (
        manifest_row.get("url") != locked["version_json"]["url"]
        or manifest_row.get("sha1") != locked["version_json"]["sha1"]
        or manifest_row.get("type") != locked_manifest_state["target_type"]
    ):
        raise ValueError("公式version manifestの対象版がsource_lockと一致しません")
    if (
        manifest_row.get("time") != locked_manifest_state["target_metadata_time"]
        or manifest_row.get("releaseTime")
        != locked_manifest_state["target_release_time"]
    ):
        raise ValueError("公式version manifestの対象版時刻がsource_lockと一致しません")
    if version_json.get("id") != minecraft_version:
        raise ValueError("version JSONの版がsource_lockと一致しません")
    for key in ("client", "server"):
        actual = version_json.get("downloads", {}).get(key)
        expected = locked[f"{key}_jar"]
        if not isinstance(actual, dict) or any(
            actual.get(field) != expected[field] for field in ("url", "sha1", "size")
        ):
            raise ValueError(f"version JSONの{key}情報がsource_lockと一致しません")
    actual_asset_index = version_json.get("assetIndex")
    expected_asset_index = locked["asset_index"]
    if not isinstance(actual_asset_index, dict) or any(
        actual_asset_index.get(field) != expected_asset_index[field]
        for field in ("id", "url", "sha1", "size")
    ):
        raise ValueError("version JSONのasset indexがsource_lockと一致しません")
    locale_row = asset_index.get("objects", {}).get(locked["ja_jp"]["asset_key"])
    if not isinstance(locale_row, dict) or any(
        locale_row.get(field) != locked["ja_jp"][locked_field]
        for field, locked_field in (("hash", "sha1"), ("size", "size"))
    ):
        raise ValueError("asset indexのja_jpがsource_lockと一致しません")
    if not isinstance(locale, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in locale.items()
    ):
        raise ValueError("ja_jp.jsonが文字列辞書ではありません")
    if version_json.get("javaVersion", {}).get("majorVersion") != lock[
        "adapter_compatibility"
    ]["java_major_version"]:
        raise ValueError("version JSONのJava版がsource_lockと一致しません")
    return manifest_row


def _base_record(
    *,
    record_id: str,
    record_type: str,
    classification_ja: str,
    title_ja: str,
    search_terms: Iterable[object],
    minecraft_version: str,
    snapshot_id: str,
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "id": record_id,
        "record_type": record_type,
        "classification_ja": classification_ja,
        "minecraft_version": minecraft_version,
        "snapshot_id": snapshot_id,
        "title_ja": title_ja,
        "search_terms": _unique_terms(search_terms),
        "sources": sources,
    }


def _registry_records(
    *,
    registries: dict[str, Any],
    blocks: dict[str, Any],
    items: dict[str, Any],
    locale: dict[str, Any],
    lock: dict[str, Any],
) -> list[dict[str, Any]]:
    minecraft_version = lock["minecraft_version"]
    snapshot_id = lock["snapshot_id"]
    server_sha1 = lock["official_artifacts"]["server_jar"]["sha1"]
    locale_sha1 = lock["official_artifacts"]["ja_jp"]["sha1"]
    records: list[dict[str, Any]] = []
    for registry_id in sorted(registries):
        registry = registries[registry_id]
        entries = registry.get("entries")
        if not RESOURCE_ID_RE.fullmatch(registry_id) or not isinstance(entries, dict):
            raise ValueError(f"registries.jsonのレジストリが不正です: {registry_id}")
        for entry_id in sorted(entries):
            entry = entries[entry_id]
            if not RESOURCE_ID_RE.fullmatch(entry_id) or not isinstance(entry, dict):
                raise ValueError(
                    f"registries.jsonの登録項目が不正です: {registry_id}:{entry_id}"
                )
            item_report = items.get(entry_id) if registry_id == "minecraft:item" else None
            title_ja, translation_key, title_status = _official_title(
                registry_id,
                entry_id,
                locale,
                item_report=item_report if isinstance(item_report, dict) else None,
            )
            sources = [
                _artifact_source(
                    "registration",
                    server_sha1,
                    "generated/reports/registries.json",
                    (
                        f"/{_json_pointer_component(registry_id)}/entries/"
                        f"{_json_pointer_component(entry_id)}"
                    ),
                )
            ]
            if registry_id == "minecraft:block":
                sources.append(
                    _artifact_source(
                        "block_definition",
                        server_sha1,
                        "generated/reports/blocks.json",
                        f"/{_json_pointer_component(entry_id)}",
                    )
                )
            if registry_id == "minecraft:item":
                sources.append(
                    _artifact_source(
                        "item_definition",
                        server_sha1,
                        "generated/reports/items.json",
                        f"/{_json_pointer_component(entry_id)}",
                    )
                )
            if title_status == "official_translation" and translation_key:
                sources.append(
                    _artifact_source(
                        "japanese_title",
                        locale_sha1,
                        lock["official_artifacts"]["ja_jp"]["asset_key"],
                        f"/{_json_pointer_component(translation_key)}",
                    )
                )
            record = _base_record(
                record_id=(
                    f"minecraft.registry.{_id_component(registry_id)}."
                    f"{_id_component(entry_id)}"
                ),
                record_type="registry_entry",
                classification_ja="レジストリ項目",
                title_ja=title_ja,
                search_terms=(
                    entry_id,
                    entry_id.split(":", 1)[1],
                    registry_id,
                    title_ja,
                    translation_key,
                    "レジストリ項目",
                ),
                minecraft_version=minecraft_version,
                snapshot_id=snapshot_id,
                sources=sources,
            )
            record.update(
                {
                    "registry_id": registry_id,
                    "entry_id": entry_id,
                    "protocol_id": entry.get("protocol_id"),
                    "registry_protocol_id": registry.get("protocol_id"),
                    "translation_key": translation_key,
                    "title_ja_status": title_status,
                }
            )
            if registry_id == "minecraft:block":
                block = blocks.get(entry_id)
                if not isinstance(block, dict):
                    raise ValueError(f"blocks.jsonに登録ブロックがありません: {entry_id}")
                definition = block.get("definition", {})
                states = block.get("states", [])
                record["block_summary"] = {
                    "properties": (
                        definition.get("properties", {})
                        if isinstance(definition, dict)
                        else {}
                    ),
                    "state_count": len(states) if isinstance(states, list) else 0,
                    "default_state_id": next(
                        (
                            state.get("id")
                            for state in states
                            if isinstance(state, dict) and state.get("default") is True
                        ),
                        None,
                    ),
                }
            if registry_id == "minecraft:item":
                item = items.get(entry_id)
                if not isinstance(item, dict):
                    raise ValueError(f"items.jsonに登録アイテムがありません: {entry_id}")
                components = item.get("components", {})
                if not isinstance(components, dict):
                    components = {}
                record["item_summary"] = {
                    "max_stack_size": components.get("minecraft:max_stack_size"),
                    "max_damage": components.get("minecraft:max_damage"),
                    "rarity": components.get("minecraft:rarity"),
                    "item_model": components.get("minecraft:item_model"),
                }
            records.append(record)
    if len(registries) != 95 or len(records) != 6_726:
        raise ValueError(
            "1.21.11のレジストリ件数が想定と異なります: "
            f"registries={len(registries)}, entries={len(records)}"
        )
    return records


def _element_registry_prefixes(datapack: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    for registry_id, metadata in datapack.get("registries", {}).items():
        if isinstance(metadata, dict) and metadata.get("elements") is True:
            namespace, path = _split_resource_id(registry_id)
            if namespace != "minecraft":
                raise ValueError(f"想定外の公式レジストリ名前空間です: {registry_id}")
            rows.append((registry_id, tuple(path.split("/"))))
    return sorted(rows, key=lambda row: (-len(row[1]), row[0]))


def _match_registry_prefix(
    parts: tuple[str, ...],
    prefixes: list[tuple[str, tuple[str, ...]]],
) -> tuple[str, tuple[str, ...]] | None:
    for registry_id, prefix in prefixes:
        if parts[: len(prefix)] == prefix and len(parts) > len(prefix):
            return registry_id, prefix
    return None


def _resource_references(value: object) -> list[str]:
    """JSON中の名前空間付きIDとタグ参照だけを、値の転載なしで抽出する。"""

    found: set[str] = set()

    def visit(child: object) -> None:
        if isinstance(child, dict):
            for nested in child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)
        elif isinstance(child, str):
            identifier = child[1:] if child.startswith("#") else child
            if RESOURCE_ID_RE.fullmatch(identifier):
                found.add(child)

    visit(value)
    return sorted(found)


def _document_summary(content: object) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {
            "top_level_fields": [],
            "declared_type": None,
            "top_level_collection_sizes": {},
            "referenced_resource_ids": _resource_references(content),
        }
    declared_type = content.get("type")
    if not isinstance(declared_type, str) or not RESOURCE_ID_RE.fullmatch(declared_type):
        declared_type = None
    return {
        "top_level_fields": sorted(str(key) for key in content),
        "declared_type": declared_type,
        "top_level_collection_sizes": {
            str(key): len(value)
            for key, value in sorted(content.items())
            if isinstance(value, (dict, list))
        },
        "referenced_resource_ids": _resource_references(content),
    }


def _datapack_records(
    *,
    server_output: Path,
    datapack: dict[str, Any],
    locale: dict[str, Any],
    lock: dict[str, Any],
) -> list[dict[str, Any]]:
    prefixes = _element_registry_prefixes(datapack)
    data_root = server_output / "data"
    server_sha1 = lock["official_artifacts"]["server_jar"]["sha1"]
    locale_sha1 = lock["official_artifacts"]["ja_jp"]["sha1"]
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    unmatched: list[str] = []
    for path in sorted(data_root.rglob("*.json")):
        relative = path.relative_to(data_root)
        if path.is_symlink() or len(relative.parts) < 3:
            raise ValueError(f"生成データのパスが不正です: {relative}")
        namespace, *rest_list = relative.parts
        if rest_list[0] in {"tags", "datapacks"}:
            continue
        rest = tuple(rest_list)
        matched = _match_registry_prefix(rest, prefixes)
        if matched is None:
            unmatched.append(relative.as_posix())
            continue
        registry_id, prefix = matched
        entry_parts = list(rest[len(prefix) :])
        entry_parts[-1] = Path(entry_parts[-1]).stem
        entry_id = f"{namespace}:{'/'.join(entry_parts)}"
        _split_resource_id(entry_id)
        if (registry_id, entry_id) in seen:
            raise ValueError(f"データパック項目が重複しています: {registry_id}:{entry_id}")
        seen.add((registry_id, entry_id))
        content = _load_json(path)
        title_ja, translation_key, title_status = _official_title(
            registry_id,
            entry_id,
            locale,
        )
        document_summary = _document_summary(content)
        sources = [
            _artifact_source(
                "definition",
                server_sha1,
                f"generated/server/data/{relative.as_posix()}",
                "",
            )
        ]
        if title_status == "official_translation" and translation_key:
            sources.append(
                _artifact_source(
                    "japanese_title",
                    locale_sha1,
                    lock["official_artifacts"]["ja_jp"]["asset_key"],
                    f"/{_json_pointer_component(translation_key)}",
                )
            )
        record = _base_record(
            record_id=(
                f"minecraft.datapack.{_id_component(registry_id)}."
                f"{_id_component(entry_id)}"
            ),
            record_type="datapack_entry",
            classification_ja="データパック項目",
            title_ja=title_ja,
            search_terms=(
                entry_id,
                entry_id.split(":", 1)[1],
                registry_id,
                title_ja,
                translation_key,
                "データパック項目",
                document_summary["declared_type"],
                *document_summary["top_level_fields"],
                *document_summary["referenced_resource_ids"],
            ),
            minecraft_version=lock["minecraft_version"],
            snapshot_id=lock["snapshot_id"],
            sources=sources,
        )
        record.update(
            {
                "registry_id": registry_id,
                "entry_id": entry_id,
                "pack_scope": "vanilla_base",
                "translation_key": translation_key,
                "title_ja_status": title_status,
                "content_sha256": _hash_file(path, "sha256"),
                "document_summary": document_summary,
            }
        )
        if registry_id == "minecraft:worldgen/biome" and isinstance(content, dict):
            record["biome_summary"] = {
                "has_precipitation": content.get("has_precipitation"),
                "temperature": content.get("temperature"),
                "downfall": content.get("downfall"),
            }
        records.append(record)
    if unmatched:
        raise ValueError(
            "datapack.jsonへ対応しない生成JSONがあります: "
            + ", ".join(unmatched[:10])
        )
    if len(records) != 5_671:
        raise ValueError(
            "1.21.11の通常データパック項目数が想定と異なります: "
            f"{len(records)} != 5671"
        )
    return records


def _tag_registry_prefixes(datapack: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    for registry_id, metadata in datapack.get("registries", {}).items():
        if isinstance(metadata, dict) and metadata.get("tags") is True:
            namespace, path = _split_resource_id(registry_id)
            if namespace == "minecraft":
                rows.append((registry_id, tuple(path.split("/"))))
    for name, metadata in datapack.get("others", {}).items():
        if isinstance(metadata, dict) and metadata.get("tags") is True:
            rows.append((f"minecraft:{name}", (name,)))
    return sorted(rows, key=lambda row: (-len(row[1]), row[0]))


def _tag_records(
    *,
    server_output: Path,
    datapack: dict[str, Any],
    known_entries: dict[str, set[str]],
    lock: dict[str, Any],
) -> list[dict[str, Any]]:
    prefixes = _tag_registry_prefixes(datapack)
    data_root = server_output / "data"
    definitions: dict[tuple[str, str], dict[str, Any]] = {}
    source_paths: dict[tuple[str, str], str] = {}
    for namespace_dir in sorted(data_root.iterdir()):
        tags_root = namespace_dir / "tags"
        if not tags_root.is_dir():
            continue
        namespace = namespace_dir.name
        for path in sorted(tags_root.rglob("*.json")):
            relative = path.relative_to(tags_root)
            matched = _match_registry_prefix(relative.parts, prefixes)
            if matched is None:
                raise ValueError(f"タグのレジストリ種別を確定できません: {relative}")
            registry_id, prefix = matched
            tag_parts = list(relative.parts[len(prefix) :])
            tag_parts[-1] = Path(tag_parts[-1]).stem
            tag_id = f"{namespace}:{'/'.join(tag_parts)}"
            key = (registry_id, tag_id)
            if key in definitions:
                raise ValueError(f"タグが重複しています: {registry_id}:{tag_id}")
            payload = _load_json(path)
            if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
                raise ValueError(f"タグJSONが不正です: {path}")
            members: list[dict[str, Any]] = []
            for raw_member in payload["values"]:
                required = True
                raw_id: object = raw_member
                if isinstance(raw_member, dict):
                    raw_id = raw_member.get("id")
                    required = raw_member.get("required", True)
                if not isinstance(raw_id, str) or not isinstance(required, bool):
                    raise ValueError(f"タグ参照が不正です: {path}: {raw_member!r}")
                reference_type = "tag" if raw_id.startswith("#") else "entry"
                clean_id = raw_id[1:] if reference_type == "tag" else raw_id
                member_id = _with_namespace(clean_id)
                _split_resource_id(member_id)
                members.append(
                    {
                        "reference_type": reference_type,
                        "id": member_id,
                        "required": required,
                    }
                )
            definitions[key] = {
                "replace": payload.get("replace", False),
                "members": members,
            }
            source_paths[key] = (
                f"generated/server/data/{namespace}/tags/{relative.as_posix()}"
            )

    cache: dict[tuple[str, str], set[str]] = {}

    def expand(key: tuple[str, str], stack: tuple[tuple[str, str], ...] = ()) -> set[str]:
        if key in cache:
            return cache[key]
        if key in stack:
            chain = " -> ".join(f"{row[0]}#{row[1]}" for row in (*stack, key))
            raise ValueError(f"タグ参照が循環しています: {chain}")
        definition = definitions.get(key)
        if definition is None:
            raise ValueError(f"参照先タグがありません: {key[0]}#{key[1]}")
        resolved: set[str] = set()
        for member in definition["members"]:
            if member["reference_type"] == "tag":
                target = (key[0], member["id"])
                if target not in definitions:
                    if member["required"]:
                        raise ValueError(
                            f"必須タグ参照が解決できません: {key[1]} -> {member['id']}"
                        )
                    continue
                resolved.update(expand(target, (*stack, key)))
            else:
                if member["id"] not in known_entries.get(key[0], set()):
                    if member["required"]:
                        raise ValueError(
                            f"必須タグ項目がレジストリにありません: "
                            f"{key[0]}#{key[1]} -> {member['id']}"
                        )
                    continue
                resolved.add(member["id"])
        cache[key] = resolved
        return resolved

    server_sha1 = lock["official_artifacts"]["server_jar"]["sha1"]
    records: list[dict[str, Any]] = []
    for registry_id, tag_id in sorted(definitions):
        definition = definitions[(registry_id, tag_id)]
        expanded = sorted(expand((registry_id, tag_id)))
        record = _base_record(
            record_id=(
                f"minecraft.tag.{_id_component(registry_id)}."
                f"{_id_component(tag_id)}"
            ),
            record_type="tag_definition",
            classification_ja="タグ定義",
            title_ja=f"#{tag_id}",
            search_terms=(
                tag_id,
                f"#{tag_id}",
                tag_id.split(":", 1)[1],
                registry_id,
                "タグ定義",
                *expanded,
            ),
            minecraft_version=lock["minecraft_version"],
            snapshot_id=lock["snapshot_id"],
            sources=[
                _artifact_source(
                    "definition",
                    server_sha1,
                    source_paths[(registry_id, tag_id)],
                    "",
                )
            ],
        )
        record.update(
            {
                "registry_id": registry_id,
                "tag_id": tag_id,
                "pack_scope": "vanilla_base",
                "replace": definition["replace"],
                "members": definition["members"],
                "expanded_entry_ids": expanded,
            }
        )
        records.append(record)
    if len(records) != 625:
        raise ValueError(
            "1.21.11の通常タグ数が想定と異なります: "
            f"{len(records)} != 625"
        )
    return records


def _version_record(
    *,
    lock: dict[str, Any],
    version_json: dict[str, Any],
    server_version: dict[str, Any],
    locale_key_count: int,
) -> dict[str, Any]:
    locked_runtime = lock["expected_runtime"]
    actual_runtime = {
        "world_version": server_version.get("world_version"),
        "protocol_version": server_version.get("protocol_version"),
        "data_pack_version": (
            f"{server_version.get('pack_version', {}).get('data_major')}."
            f"{server_version.get('pack_version', {}).get('data_minor')}"
        ),
        "resource_pack_version": (
            f"{server_version.get('pack_version', {}).get('resource_major')}."
            f"{server_version.get('pack_version', {}).get('resource_minor')}"
        ),
        "stable": server_version.get("stable"),
    }
    if actual_runtime != locked_runtime:
        raise ValueError(
            f"server.jar内の版情報がsource_lockと一致しません: {actual_runtime}"
        )
    record = _base_record(
        record_id=f"minecraft.version.{lock['snapshot_id']}",
        record_type="distribution_version",
        classification_ja="配布版情報",
        title_ja=f"Minecraft Java Edition {lock['minecraft_version']}",
        search_terms=(
            lock["minecraft_version"],
            lock["snapshot_id"],
            "Minecraft Java Edition",
            "配布版情報",
            f"Data Pack {actual_runtime['data_pack_version']}",
            f"Resource Pack {actual_runtime['resource_pack_version']}",
        ),
        minecraft_version=lock["minecraft_version"],
        snapshot_id=lock["snapshot_id"],
        sources=[
            _artifact_source(
                "distribution_metadata",
                lock["official_artifacts"]["version_json"]["sha1"],
                "official/version_json",
                "",
            ),
            _artifact_source(
                "runtime_versions",
                lock["official_artifacts"]["server_jar"]["sha1"],
                "server.jar!/version.json",
                "",
            ),
            _artifact_source(
                "locale_key_count",
                lock["official_artifacts"]["ja_jp"]["sha1"],
                lock["official_artifacts"]["ja_jp"]["asset_key"],
                "",
            ),
            _web_source(
                "release_notes",
                lock["official_documents"]["release_notes"],
                "Minecraft Java 1.21.11 Released",
                lock["checked_at"],
            ),
        ],
    )
    record.update(
        {
            "distribution_type": version_json.get("type"),
            "metadata_time": version_json.get("time"),
            "release_time": version_json.get("releaseTime"),
            "runtime_versions": actual_runtime,
            "java_major_version": server_version.get("java_version"),
            "locale": "ja_jp",
            "locale_key_count": locale_key_count,
            "official_artifacts": lock["official_artifacts"],
            "release_notes": lock["official_documents"]["release_notes"],
        }
    )
    return record


def _official_change_records(lock: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _load_json(OFFICIAL_CHANGES_PATH)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("minecraft_version") != lock["minecraft_version"]
        or payload.get("classification_ja") != "公式変更事項"
        or payload.get("coverage") != "representative_selection"
        or not isinstance(payload.get("coverage_note_ja"), str)
        or not payload["coverage_note_ja"].strip()
    ):
        raise ValueError("official_changes.jsonの版または分類が不正です")
    source = payload.get("source")
    if (
        not isinstance(source, dict)
        or source.get("url") != lock["official_documents"]["release_notes"]
        or source.get("checked_at") != lock["checked_at"]
    ):
        raise ValueError("official_changes.jsonの出典がsource_lockと一致しません")
    rows = payload.get("records")
    if not isinstance(rows, list) or len(rows) != 25:
        raise ValueError("official_changes.jsonの変更事項は25件である必要があります")
    allowed_areas = {
        "データパック",
        "リソースパック",
        "サーバー管理プロトコル",
        "性能計測",
        "ネットワーク通信",
        "コマンド",
        "ゲームルール",
        "ルートテーブル",
        "データコンポーネント",
    }
    allowed_types = {"追加", "変更", "廃止", "版番号更新"}
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("official_changes.jsonの変更事項がオブジェクトではありません")
        key = row.get("key")
        related = row.get("related_identifiers")
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", key)
            or key in seen_keys
            or row.get("technical_area_ja") not in allowed_areas
            or row.get("change_type_ja") not in allowed_types
            or not isinstance(row.get("title_ja"), str)
            or not row["title_ja"].strip()
            or not isinstance(row.get("summary_ja"), str)
            or not row["summary_ja"].strip()
            or not isinstance(row.get("source_section"), str)
            or not row["source_section"].strip()
            or not isinstance(related, list)
            or any(not isinstance(value, str) or not value.strip() for value in related)
            or len(related) != len(set(related))
        ):
            raise ValueError(f"official_changes.jsonの変更事項が不正です: {key!r}")
        seen_keys.add(key)
        mappings = row.get("identifier_mappings", [])
        mapping_scope = row.get("identifier_mapping_scope")
        if not isinstance(mappings, list) or any(
            not isinstance(mapping, dict)
            or set(mapping) != {"from", "to", "value_inverted"}
            or not isinstance(mapping.get("from"), str)
            or not isinstance(mapping.get("to"), str)
            or not isinstance(mapping.get("value_inverted"), bool)
            for mapping in mappings
        ):
            raise ValueError(f"official_changes.jsonの識別子対応が不正です: {key}")
        if (mappings and mapping_scope != "representative_selection") or (
            not mappings and "identifier_mapping_scope" in row
        ):
            raise ValueError(f"official_changes.jsonの識別子対応範囲が不正です: {key}")
        search_terms = [
            row["title_ja"],
            row["summary_ja"],
            row["technical_area_ja"],
            row["change_type_ja"],
            "公式変更事項",
            *related,
            row.get("migration_note_ja"),
            *(value for mapping in mappings for value in (mapping["from"], mapping["to"])),
        ]
        record = _base_record(
            record_id=f"minecraft.change.{lock['minecraft_version']}.{key}",
            record_type="official_change",
            classification_ja="公式変更事項",
            title_ja=row["title_ja"],
            search_terms=search_terms,
            minecraft_version=lock["minecraft_version"],
            snapshot_id=lock["snapshot_id"],
            sources=[
                _web_source(
                    "change_statement",
                    source["url"],
                    row["source_section"],
                    source["checked_at"],
                )
            ],
        )
        record.update(
            {
                "technical_area_ja": row["technical_area_ja"],
                "change_type_ja": row["change_type_ja"],
                "summary_ja": row["summary_ja"],
                "related_identifiers": related,
                "summary_language": "ja",
                "summary_method": "editorial_paraphrase",
                "experimental": row.get("experimental", False),
                "coverage": payload["coverage"],
                "coverage_note_ja": payload["coverage_note_ja"],
            }
        )
        if mappings:
            record["identifier_mappings"] = mappings
            record["identifier_mapping_scope"] = mapping_scope
        if row.get("migration_note_ja"):
            record["migration_note_ja"] = row["migration_note_ja"]
        records.append(record)
    return records


def _serialize_jsonl(
    records: list[dict[str, Any]],
) -> tuple[bytes, list[tuple[int, int]]]:
    chunks: list[bytes] = []
    locators: list[tuple[int, int]] = []
    offset = 0
    for record in records:
        body = (
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        chunks.append(body)
        locators.append((offset, len(body)))
        offset += len(body)
    return b"".join(chunks), locators


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _safe_leaf(path: Path, root: Path) -> Path:
    lexical_root = root.absolute()
    lexical_path = path.absolute()
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError as error:
        raise ValueError(f"出力先がローカルキャッシュ外です: {path}") from error
    cursor = lexical_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"出力先にシンボリックリンクがあります: {path}")
    if not lexical_path.resolve().is_relative_to(lexical_root.resolve()):
        raise ValueError(f"出力先がローカルキャッシュ外です: {path}")
    return lexical_path


def _validate_cache_root(cache_root: Path) -> Path:
    """全量派生DBの出力を、Git除外済みの専用領域へ限定する。"""

    lexical_base = LOCAL_CACHE_BASE.absolute()
    lexical_cache = cache_root.absolute()
    try:
        relative = lexical_cache.relative_to(lexical_base)
    except ValueError as error:
        raise ValueError(
            f"出力先は{lexical_base}配下に限ります: {cache_root}"
        ) from error
    if not relative.parts:
        raise ValueError(".dogido_reference直下をスナップショット領域にできません")
    cursor = lexical_base
    if cursor.is_symlink():
        raise ValueError(f"出力先にシンボリックリンクがあります: {cursor}")
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"出力先にシンボリックリンクがあります: {cursor}")
    if not lexical_cache.resolve().is_relative_to(lexical_base.resolve()):
        raise ValueError(f"出力先がローカル専用領域外です: {cache_root}")
    return lexical_cache


def _write_local_snapshot(
    *,
    cache_root: Path,
    lock: dict[str, Any],
    dataset_records: dict[str, list[dict[str, Any]]],
    source_lock_sha256: str,
    generator_summary: dict[str, Any],
) -> Path:
    cache_root = _validate_cache_root(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    snapshot_id = lock["snapshot_id"]
    target = _safe_leaf(cache_root / snapshot_id, cache_root)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=cache_root)
    )
    try:
        dataset_rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for dataset_id, filename in DATASETS:
            records = sorted(dataset_records[dataset_id], key=lambda row: row["id"])
            data_body, locators = _serialize_jsonl(records)
            index_records: list[dict[str, Any]] = []
            for record, (offset, length) in zip(records, locators, strict=True):
                if record["id"] in seen_ids:
                    raise ValueError(f"正規化IDが重複しています: {record['id']}")
                seen_ids.add(record["id"])
                index_records.append(
                    {
                        "id": record["id"],
                        "dataset_id": dataset_id,
                        "dataset_path": filename,
                        "byte_offset": offset,
                        "byte_length": length,
                        "record_type": record["record_type"],
                        "registry_id": record.get("registry_id"),
                        "title_ja": record["title_ja"],
                        "search_terms": record["search_terms"],
                    }
                )
            index_body, _ = _serialize_jsonl(index_records)
            index_filename = filename.removesuffix(".jsonl") + ".index.jsonl"
            (staging / filename).write_bytes(data_body)
            (staging / index_filename).write_bytes(index_body)
            dataset_rows.append(
                {
                    "id": dataset_id,
                    "path": filename,
                    "index_path": index_filename,
                    "record_count": len(records),
                    "sha256": _sha256_bytes(data_body),
                    "index_sha256": _sha256_bytes(index_body),
                }
            )
        manifest = {
            "schema_version": 1,
            "snapshot_id": snapshot_id,
            "minecraft_version": lock["minecraft_version"],
            "source_lock_sha256": source_lock_sha256,
            "generator": "scripts/build_minecraft_technical_data.py",
            "normalization_revision": lock["normalization_revision"],
            "normalization_inputs": lock["normalization_inputs"],
            "runtime_network_access": False,
            "storage_scope": "local_cache_only_gitignored",
            "disclaimer": lock["storage_policy"]["disclaimer"],
            "source_manifest_state": lock["manifest_state"],
            "datasets": dataset_rows,
            "generator_summary": generator_summary,
        }
        manifest_body = _json_bytes(manifest)
        (staging / "manifest.json").write_bytes(manifest_body)

        if target.exists():
            expected = {
                path.relative_to(staging): path.read_bytes()
                for path in staging.iterdir()
                if path.is_file()
            }
            actual = {
                path.relative_to(target): path.read_bytes()
                for path in target.iterdir()
                if path.is_file()
            }
            if actual != expected:
                raise ValueError(
                    "同じsnapshot_idのローカルDBが異なる内容で存在します。"
                    "source_lockを更新して新しいsnapshot_idを作成してください。"
                )
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)

        current_body = _json_bytes(
            {
                "schema_version": 1,
                "snapshot_id": snapshot_id,
                "manifest_sha256": _sha256_bytes(manifest_body),
            }
        )
        current = _safe_leaf(cache_root / "current.json", cache_root)
        temporary = _safe_leaf(cache_root / ".current.json.tmp", cache_root)
        temporary.write_bytes(current_body)
        os.replace(temporary, current)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def build(
    *,
    version_manifest_path: Path,
    version_json_path: Path,
    server_jar_path: Path,
    asset_index_path: Path,
    locale_path: Path,
    cache_root: Path,
    java: str,
) -> Path:
    lock = _load_json(LOCK_PATH)
    schema = _load_json(SCHEMA_PATH)
    if lock.get("schema_version") != 1:
        raise ValueError("未対応のsource_lockです")
    if lock.get("normalization_revision") != 3:
        raise ValueError("未対応のMinecraft DB正規化版です")
    _validate_official_urls(lock)
    normalization_inputs = lock.get("normalization_inputs", {})
    for path, path_key, hash_key, expected_relative in (
        (
            SCHEMA_PATH,
            "record_schema_path",
            "record_schema_sha256",
            "reference/minecraft_technical/schema.json",
        ),
        (
            OFFICIAL_CHANGES_PATH,
            "official_changes_path",
            "official_changes_sha256",
            "reference/minecraft_technical/official_changes.json",
        ),
        (
            Path(__file__).resolve(),
            "generator_path",
            "generator_sha256",
            "scripts/build_minecraft_technical_data.py",
        ),
    ):
        if (
            normalization_inputs.get(path_key) != expected_relative
            or _hash_file(path, "sha256") != normalization_inputs.get(hash_key)
        ):
            raise ValueError(
                f"正規化入力のハッシュがsource_lockと一致しません: {path.name}. "
                "正規化仕様を変えた場合はnormalization_revisionとsnapshot_idを更新してください。"
            )
    if _adapter_minecraft_version() != lock["minecraft_version"]:
        raise ValueError(
            "FabricアダプターのMinecraft版とsource_lockが一致しません"
        )
    compatibility = lock["adapter_compatibility"]
    for property_name, lock_name in (
        ("yarn_mappings", "yarn_mappings"),
        ("loader_version", "fabric_loader"),
        ("fabric_api_version", "fabric_api"),
    ):
        if _adapter_property(property_name) != compatibility[lock_name]:
            raise ValueError(
                f"Fabricアダプターの{property_name}とsource_lockが一致しません"
            )
    artifacts = lock["official_artifacts"]
    _verify_artifact(version_json_path, artifacts["version_json"], "version JSON")
    _verify_artifact(server_jar_path, artifacts["server_jar"], "server.jar")
    _verify_artifact(asset_index_path, artifacts["asset_index"], "asset index")
    _verify_artifact(locale_path, artifacts["ja_jp"], "ja_jp.json")

    version_manifest = _load_json(version_manifest_path)
    version_json = _load_json(version_json_path)
    asset_index = _load_json(asset_index_path)
    locale = _load_json(locale_path)
    _validate_source_chain(
        lock=lock,
        version_manifest=version_manifest,
        version_json=version_json,
        asset_index=asset_index,
        locale=locale,
    )

    with tempfile.TemporaryDirectory(prefix="dogido-minecraft-generator-") as directory:
        working = Path(directory)
        bundler = working / "bundler"
        reports_output = working / "reports-output"
        server_output = working / "server-output"
        _run_data_generator(
            java=java,
            server_jar=server_jar_path,
            bundler_dir=bundler,
            output_dir=reports_output,
            mode="--reports",
            working_dir=working,
        )
        _run_data_generator(
            java=java,
            server_jar=server_jar_path,
            bundler_dir=bundler,
            output_dir=server_output,
            mode="--server",
            working_dir=working,
        )
        actual_reports = {
            path.relative_to(reports_output).as_posix()
            for path in reports_output.rglob("*")
            if path.is_file() and ".cache" not in path.parts
        }
        if actual_reports != EXPECTED_REPORTS:
            raise ValueError(
                "公式reportsのファイル集合が想定と異なります: "
                f"missing={sorted(EXPECTED_REPORTS - actual_reports)}, "
                f"extra={sorted(actual_reports - EXPECTED_REPORTS)}"
            )
        reports_dir = reports_output / "reports"
        registries = _load_json(reports_dir / "registries.json")
        blocks = _load_json(reports_dir / "blocks.json")
        items = _load_json(reports_dir / "items.json")
        datapack = _load_json(reports_dir / "datapack.json")
        server_version = _server_version_info(bundler, lock["minecraft_version"])
        if (
            server_version.get("id") != lock["minecraft_version"]
            or server_version.get("java_version")
            != lock["adapter_compatibility"]["java_major_version"]
        ):
            raise ValueError("server.jar内の版またはJava版がsource_lockと一致しません")

        registry_records = _registry_records(
            registries=registries,
            blocks=blocks,
            items=items,
            locale=locale,
            lock=lock,
        )
        datapack_records = _datapack_records(
            server_output=server_output,
            datapack=datapack,
            locale=locale,
            lock=lock,
        )
        known_entries: dict[str, set[str]] = {}
        for record in (*registry_records, *datapack_records):
            known_entries.setdefault(record["registry_id"], set()).add(
                record["entry_id"]
            )
        tag_records = _tag_records(
            server_output=server_output,
            datapack=datapack,
            known_entries=known_entries,
            lock=lock,
        )
        official_change_records = _official_change_records(lock)
        version_record = _version_record(
            lock=lock,
            version_json=version_json,
            server_version=server_version,
            locale_key_count=len(locale),
        )

        validator = Draft202012Validator(schema)
        all_records = [
            version_record,
            *official_change_records,
            *registry_records,
            *datapack_records,
            *tag_records,
        ]
        for record in all_records:
            errors = sorted(
                validator.iter_errors(record),
                key=lambda error: list(error.path),
            )
            if errors:
                raise ValueError(
                    f"Minecraft正規化レコードがスキーマに違反しています: "
                    f"{record['id']}: {errors[0].message}"
                )
        if len(locale) != 8_524:
            raise ValueError(
                f"1.21.11の公式日本語キー数が想定と異なります: {len(locale)}"
            )
        direct_tag_references = sum(
            len(record["members"]) for record in tag_records
        )
        generator_summary = {
            "report_file_count": len(actual_reports),
            "registry_count": len(registries),
            "official_change_count": len(official_change_records),
            "registry_entry_count": len(registry_records),
            "datapack_entry_count": len(datapack_records),
            "tag_count": len(tag_records),
            "direct_tag_reference_count": direct_tag_references,
            "locale_key_count": len(locale),
            "raw_generator_outputs_stored": False,
            "generator_cache_included": False,
        }
        return _write_local_snapshot(
            cache_root=cache_root,
            lock=lock,
            dataset_records={
                "version_information": [version_record],
                "official_changes": official_change_records,
                "registry_entries": registry_records,
                "datapack_entries": datapack_records,
                "tag_definitions": tag_records,
            },
            source_lock_sha256=_hash_file(LOCK_PATH, "sha256"),
            generator_summary=generator_summary,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Minecraft Java公式技術データのローカル専用DBを生成"
    )
    parser.add_argument("--version-manifest", type=Path, required=True)
    parser.add_argument("--version-json", type=Path, required=True)
    parser.add_argument("--server-jar", type=Path, required=True)
    parser.add_argument("--asset-index", type=Path, required=True)
    parser.add_argument("--locale-json", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--java", default=shutil.which("java") or "java")
    args = parser.parse_args(argv)
    target = build(
        version_manifest_path=args.version_manifest.resolve(),
        version_json_path=args.version_json.resolve(),
        server_jar_path=args.server_jar.resolve(),
        asset_index_path=args.asset_index.resolve(),
        locale_path=args.locale_json.resolve(),
        cache_root=args.cache_root.resolve(),
        java=args.java,
    )
    print(f"wrote local Minecraft knowledge database: {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
