"""国語教育・世界詩参照カタログの読み取り専用アクセス。

このモジュールは生成済み ``index.json`` だけを検索し、外部サイトへは
アクセスしない。検索対象も、各レコードが明示した ID・正式名・別名・
検索語内の一致と構造化ファセットに限定する。説明文の曖昧な全文検索や、LLM プロンプト
への自動注入は行わない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any


REFERENCE_DIR = (
    Path(__file__).resolve().parents[2]
    / "reference"
    / "language_education_and_poetry"
)


def _normalize(value: object) -> str:
    # 国語研の文型名に混在する全角チルダ・波ダッシュを検索時だけ統一する。
    return (
        unicodedata.normalize("NFKC", str(value))
        .replace("～", "~")
        .replace("〜", "~")
        .strip()
        .casefold()
    )


def _compact(value: object) -> str:
    return "".join(_normalize(value).split())


def load_reference_index(reference_dir: Path | None = None) -> dict[str, Any]:
    """生成済み検索索引を読み込む。"""

    base = reference_dir or REFERENCE_DIR
    index_path = _safe_dataset_path(base, "index.json")
    if not index_path.is_file():
        raise FileNotFoundError(
            f"参照カタログが見つかりません: {index_path}. "
            "リポジトリまたはeditable installから実行してください。"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    _validate_indexed_datasets(index, base)
    return index


@lru_cache(maxsize=128)
def _verify_dataset_digest(
    path_string: str,
    expected_sha256: str,
    size: int,
    modified_ns: int,
    device: int,
    inode: int,
    changed_ns: int,
) -> None:
    """ファイル状態が変わらない間だけ、2MB級カタログの再ハッシュを省く。"""

    del size, modified_ns, device, inode, changed_ns  # キャッシュキーとして使用する。
    actual = hashlib.sha256(Path(path_string).read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"reference dataset hash mismatch: {Path(path_string).name}")


def _validate_indexed_datasets(index: object, base: Path) -> None:
    if not isinstance(index, dict) or index.get("schema_version") != 1:
        raise ValueError("invalid reference index")
    rows = index.get("datasets")
    entries = index.get("entries")
    if not isinstance(rows, list) or not rows or not isinstance(entries, dict):
        raise ValueError("invalid reference index datasets")
    validated_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid reference index dataset row")
        expected = str(row.get("sha256") or "")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ValueError("invalid reference index dataset hash")
        path = _safe_dataset_path(base, row.get("path"))
        stat = path.stat()
        _verify_dataset_digest(
            str(path.resolve()),
            expected,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_dev,
            stat.st_ino,
            stat.st_ctime_ns,
        )
        validated_paths.add(path.name)
    for entry in entries.values():
        if (
            not isinstance(entry, dict)
            or str(entry.get("dataset_path") or "") not in validated_paths
        ):
            raise ValueError("reference index entry uses an unverified dataset")


def _safe_dataset_path(base: Path, value: object) -> Path:
    raw = str(value)
    relative = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) != 1
        or raw != relative.name
        or relative.suffix.casefold() != ".json"
    ):
        raise ValueError(f"unsafe reference dataset path: {value!r}")

    lexical_base = base.absolute()
    lexical_path = lexical_base / relative.name
    if lexical_base.is_symlink() or lexical_path.is_symlink():
        raise ValueError(f"unsafe reference dataset path: {value!r}")
    resolved_base = lexical_base.resolve()
    resolved_path = lexical_path.resolve()
    if resolved_path.parent != resolved_base:
        raise ValueError(f"unsafe reference dataset path: {value!r}")
    return resolved_path


def _load_record(entry: dict[str, Any], base: Path) -> dict[str, Any]:
    dataset_path = _safe_dataset_path(base, entry["dataset_path"])
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    records = payload[entry["record_key"]]
    record = records[int(entry["record_index"])]
    if record.get("id") != entry["id"]:
        raise ValueError(f"index mismatch for {entry['id']}")
    return record


def get_reference(
    record_id: str,
    *,
    reference_dir: Path | None = None,
) -> dict[str, Any] | None:
    """安定 ID で完全なレコードを1件取得する。"""

    base = reference_dir or REFERENCE_DIR
    index = load_reference_index(base)
    entry = index["entries"].get(record_id)
    if not entry:
        return None
    return _load_record(entry, base)


def _facet_ids(
    index: dict[str, Any],
    facet: str,
    values: Iterable[str],
) -> set[str] | None:
    requested = [_normalize(value) for value in values if _normalize(value)]
    if not requested:
        return None
    matches: set[str] = set()
    table = index[facet]
    for value in requested:
        matches.update(table.get(value, ()))
    return matches


def _token_ids(index: dict[str, Any], token: str) -> set[str]:
    """明示語の完全一致を優先し、なければn-gram索引で部分一致する。"""

    direct = set(index["by_term"].get(token, ()))
    compact = _compact(token)
    if not compact:
        return set()
    size = min(3, len(compact))
    grams = {compact[start : start + size] for start in range(len(compact) - size + 1)}
    matches: set[str] | None = None
    for gram in grams:
        ids = set(index["by_term_ngram"].get(gram, ()))
        matches = ids if matches is None else matches & ids
    if not matches:
        return direct
    partial = {
        record_id
        for record_id in matches
        if any(
            compact in _compact(value)
            for value in index["entries"][record_id]["explicit_terms"]
        )
    }
    return direct | partial


def _result_score(entry: dict[str, Any], normalized_query: str) -> int:
    """正式名、別名、その他の明示語、部分一致の順に並べる。"""

    if not normalized_query:
        return 4
    if _normalize(entry.get("title_ja", "")) == normalized_query:
        return 0
    if any(_normalize(value) == normalized_query for value in entry.get("aliases", ())):
        return 1
    if any(
        _normalize(value) == normalized_query
        for value in entry.get("explicit_terms", ())
    ):
        return 2
    return 3


def search_references(
    query: str = "",
    *,
    dataset_ids: Iterable[str] = (),
    kinds: Iterable[str] = (),
    tags: Iterable[str] = (),
    regions: Iterable[str] = (),
    languages: Iterable[str] = (),
    source_ids: Iterable[str] = (),
    entity_kinds: Iterable[str] = (),
    expression_modes: Iterable[str] = (),
    formal_constraints: Iterable[str] = (),
    prosodic_bases: Iterable[str] = (),
    transmission_modes: Iterable[str] = (),
    composition_modes: Iterable[str] = (),
    education_stages: Iterable[str] = (),
    subject_areas: Iterable[str] = (),
    acquisition_modes: Iterable[str] = (),
    machine_readable: Iterable[str] = (),
    local_runtime_allowed: Iterable[str] = (),
    providers: Iterable[str] = (),
    limit: int = 20,
    reference_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """明示検索語と構造化ファセットだけで索引を検索する。

    完全一致、空白を除いた連続一致、空白区切りの AND 条件の順で、
    明示語内の部分一致索引を使う。説明文は検索しない。
    """

    if limit < 1:
        return []
    index = load_reference_index(reference_dir)
    candidates = set(index["entries"])

    normalized_query = _normalize(query)
    if normalized_query:
        direct = set(index["by_term"].get(normalized_query, ()))
        if direct:
            candidates &= direct
        else:
            compact_query = _compact(normalized_query)
            compact_matches = _token_ids(index, compact_query)
            if compact_matches:
                candidates &= compact_matches
            else:
                for token in normalized_query.split():
                    candidates &= _token_ids(index, token)

    filters = (
        ("by_dataset", dataset_ids),
        ("by_kind", kinds),
        ("by_tag", tags),
        ("by_region", regions),
        ("by_language", languages),
        ("by_source", source_ids),
        ("by_entity_kind", entity_kinds),
        ("by_expression_mode", expression_modes),
        ("by_formal_constraint", formal_constraints),
        ("by_prosodic_basis", prosodic_bases),
        ("by_transmission_mode", transmission_modes),
        ("by_composition_mode", composition_modes),
        ("by_education_stage", education_stages),
        ("by_subject_area", subject_areas),
        ("by_acquisition_mode", acquisition_modes),
        ("by_machine_readable", machine_readable),
        ("by_local_runtime_allowed", local_runtime_allowed),
        ("by_provider", providers),
    )
    for facet, values in filters:
        matched = _facet_ids(index, facet, values)
        if matched is not None:
            candidates &= matched

    ranked = sorted(
        (index["entries"][record_id] for record_id in candidates),
        key=lambda entry: (_result_score(entry, normalized_query), entry["id"]),
    )
    return ranked[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="国語教育・世界詩参照カタログ検索")
    parser.add_argument("query", nargs="?", default="", help="明示検索語（連続一致を優先し、空白区切りはAND）")
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--kind", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--region", action="append", default=[])
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--entity-kind", action="append", default=[])
    parser.add_argument("--expression-mode", action="append", default=[])
    parser.add_argument("--formal-constraint", action="append", default=[])
    parser.add_argument("--prosodic-basis", action="append", default=[])
    parser.add_argument("--transmission-mode", action="append", default=[])
    parser.add_argument("--composition-mode", action="append", default=[])
    parser.add_argument("--education-stage", action="append", default=[])
    parser.add_argument("--subject-area", action="append", default=[])
    parser.add_argument("--acquisition-mode", action="append", default=[])
    parser.add_argument("--machine-readable", action="append", default=[])
    parser.add_argument("--local-runtime-allowed", action="append", default=[])
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--full", action="store_true", help="完全なレコードを返す")
    args = parser.parse_args(argv)

    hits = search_references(
        args.query,
        dataset_ids=args.dataset,
        kinds=args.kind,
        tags=args.tag,
        regions=args.region,
        languages=args.language,
        source_ids=args.source,
        entity_kinds=args.entity_kind,
        expression_modes=args.expression_mode,
        formal_constraints=args.formal_constraint,
        prosodic_bases=args.prosodic_basis,
        transmission_modes=args.transmission_mode,
        composition_modes=args.composition_mode,
        education_stages=args.education_stage,
        subject_areas=args.subject_area,
        acquisition_modes=args.acquisition_mode,
        machine_readable=args.machine_readable,
        local_runtime_allowed=args.local_runtime_allowed,
        providers=args.provider,
        limit=args.limit,
    )
    if args.full:
        result = [get_reference(hit["id"]) for hit in hits]
    else:
        result = hits
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
