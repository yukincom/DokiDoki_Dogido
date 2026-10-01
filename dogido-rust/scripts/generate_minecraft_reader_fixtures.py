#!/usr/bin/env python3
"""Synthetic Minecraft reader fixtures from canonical Python; no network or real DB."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

DATASETS = ("version_information", "official_changes", "registry_entries", "datapack_entries", "tag_definitions")
SNAPSHOT = "mcjava-1.21.11-synthetic-n1"
PREFIX = "cache/" + SNAPSHOT + "/"


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def seal_current(files):
    current = json.loads(files["cache/current.json"])
    current["manifest_sha256"] = digest(files[PREFIX + "manifest.json"])
    files["cache/current.json"] = encode(current)


def seal(files):
    manifest = json.loads(files[PREFIX + "manifest.json"])
    manifest["source_lock_sha256"] = digest(files["source_lock.json"])
    for row in manifest["datasets"]:
        for key, sha in (("path", "sha256"), ("index_path", "index_sha256")):
            if PREFIX + row[key] in files:
                row[sha] = digest(files[PREFIX + row[key]])
    files[PREFIX + "manifest.json"] = encode(manifest)
    seal_current(files)


def base_files():
    records = {
        "version_information": [("version.synthetic", "配布版情報", ["java", "1.21.11"], "version_information", "")],
        "official_changes": [("change.synthetic", "スポーンの変更", ["doMobSpawning", "spawn mobs"], "official_change", "minecraft:game_rule")],
        "registry_entries": [
            ("entry.a", "ダイヤモンドの剣", ["minecraft:diamond_sword", "剣", "青い 武器"], "registry_entry", "minecraft:item"),
            ("entry.b", "剣", ["特別", "diamond", "sword"], "registry_entry", "minecraft:item"),
            ("entry.c", "剣士", ["剣", "ひと"], "registry_entry", "minecraft:entity_type"),
            ("entry.d", "仮名", ["ｶﾀｶﾅ", "Straße", "〜例", "A\u001cB", "\U0001ccd6"], "registry_entry", "minecraft:item"),
            ("entry.e", "別の資料", ["blue", "long", "sword"], "registry_entry", "minecraft:item"),
        ],
        "datapack_entries": [("recipe.synthetic", "剣のレシピ", ["minecraft:diamond_sword", "剣"], "datapack_entry", "minecraft:recipe")],
        "tag_definitions": [("tag.synthetic", "#minecraft:swords", ["剣", "swords"], "tag_definition", "minecraft:item")],
    }
    files = {"source_lock.json": encode({"schema_version": 1, "snapshot_id": SNAPSHOT, "minecraft_version": "1.21.11"})}
    rows = []
    for dataset in DATASETS:
        data, entries = "", []
        for ident, title, terms, kind, registry in records[dataset]:
            record = {"id": ident, "title_ja": title, "search_terms": terms, "record_type": kind, "registry_id": registry, "synthetic": True}
            if ident == "entry.d":
                record["dataset_id"] = "record-owned"
            line = encode(record) + "\n"
            entries.append({**record, "dataset_id": dataset, "dataset_path": dataset + ".jsonl", "byte_offset": len(data.encode()), "byte_length": len(line.encode())})
            data += line
        files[PREFIX + dataset + ".jsonl"] = data
        files[PREFIX + dataset + ".index.jsonl"] = "".join(encode(e) + "\n" for e in entries)
        rows.append({"id": dataset, "path": dataset + ".jsonl", "index_path": dataset + ".index.jsonl", "record_count": len(entries)})
    files[PREFIX + "manifest.json"] = encode({"schema_version": 1, "snapshot_id": SNAPSHOT, "minecraft_version": "1.21.11", "storage_scope": "local_cache_only_gitignored", "datasets": rows})
    files["cache/current.json"] = encode({"schema_version": 1, "snapshot_id": SNAPSHOT})
    seal(files)
    return files


def build_fixture(root, files=None, links=None):
    """Write synthetic DB, return (cache_root, source_lock_path); importable by provider fixtures."""
    root = Path(root)
    for name, text in (base_files() if files is None else files).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for name, target in (links or {}).items():
        (root / name).symlink_to(root / target)
    return root / "cache", root / "source_lock.json"


def mutate_json(files, path, mutate):
    value = json.loads(files[path])
    mutate(value)
    files[path] = encode(value)


def mutate_entry(files, mutate):
    path = PREFIX + "version_information.index.jsonl"
    value = json.loads(files[path])
    mutate(value)
    files[path] = encode(value) + "\n"
    seal(files)


def cases():
    base = base_files()
    results = []
    def add(name, query="", *, files=None, links=None, datasets=(), types=(), registries=(), limit=20):
        results.append({"name": name, "files": deepcopy(base if files is None else files), "links": links or {},
                        "query": query, "datasets": list(datasets), "types": list(types), "registries": list(registries), "limit": limit})
    queries = ["", "剣", "entry.a", "minecraft:diamond_sword", "ダイヤモンドの剣", "ダイヤモンド の 剣", "blue sword", "unknown", "ＳＴＲＡＳＳＥ", "カタカナ", "〜例", "～例", "a b", "\U0001ccd6", "  剣\u001c "]
    for query in queries:
        for limit in (1, 3, 20):
            add(f"query-{query!r}-{limit}", query, limit=limit)
    for datasets in (DATASETS, (" REGISTRY_ENTRIES ", "registry_entries", "unknown"), ("unknown",), ("tag_definitions", "registry_entries")):
        add("datasets-" + repr(datasets), "剣", datasets=datasets)
    add("whitespace-dataset", datasets=[" "])
    add("whitespace-type", types=[" "])
    add("whitespace-registry", registries=[" "])
    add("types", "剣", types=[" TAG_DEFINITION ", "registry_entry"])
    add("registry", "剣", registries=[" MINECRAFT:ITEM "])
    add("combined", "剣", datasets=["registry_entries"], types=["registry_entry"], registries=["minecraft:item"])
    add("zero-limit", limit=0)
    for part in ("cache/current.json", PREFIX + "manifest.json", "source_lock.json", PREFIX + "version_information.jsonl", PREFIX + "version_information.index.jsonl"):
        files = deepcopy(base)
        del files[part]
        add("missing-" + part, files=files)
        files = deepcopy(base)
        files[part] = files[part].replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1) if "schema_version" in files[part] else files[part].replace('"id":', '"id":"duplicate","id":', 1)
        if part == PREFIX + "manifest.json":
            seal_current(files)
        elif part != "cache/current.json":
            seal(files)
        add("duplicate-key-" + part, files=files)
        files = deepcopy(base)
        files["outside.json"] = files.pop(part)
        add("symlink-" + part, files=files, links={part: "outside.json"})
    for field, value in (("byte_offset", -1), ("byte_offset", 0.5), ("byte_offset", 2**64), ("byte_length", 1), ("byte_length", 999999), ("byte_length", None), ("id", "wrong"), ("dataset_id", "wrong"), ("dataset_path", "../outside.jsonl"), ("search_terms", None)):
        files = deepcopy(base)
        mutate_entry(files, lambda e: e.update({field: value}))
        add("bad-entry-" + field + repr(value), files=files)
    files = deepcopy(base)
    mutate_entry(files, lambda e: e.update(byte_offset=False))
    add("bool-offset-python-int", files=files)
    for change, value in (("record_count", 0), ("record_count", -1), ("record_count", 1.0), ("record_count", True), ("path", "../outside.jsonl"), ("path", "/tmp/outside.jsonl"), ("path", "a/b.jsonl"), ("path", "a\\b.jsonl"), ("path", "version_information.index.jsonl")):
        files = deepcopy(base)
        mutate_json(files, PREFIX + "manifest.json", lambda m: m["datasets"][0].update({change: value}))
        seal_current(files)
        add("manifest-" + change + repr(value), files=files)
    for field, value in (("snapshot_id", "../escape"), ("snapshot_id", "mcjava-UPPER"), ("schema_version", 2), ("manifest_sha256", "0"*64)):
        files = deepcopy(base)
        mutate_json(files, "cache/current.json", lambda c: c.update({field:value}))
        add("current-" + field, files=files)
    for field, value in (("schema_version", 2), ("minecraft_version", "other"), ("snapshot_id", "mcjava-other")):
        files = deepcopy(base)
        mutate_json(files, "source_lock.json", lambda c: c.update({field:value}))
        seal(files)
        add("lock-" + field, files=files)
    files = deepcopy(base)
    mutate_json(files, PREFIX + "manifest.json", lambda m: m["datasets"].reverse())
    seal_current(files)
    add("dataset-order", files=files)
    files = deepcopy(base)
    mutate_json(files, PREFIX + "manifest.json", lambda m: m["datasets"].pop())
    seal_current(files)
    add("dataset-set", files=files)
    files = deepcopy(base)
    files[PREFIX + "version_information.index.jsonl"] *= 2
    mutate_json(files, PREFIX + "manifest.json", lambda m: m["datasets"][0].update(record_count=2))
    seal(files)
    add("duplicate-id", "query-with-no-hit", files=files)
    add("duplicate-id-filtered-out", files=files, types=["tag_definition"])
    files = deepcopy(base)
    files[PREFIX + "version_information.jsonl"] = files[PREFIX + "version_information.jsonl"].replace("配布版情報", "別の版情報")
    add("data-sha", files=files)
    files = deepcopy(base)
    files[PREFIX + "version_information.index.jsonl"] += "\n"
    add("index-sha", files=files)
    files = deepcopy(base)
    files[PREFIX + "version_information.jsonl"] = '{"id":"version.synthetic","nested":{"x":1,"x":2}}\n'
    mutate_entry(files, lambda e: e.update(byte_length=len(files[PREFIX + "version_information.jsonl"].encode())))
    add("nested-duplicate-record", files=files)
    for raw in ('{"schema_version":NaN}', '{"schema_version":Infinity}', '{"schema_version":1} extra'):
        files = deepcopy(base)
        files["cache/current.json"] = raw
        add("invalid-json-" + raw, files=files)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo))
    from dogido_server import minecraft_knowledge as source
    rows = cases()
    for row in rows:
        with tempfile.TemporaryDirectory() as temporary:
            cache, lock = build_fixture(temporary, row["files"], row["links"])
            with patch.object(source, "SOURCE_LOCK_PATH", lock):
                try:
                    # Public Rust API opens first even for a zero-limit search.
                    source._load_context(cache)
                    row["expected"] = source.search_minecraft_knowledge(row["query"], dataset_ids=row["datasets"], record_types=row["types"], registry_ids=row["registries"], limit=row["limit"], cache_root=cache)
                    row["error"] = ""
                except (OSError, ValueError, KeyError, TypeError) as error:
                    row["expected"] = None
                    row["error"] = type(error).__name__
    target = args.output or args.repo/"dogido-rust/fixtures/minecraft-reader.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(rows)} synthetic Minecraft reader cases; no real database or network used")


if __name__ == "__main__":
    main()
