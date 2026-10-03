"""Minecraft Java 公式技術データベースのローカル検索API。

版を固定した公式配布物から生成済みの ``.dogido_reference`` だけを読み、
実行時のダウンロード、ゲーム操作、LLMへの自動注入は行わない。
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = ROOT / "reference" / "minecraft_technical"
SOURCE_LOCK_PATH = REFERENCE_DIR / "source_lock.json"
DEFAULT_CACHE_ROOT = ROOT / ".dogido_reference" / "minecraft_technical"
EXPECTED_DATASET_IDS = (
    "version_information",
    "official_changes",
    "registry_entries",
    "datapack_entries",
    "tag_definitions",
)
SNAPSHOT_ID_RE = re.compile(r"^mcjava-[a-z0-9.-]+$")


class _WorstFirst:
    """heapの根に、保持中で最も低順位の検索キーを置く。"""

    __slots__ = ("key",)

    def __init__(self, key: tuple[int, str]) -> None:
        self.key = key

    def __lt__(self, other: _WorstFirst) -> bool:
        return self.key > other.key


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
    return _loads_json(path.read_bytes(), label=str(path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_handle(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _normalize(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _compact(value: object) -> str:
    return "".join(_normalize(value).split())


def _safe_cache_root(cache_root: Path) -> Path:
    lexical = cache_root.absolute()
    if lexical.is_symlink():
        raise ValueError(f"ローカルDBのルートがシンボリックリンクです: {cache_root}")
    resolved = lexical.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(
            f"Minecraft技術データベースがありません: {resolved}. "
            "scripts/build_minecraft_technical_data.py を実行してください。"
        )
    return resolved


def _safe_child_directory(root: Path, name: object) -> Path:
    raw = str(name)
    relative = PurePosixPath(raw)
    if (
        not SNAPSHOT_ID_RE.fullmatch(raw)
        or "\\" in raw
        or relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) != 1
    ):
        raise ValueError(f"不正なMinecraftスナップショットIDです: {name!r}")
    candidate = root / raw
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError(f"通常ディレクトリではありません: {candidate}")
    if candidate.resolve().parent != root.resolve():
        raise ValueError(f"ローカルDB外を指すスナップショットです: {candidate}")
    return candidate.resolve()


def _safe_artifact_path(
    snapshot_dir: Path,
    value: object,
    *,
    suffix: str,
) -> Path:
    raw = str(value)
    relative = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) != 1
        or not relative.name.endswith(suffix)
        or (suffix == ".jsonl" and relative.name.endswith(".index.jsonl"))
    ):
        raise ValueError(f"不正なMinecraft DBファイル名です: {value!r}")
    candidate = snapshot_dir / relative.name
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"通常ファイルではありません: {candidate}")
    if candidate.resolve().parent != snapshot_dir.resolve():
        raise ValueError(f"ローカルDB外を指すファイルです: {candidate}")
    return candidate.resolve()


def _load_context(cache_root: Path | None = None) -> dict[str, Any]:
    root = _safe_cache_root(cache_root or DEFAULT_CACHE_ROOT)
    current_path = root / "current.json"
    current = _load_json(current_path)
    if not isinstance(current, dict) or current.get("schema_version") != 1:
        raise ValueError("未対応のMinecraft DB current.jsonです")
    snapshot_dir = _safe_child_directory(root, current.get("snapshot_id"))
    manifest_path = snapshot_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    if _sha256(manifest_path) != current.get("manifest_sha256"):
        raise ValueError("Minecraft DBのmanifestハッシュがcurrent.jsonと一致しません")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("snapshot_id") != current.get("snapshot_id")
        or manifest.get("storage_scope") != "local_cache_only_gitignored"
    ):
        raise ValueError("Minecraft DBのmanifestが不正です")

    source_lock = _load_json(SOURCE_LOCK_PATH)
    if (
        not isinstance(source_lock, dict)
        or source_lock.get("schema_version") != 1
        or source_lock.get("snapshot_id") != manifest.get("snapshot_id")
        or source_lock.get("minecraft_version") != manifest.get("minecraft_version")
        or _sha256(SOURCE_LOCK_PATH) != manifest.get("source_lock_sha256")
    ):
        raise ValueError("Minecraft DBとsource_lock.jsonが一致しません")

    raw_datasets = manifest.get("datasets")
    if not isinstance(raw_datasets, list):
        raise ValueError("Minecraft DBのデータセット一覧が不正です")
    datasets: dict[str, dict[str, Any]] = {}
    for row in raw_datasets:
        if not isinstance(row, dict):
            raise ValueError("Minecraft DBのデータセット行が不正です")
        dataset_id = str(row.get("id", ""))
        if dataset_id in datasets or dataset_id not in EXPECTED_DATASET_IDS:
            raise ValueError(f"Minecraft DBのデータセットIDが不正です: {dataset_id!r}")
        if not isinstance(row.get("record_count"), int) or row["record_count"] < 0:
            raise ValueError(f"Minecraft DBの件数が不正です: {dataset_id}")
        data_path = _safe_artifact_path(snapshot_dir, row.get("path"), suffix=".jsonl")
        index_path = _safe_artifact_path(
            snapshot_dir,
            row.get("index_path"),
            suffix=".index.jsonl",
        )
        if _sha256(data_path) != row.get("sha256"):
            raise ValueError(f"Minecraft DB本体のハッシュが不一致です: {dataset_id}")
        if _sha256(index_path) != row.get("index_sha256"):
            raise ValueError(f"Minecraft DB索引のハッシュが不一致です: {dataset_id}")
        datasets[dataset_id] = {
            **row,
            "_data_path": data_path,
            "_index_path": index_path,
        }
    if tuple(row.get("id") for row in raw_datasets) != EXPECTED_DATASET_IDS:
        raise ValueError("Minecraft DBのデータセット順または集合が不正です")
    return {
        "root": root,
        "snapshot_dir": snapshot_dir,
        "current": current,
        "manifest": manifest,
        "source_lock": source_lock,
        "datasets": datasets,
    }


def load_minecraft_manifest(cache_root: Path | None = None) -> dict[str, Any]:
    """ハッシュ検証済みのローカルDB manifestを返す。"""

    return dict(_load_context(cache_root)["manifest"])


def _selected_datasets(
    context: dict[str, Any], dataset_ids: Iterable[str]
) -> list[dict[str, Any]]:
    requested = tuple(
        dict.fromkeys(_normalize(value) for value in dataset_ids if _normalize(value))
    )
    datasets = context["datasets"]
    return [datasets[value] for value in (requested or EXPECTED_DATASET_IDS) if value in datasets]


def _iter_index_entries(
    context: dict[str, Any],
    *,
    dataset_ids: Iterable[str] = (),
) -> Iterator[dict[str, Any]]:
    for dataset in _selected_datasets(context, dataset_ids):
        path: Path = dataset["_index_path"]
        with path.open("rb") as handle:
            if _sha256_handle(handle) != dataset.get("index_sha256"):
                raise ValueError(f"Minecraft DB索引のハッシュが不一致です: {dataset['id']}")
            handle.seek(0)
            count = 0
            for line_number, line in enumerate(handle, 1):
                entry = _loads_json(line, label=f"{path.name}:{line_number}")
                if not isinstance(entry, dict):
                    raise ValueError(f"Minecraft DB索引行がオブジェクトではありません: {path}:{line_number}")
                if (
                    entry.get("dataset_id") != dataset["id"]
                    or entry.get("dataset_path") != dataset["path"]
                    or not isinstance(entry.get("id"), str)
                    or not isinstance(entry.get("record_type"), str)
                    or not isinstance(entry.get("title_ja"), str)
                    or not isinstance(entry.get("search_terms"), list)
                ):
                    raise ValueError(f"Minecraft DB索引行が不正です: {path}:{line_number}")
                count += 1
                yield {**entry, "_dataset_sha256": dataset["sha256"]}
            if count != dataset["record_count"]:
                raise ValueError(f"Minecraft DB索引件数が不一致です: {dataset['id']}")


def _load_entries(
    entries: Iterable[dict[str, Any]],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    ordered = list(entries)
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for position, entry in enumerate(ordered):
        grouped.setdefault(str(entry["dataset_id"]), []).append((position, entry))
    loaded: list[dict[str, Any] | None] = [None] * len(ordered)
    for dataset_id, group in grouped.items():
        dataset = context["datasets"].get(dataset_id)
        if dataset is None:
            raise ValueError(f"未知のMinecraft DBデータセットです: {dataset_id}")
        path: Path = dataset["_data_path"]
        with path.open("rb") as handle:
            if _sha256_handle(handle) != dataset.get("sha256"):
                raise ValueError(f"Minecraft DB本体のハッシュが不一致です: {dataset_id}")
            size = handle.tell()
            for position, entry in group:
                offset = entry.get("byte_offset")
                length = entry.get("byte_length")
                if (
                    not isinstance(offset, int)
                    or not isinstance(length, int)
                    or offset < 0
                    or length < 2
                    or offset + length > size
                ):
                    raise ValueError(f"Minecraft DBのバイト位置が不正です: {entry.get('id')}")
                handle.seek(offset)
                record = _loads_json(
                    handle.read(length),
                    label=f"{path.name}@{offset}",
                )
                if not isinstance(record, dict) or record.get("id") != entry.get("id"):
                    raise ValueError(f"Minecraft DB索引と本体が一致しません: {entry.get('id')}")
                loaded[position] = record
    if any(record is None for record in loaded):
        raise ValueError("Minecraft DBレコードの読込みが完了しませんでした")
    return [record for record in loaded if record is not None]


def _entry_score(entry: dict[str, Any], normalized_query: str) -> int | None:
    if not normalized_query:
        return 4
    normalized_id = _normalize(entry.get("id", ""))
    title = _normalize(entry.get("title_ja", ""))
    terms = [_normalize(value) for value in entry.get("search_terms", ()) if value]
    if normalized_query in {normalized_id, title}:
        return 0
    if normalized_query in terms:
        return 1
    compact_query = _compact(normalized_query)
    compact_terms = [_compact(value) for value in (normalized_id, title, *terms)]
    if compact_query and any(compact_query in term for term in compact_terms):
        return 2
    tokens = [_compact(token) for token in normalized_query.split() if _compact(token)]
    if tokens and all(any(token in term for term in compact_terms) for token in tokens):
        return 3
    return None


def search_minecraft_knowledge(
    query: str,
    *,
    dataset_ids: Iterable[str] = (),
    record_types: Iterable[str] = (),
    registry_ids: Iterable[str] = (),
    limit: int = 20,
    cache_root: Path | None = None,
) -> list[dict[str, Any]]:
    """公式ID、日本語名、分類語を使って版固定の技術レコードを検索する。"""

    if limit < 1:
        return []
    context = _load_context(cache_root)
    requested_types = {_normalize(value) for value in record_types if _normalize(value)}
    requested_registries = {
        _normalize(value) for value in registry_ids if _normalize(value)
    }
    normalized_query = _normalize(query)
    candidates: list[tuple[_WorstFirst, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for entry in _iter_index_entries(context, dataset_ids=dataset_ids):
        if requested_types and _normalize(entry.get("record_type", "")) not in requested_types:
            continue
        if requested_registries and _normalize(entry.get("registry_id", "")) not in requested_registries:
            continue
        if entry["id"] in seen_ids:
            raise ValueError(f"Minecraft DB索引IDが重複しています: {entry['id']}")
        seen_ids.add(entry["id"])
        score = _entry_score(entry, normalized_query)
        if score is None:
            continue
        key = (score, entry["id"])
        item = (_WorstFirst(key), entry)
        if len(candidates) < limit:
            heapq.heappush(candidates, item)
        elif key < candidates[0][0].key:
            heapq.heapreplace(candidates, item)
    ranked = [entry for _, entry in sorted(candidates, key=lambda item: item[0].key)]
    records = _load_entries(ranked, context)
    return [
        {"dataset_id": entry["dataset_id"], **record}
        for entry, record in zip(ranked, records, strict=True)
    ]


def get_minecraft_knowledge(
    record_id: str,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any] | None:
    """正規化レコードの安定IDを指定して一件取得する。"""

    context = _load_context(cache_root)
    for entry in _iter_index_entries(context):
        if entry["id"] == record_id:
            return _load_entries([entry], context)[0]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ドギド Minecraft Java 公式技術DB検索")
    parser.add_argument("query", help="公式ID、日本語名、分類語")
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--record-type", action="append", default=[])
    parser.add_argument("--registry", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--id", action="store_true", help="検索語を正規化レコードIDとして一件取得")
    args = parser.parse_args(argv)
    if args.id:
        result: object = get_minecraft_knowledge(args.query)
    else:
        result = search_minecraft_knowledge(
            args.query,
            dataset_ids=args.dataset,
            record_types=args.record_type,
            registry_ids=args.registry,
            limit=args.limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
