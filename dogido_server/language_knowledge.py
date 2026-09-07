"""国語知識データベースのローカル専用検索API。

公式配布データの正規化版と、出典を明示した知識レコードだけを読む。
実行時の外部通信、LLM呼び出し、プロンプトへの自動注入は行わない。
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from dogido_server.reference_catalog import (
    REFERENCE_DIR,
    _compact,
    _normalize,
    get_reference,
    search_references,
)


NORMALIZED_DIR = REFERENCE_DIR / "data" / "normalized"
BULK_INDEX_PATH = NORMALIZED_DIR / "index.json"
CORE_DATASET_IDS = (
    "japanese_grammar",
    "historical_kana_and_scripts",
    "makurakotoba",
    "japanese_poetry_forms",
)


class _WorstFirst:
    """heapの根に、現在保持している中で最も低順位のキーを置く。"""

    __slots__ = ("key",)

    def __init__(self, key: tuple[int, str]) -> None:
        self.key = key

    def __lt__(self, other: _WorstFirst) -> bool:
        return self.key > other.key


@lru_cache(maxsize=8)
def _load_bulk_index_path(
    path: Path,
    mtime_ns: int,
    size: int,
    inode: int,
) -> dict[str, Any]:
    # mtime・size・inodeは、os.replace後に旧索引を使い続けないためのcache key。
    del mtime_ns, size, inode
    if not path.is_file():
        raise FileNotFoundError(
            f"正規化済み国語データ索引が見つかりません: {path}. "
            "scripts/build_language_knowledge_data.py を実行してください。"
        )
    index = json.loads(path.read_text(encoding="utf-8"))
    _verify_bulk_artifacts(index, reference_dir=path.parents[2])
    return index


def load_bulk_index(reference_dir: Path | None = None) -> dict[str, Any]:
    base = (reference_dir or REFERENCE_DIR).resolve()
    path = base / "data" / "normalized" / "index.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"正規化済み国語データ索引が見つかりません: {path}. "
            "scripts/build_language_knowledge_data.py を実行してください。"
        )
    stat = path.stat()
    return _load_bulk_index_path(
        path,
        stat.st_mtime_ns,
        stat.st_size,
        stat.st_ino,
    )


def _safe_dataset_path(
    base: Path,
    value: object,
    *,
    suffix: str = ".jsonl",
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
        raise ValueError(f"unsafe normalized dataset path: {value!r}")
    lexical_dir = base / "data" / "normalized"
    lexical_candidate = lexical_dir / relative.name
    if lexical_dir.is_symlink() or lexical_candidate.is_symlink():
        raise ValueError(f"unsafe normalized dataset path: {value!r}")
    normalized_dir = lexical_dir.resolve()
    candidate = lexical_candidate.resolve()
    if candidate.parent != normalized_dir:
        raise ValueError(f"unsafe normalized dataset path: {value!r}")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_handle(handle: Any) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_bulk_artifacts(
    index: object, *, reference_dir: Path
) -> None:
    """公開単位の索引とデータが同一世代か、読込み前に確認する。"""

    if not isinstance(index, dict) or index.get("schema_version") != 2:
        raise ValueError("unsupported normalized knowledge index")
    datasets = index.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("invalid normalized knowledge datasets")
    seen_ids: set[str] = set()
    for row in datasets:
        if not isinstance(row, dict):
            raise ValueError("invalid normalized knowledge dataset row")
        dataset_id = str(row.get("id", ""))
        if not dataset_id or dataset_id in seen_ids:
            raise ValueError(f"invalid normalized dataset id: {dataset_id!r}")
        seen_ids.add(dataset_id)
        for path_key, hash_key, suffix in (
            ("path", "sha256", ".jsonl"),
            ("index_path", "index_sha256", ".index.jsonl"),
        ):
            path = _safe_dataset_path(
                reference_dir,
                row.get(path_key, ""),
                suffix=suffix,
            )
            expected_hash = row.get(hash_key)
            if not path.is_file() or not isinstance(expected_hash, str):
                raise ValueError(f"missing normalized artifact: {dataset_id}:{path_key}")
            if _sha256(path) != expected_hash:
                raise ValueError(
                    f"normalized artifact hash mismatch: {dataset_id}:{path.name}"
                )


def _dataset_rows(
    index: dict[str, Any], dataset_ids: Iterable[str] = ()
) -> list[dict[str, Any]]:
    rows = {str(row["id"]): row for row in index.get("datasets", [])}
    requested = tuple(
        dict.fromkeys(_normalize(value) for value in dataset_ids if value)
    )
    return [rows[value] for value in (requested or tuple(rows)) if value in rows]


def _iter_bulk_entries(
    index: dict[str, Any],
    *,
    dataset_ids: Iterable[str] = (),
    reference_dir: Path | None = None,
) -> Iterable[dict[str, Any]]:
    """データセット別索引を一行ずつ読み、巨大な辞書を常駐させない。"""

    base = (reference_dir or REFERENCE_DIR).resolve()
    for dataset in _dataset_rows(index, dataset_ids):
        path = _safe_dataset_path(
            base,
            dataset["index_path"],
            suffix=".index.jsonl",
        )
        with path.open("rb") as handle:
            if _sha256_handle(handle) != dataset.get("index_sha256"):
                raise ValueError(
                    f"normalized artifact hash mismatch: {dataset['id']}:{path.name}"
                )
            handle.seek(0)
            for line_number, line in enumerate(handle, 1):
                entry = json.loads(line)
                if (
                    entry.get("dataset_id") != dataset["id"]
                    or entry.get("dataset_path") != dataset["path"]
                ):
                    raise ValueError(
                        f"normalized dataset index mismatch: {path.name}:{line_number}"
                    )
                yield {
                    **entry,
                    "_dataset_sha256": dataset["sha256"],
                }


def _load_bulk_entry(
    entry: dict[str, Any],
    *,
    reference_dir: Path | None = None,
) -> dict[str, Any]:
    return _load_bulk_entries([entry], reference_dir=reference_dir)[0]


def _load_bulk_entries(
    entries: Iterable[dict[str, Any]],
    *,
    reference_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """同じデータセットを一度だけ検証し、複数のバイト位置を読む。"""

    base = (reference_dir or REFERENCE_DIR).resolve()
    ordered_entries = list(entries)
    groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for position, entry in enumerate(ordered_entries):
        key = (str(entry["dataset_path"]), str(entry.get("_dataset_sha256", "")))
        groups.setdefault(key, []).append((position, entry))
    loaded: list[dict[str, Any] | None] = [None] * len(ordered_entries)
    for (dataset_path, expected_hash), group in groups.items():
        path = _safe_dataset_path(base, dataset_path)
        with path.open("rb") as handle:
            if _sha256_handle(handle) != expected_hash:
                first_entry = group[0][1]
                raise ValueError(
                    f"normalized artifact hash mismatch: "
                    f"{first_entry.get('dataset_id')}:{path.name}"
                )
            size = handle.tell()
            for position, entry in group:
                offset = int(entry["byte_offset"])
                length = int(entry["byte_length"])
                if offset < 0 or length < 2 or offset + length > size:
                    raise ValueError(f"invalid byte locator for {entry.get('id')}")
                handle.seek(offset)
                raw = handle.read(length)
                record = json.loads(raw)
                if record.get("id") != entry.get("id"):
                    raise ValueError(f"normalized index mismatch for {entry.get('id')}")
                loaded[position] = record
    if any(record is None for record in loaded):
        raise ValueError("normalized record load was incomplete")
    return [record for record in loaded if record is not None]


def get_bulk_knowledge(
    record_id: str,
    *,
    reference_dir: Path | None = None,
) -> dict[str, Any] | None:
    """正規化済み公式データを安定IDで1件取得する。"""

    index = load_bulk_index(reference_dir)
    for entry in _iter_bulk_entries(index, reference_dir=reference_dir):
        if entry.get("id") == record_id:
            record = _load_bulk_entry(entry, reference_dir=reference_dir)
            return {"dataset_id": entry["dataset_id"], **record}
    return None


def _bulk_entry_score(
    entry: dict[str, Any], normalized_query: str
) -> int | None:
    if not normalized_query:
        return 3
    title = _normalize(entry.get("title_ja", ""))
    normalized_terms = [
        _normalize(value) for value in entry.get("search_terms", ()) if value
    ]
    if title == normalized_query:
        return 0
    if normalized_query in normalized_terms:
        return 1
    terms = [_compact(value) for value in normalized_terms]
    compact_query = _compact(normalized_query)
    query_tokens = [_compact(token) for token in normalized_query.split()]
    if any(compact_query in term for term in terms):
        return 2
    if all(any(token in term for term in terms) for token in query_tokens):
        return 3
    return None


def search_bulk_knowledge(
    query: str,
    *,
    dataset_ids: Iterable[str] = (),
    kinds: Iterable[str] = (),
    limit: int = 20,
    reference_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """文型・教育語彙・国語科コードを明示語だけで検索する。"""

    if limit < 1:
        return []
    index = load_bulk_index(reference_dir)
    normalized_query = _normalize(query)
    requested_kinds = {_normalize(value) for value in kinds if value}
    candidates: list[tuple[_WorstFirst, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for entry in _iter_bulk_entries(
        index,
        dataset_ids=dataset_ids,
        reference_dir=reference_dir,
    ):
        if requested_kinds and _normalize(entry.get("kind", "")) not in requested_kinds:
            continue
        score = _bulk_entry_score(entry, normalized_query)
        if score is None or entry["id"] in seen_ids:
            continue
        seen_ids.add(entry["id"])
        key = (score, entry["id"])
        item = (_WorstFirst(key), entry)
        if len(candidates) < limit:
            heapq.heappush(candidates, item)
        elif key < candidates[0][0].key:
            heapq.heapreplace(candidates, item)
    ranked_entries = [
        entry for _, entry in sorted(candidates, key=lambda item: item[0].key)
    ]
    loaded_records = _load_bulk_entries(
        ranked_entries,
        reference_dir=reference_dir,
    )
    results: list[dict[str, Any]] = []
    for entry, record in zip(ranked_entries, loaded_records, strict=True):
        record = {"dataset_id": entry["dataset_id"], **record}
        results.append(record)
    return results


def _knowledge_result_score(
    record: dict[str, Any], normalized_query: str
) -> tuple[int, str]:
    if not normalized_query:
        return (4, record["id"])
    if _normalize(record.get("title_ja", "")) == normalized_query:
        return (0, record["id"])
    if any(
        _normalize(value) == normalized_query for value in record.get("aliases", ())
    ):
        return (1, record["id"])
    if any(
        _normalize(value) == normalized_query
        for value in record.get("search_terms", ())
    ):
        return (2, record["id"])
    return (3, record["id"])


def _rank_knowledge_results(
    records: Iterable[dict[str, Any]], query: str, limit: int
) -> list[dict[str, Any]]:
    by_id = {record["id"]: record for record in records}
    normalized_query = _normalize(query)
    return sorted(
        by_id.values(),
        key=lambda record: _knowledge_result_score(record, normalized_query),
    )[:limit]


def search_japanese_knowledge(
    query: str,
    *,
    dataset_ids: Iterable[str] = (),
    kinds: Iterable[str] = (),
    limit: int = 20,
    reference_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """手整備の知識レコードと公式配布データを一つの入口から検索する。"""

    if limit < 1:
        return []
    requested = tuple(
        dict.fromkeys(_normalize(value) for value in dataset_ids if _normalize(value))
    )
    requested_kinds = tuple(kinds)
    core_requested = tuple(
        value for value in (requested or CORE_DATASET_IDS) if value in CORE_DATASET_IDS
    )
    results: list[dict[str, Any]] = []
    if core_requested:
        hits = search_references(
            query,
            dataset_ids=core_requested,
            kinds=requested_kinds,
            limit=limit,
            reference_dir=reference_dir,
        )
        hits.sort(
            key=lambda hit: (
                _normalize(hit["title_ja"]) != _normalize(query),
                hit["id"],
            )
        )
        for hit in hits:
            record = get_reference(hit["id"], reference_dir=reference_dir)
            if record is not None:
                results.append({"dataset_id": hit["dataset_id"], **record})
                if len(results) >= limit:
                    break
    # 手整備データセットだけを明示した検索では、大容量側の索引に触れない。
    if requested and all(value in CORE_DATASET_IDS for value in requested):
        return _rank_knowledge_results(results, query, limit)
    bulk_index = load_bulk_index(reference_dir)
    bulk_dataset_ids = {row["id"] for row in bulk_index["datasets"]}
    bulk_requested = tuple(
        value
        for value in (requested or sorted(bulk_dataset_ids))
        if value in bulk_dataset_ids
    )
    # 手整備側だけで上限に達していても、大容量側に完全一致があれば
    # そちらを優先できるよう、双方から候補を集めてから全体を順位付けする。
    if bulk_requested:
        results.extend(
            search_bulk_knowledge(
                query,
                dataset_ids=bulk_requested,
                kinds=requested_kinds,
                limit=limit,
                reference_dir=reference_dir,
            )
        )
    return _rank_knowledge_results(results, query, limit)


def get_kanji_profile(
    character: str,
    *,
    reference_dir: Path | None = None,
) -> dict[str, Any] | None:
    """一字について、常用漢字表と配当学年を出典別のまままとめる。"""

    if len(character) != 1:
        raise ValueError("漢字プロフィールには一字を指定してください")
    codepoint = ord(character)
    joyo = get_bulk_knowledge(
        f"kanji.joyo.u{codepoint:x}",
        reference_dir=reference_dir,
    )
    grade = get_bulk_knowledge(
        f"kanji.grade-allocation.u{codepoint:x}",
        reference_dir=reference_dir,
    )
    if joyo is None and grade is None:
        return None
    return {
        "character": character,
        "codepoint": f"U+{codepoint:04X}",
        "joyo_kanji": joyo,
        "grade_level_kanji_allocation": grade,
        "source_boundary": "separate_official_tables",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ドギド国語知識データベース検索")
    parser.add_argument("query", help="明示語、文型、語彙、学習指導要領コード")
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--kind", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--kanji-profile",
        action="store_true",
        help="一字について常用漢字表と学年別漢字配当表を出典別に表示する",
    )
    args = parser.parse_args(argv)
    if args.kanji_profile:
        profile = get_kanji_profile(args.query)
        print(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    records = search_japanese_knowledge(
        args.query,
        dataset_ids=args.dataset,
        kinds=args.kind,
        limit=args.limit,
    )
    print(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
