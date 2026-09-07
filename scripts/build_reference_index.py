"""国語教育・世界詩参照カタログの決定的な索引を生成する。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference" / "language_education_and_poetry"
INDEX_PATH = REFERENCE_DIR / "index.json"
DATASETS = (
    ("sources", "sources.json", "sources"),
    ("japanese_language_education", "japanese_language_education.json", "resources"),
    ("japanese_grammar", "japanese_grammar.json", "entries"),
    ("historical_kana_and_scripts", "historical_kana_and_scripts.json", "entries"),
    ("makurakotoba", "makurakotoba.json", "entries"),
    ("japanese_poetry_forms", "japanese_poetry_forms.json", "entries"),
    ("world_poetry", "world_poetry.json", "entries"),
)

CLASSIFICATION_FACETS = {
    "entity_kinds": "by_entity_kind",
    "expression_modes": "by_expression_mode",
    "formal_constraints": "by_formal_constraint",
    "prosodic_bases": "by_prosodic_basis",
    "transmission_modes": "by_transmission_mode",
    "composition_modes": "by_composition_mode",
}


def _validate_output_path(path: Path) -> Path:
    """生成先にsymlinkを含めず、リポジトリ外への書込みを拒否する。"""

    lexical_root = ROOT.absolute()
    lexical_path = path.absolute()
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError as error:
        raise ValueError(f"output path is outside repository: {path}") from error
    cursor = lexical_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"output path contains symlink: {path}")
    if not lexical_path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f"output path escapes repository: {path}")
    return lexical_path


def normalize(value: object) -> str:
    return (
        unicodedata.normalize("NFKC", str(value))
        .replace("～", "~")
        .replace("〜", "~")
        .strip()
        .casefold()
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _add(table: dict[str, set[str]], values: object, record_id: str) -> None:
    for value in _as_list(values):
        key = normalize(value)
        if key:
            table[key].add(record_id)


def _add_ngrams(
    table: dict[str, set[str]],
    values: object,
    record_id: str,
) -> None:
    """明示検索語だけから1〜3文字の部分一致索引を作る。"""

    for value in _as_list(values):
        compact = "".join(normalize(value).split())
        for size in range(1, min(3, len(compact)) + 1):
            for start in range(len(compact) - size + 1):
                table[compact[start : start + size]].add(record_id)


def _source_ids(record: dict[str, Any]) -> list[str]:
    if record.get("kind") == "source":
        return [str(record["id"])]
    return sorted(
        {
            str(ref["source_id"])
            for ref in record.get("source_refs", [])
            if isinstance(ref, dict) and ref.get("source_id")
        }
    )


def _classification_values(record: dict[str, Any]) -> dict[str, list[str]]:
    classification = record.get("classification")
    if not isinstance(classification, dict):
        classification = {}
    return {
        "entity_kinds": _as_list(record.get("entity_kind")),
        "expression_modes": _as_list(classification.get("expression_modes")),
        "formal_constraints": _as_list(classification.get("formal_constraints")),
        "prosodic_bases": _as_list(classification.get("prosodic_bases")),
        "transmission_modes": _as_list(classification.get("transmission_modes")),
        "composition_modes": _as_list(classification.get("composition_modes")),
    }


def _classification_labels(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    axes = payload.get("classification_axes")
    if not isinstance(axes, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for axis_name in CLASSIFICATION_FACETS:
        options = axes.get(axis_name)
        if not isinstance(options, list):
            continue
        result[axis_name] = {
            str(option["id"]): str(option["label_ja"])
            for option in options
            if isinstance(option, dict)
            and option.get("id")
            and option.get("label_ja")
        }
    return result


def build_index() -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    facets: dict[str, dict[str, set[str]]] = {
        name: defaultdict(set)
        for name in (
            "by_term",
            "by_term_ngram",
            "by_dataset",
            "by_kind",
            "by_tag",
            "by_region",
            "by_language",
            "by_source",
            "by_entity_kind",
            "by_expression_mode",
            "by_formal_constraint",
            "by_prosodic_basis",
            "by_transmission_mode",
            "by_composition_mode",
            "by_education_stage",
            "by_subject_area",
            "by_acquisition_mode",
            "by_machine_readable",
            "by_local_runtime_allowed",
            "by_provider",
        )
    }
    dataset_rows: list[dict[str, Any]] = []

    for dataset_id, filename, record_key in DATASETS:
        path = REFERENCE_DIR / filename
        raw = path.read_bytes()
        payload = json.loads(raw)
        records = payload[record_key]
        classification_labels = _classification_labels(payload)
        dataset_rows.append(
            {
                "id": dataset_id,
                "path": filename,
                "record_count": len(records),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        for position, record in enumerate(records):
            record_id = str(record["id"])
            if record_id in entries:
                raise ValueError(f"duplicate id: {record_id}")
            aliases = _as_list(record.get("aliases"))
            search_terms = _as_list(record.get("search_terms"))
            classification_values = _classification_values(record)
            classification_terms = [
                label
                for axis_name, values in classification_values.items()
                for value in values
                if (label := classification_labels.get(axis_name, {}).get(value))
            ]
            explicit_terms = [
                record_id,
                str(record["title_ja"]),
                str(record.get("title_original", "")),
                *aliases,
                *search_terms,
                *classification_terms,
            ]
            explicit_terms = sorted({term for term in explicit_terms if term})
            tags = sorted(set(_as_list(record.get("tags"))))
            regions = sorted(set(_as_list(record.get("regions"))))
            languages = sorted(set(_as_list(record.get("languages"))))
            source_ids = _source_ids(record)
            entry = {
                "id": record_id,
                "dataset_id": dataset_id,
                "dataset_path": filename,
                "record_key": record_key,
                "record_index": position,
                "kind": str(record["kind"]),
                "title_ja": str(record["title_ja"]),
                "title_original": str(record.get("title_original", "")),
                "aliases": aliases,
                "search_terms": search_terms,
                "explicit_terms": explicit_terms,
                "tags": tags,
                "regions": regions,
                "languages": languages,
                "source_ids": source_ids,
            }
            entries[record_id] = entry
            _add(facets["by_term"], explicit_terms, record_id)
            _add_ngrams(facets["by_term_ngram"], explicit_terms, record_id)
            _add(facets["by_dataset"], dataset_id, record_id)
            _add(facets["by_kind"], record["kind"], record_id)
            _add(facets["by_tag"], tags, record_id)
            _add(facets["by_region"], regions, record_id)
            _add(facets["by_language"], languages, record_id)
            _add(facets["by_source"], source_ids, record_id)
            for axis_name, facet_name in CLASSIFICATION_FACETS.items():
                values = classification_values[axis_name]
                labels = [
                    classification_labels.get(axis_name, {}).get(value)
                    for value in values
                ]
                _add(facets[facet_name], values, record_id)
                _add(facets[facet_name], [label for label in labels if label], record_id)
            _add(
                facets["by_education_stage"],
                record.get("education_stages"),
                record_id,
            )
            _add(facets["by_subject_area"], record.get("subject_areas"), record_id)
            _add(
                facets["by_acquisition_mode"],
                record.get("acquisition_mode"),
                record_id,
            )
            if "machine_readable" in record:
                _add(
                    facets["by_machine_readable"],
                    str(record["machine_readable"]).lower(),
                    record_id,
                )
            if "local_runtime_allowed" in record:
                _add(
                    facets["by_local_runtime_allowed"],
                    str(record["local_runtime_allowed"]).lower(),
                    record_id,
                )
            _add(facets["by_provider"], record.get("provider"), record_id)

    index: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_date": "2026-09-01",
        "generator": "scripts/build_reference_index.py",
        "search_policy": "explicit_terms_and_structured_facets_only",
        "datasets": dataset_rows,
        "entries": dict(sorted(entries.items())),
    }
    for facet_name, table in facets.items():
        index[facet_name] = {
            key: sorted(ids) for key, ids in sorted(table.items())
        }
    return index


def serialized_index() -> str:
    return json.dumps(build_index(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="生成済み索引との差分だけ確認")
    args = parser.parse_args(argv)
    _validate_output_path(INDEX_PATH)
    expected = serialized_index()
    if args.check:
        actual = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
        if actual != expected:
            print("index.json is stale; run python scripts/build_reference_index.py", file=sys.stderr)
            return 1
        print("reference index is current")
        return 0
    INDEX_PATH.write_text(expected, encoding="utf-8")
    print(f"wrote {INDEX_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
