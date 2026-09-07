"""国語教育・世界詩参照カタログ専用の構造・権利境界チェッカー。"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "reference" / "language_education_and_poetry"
ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
JP_ALLOWED_ORGANIZATION_TYPES = {
    "national_government",
    "government_agency",
    "national_research_institute",
    "national_library",
    "national_university",
    "public_university",
    "local_government",
    "international_standards_body",
}
ACQUISITION_MODES = {
    "official_download",
    "official_api",
    "official_link",
    "item_rights_check",
    "manual_research_only",
}
SUSPECT_JAPANESE = {
    "型譜",
    "別字段",
    "規則詩",
    "口演・歌唱詩形",
    "詩歌伝統",
    "profile",
    "validator",
    "fail-open",
}
MIRRORED_CONTENT_KEYS = {
    "audio_transcript",
    "corpus_text",
    "full_text",
    "lyrics",
    "poem_text",
    "question_text",
}
NORMALIZED_DATASET_KINDS = {
    "grammar_patterns": "grammar_pattern",
    "education_basic_vocabulary": "education_vocabulary",
    "japanese_education_basic_vocabulary": "japanese_education_vocabulary",
    "historical_hiragana_unicode": "historical_hiragana_character",
    "curriculum_japanese": "curriculum_code",
    "joyo_kanji": "joyo_kanji_entry",
    "grade_level_kanji_allocation": "grade_level_kanji_allocation",
}


def load(name: str) -> dict[str, Any]:
    return json.loads((REFERENCE_DIR / name).read_text(encoding="utf-8"))


def _duplicate_json_key_issues(path: Path) -> list[str]:
    """JSONオブジェクト内の重複キーを、後勝ちで見落とさず報告する。"""

    duplicates: list[str] = []

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                duplicates.append(key)
            value[key] = child
        return value

    try:
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return [f"invalid JSON in {path.relative_to(REFERENCE_DIR)}: {error}"]
    return [
        f"duplicate JSON key in {path.relative_to(REFERENCE_DIR)}: {key}"
        for key in duplicates
    ]


def _normalized_index_entry_issues(
    entry: object, *, expected_dataset_id: str
) -> list[str]:
    if not isinstance(entry, dict):
        return [f"normalized index entry is not an object: {entry!r}"]
    entry_id = str(entry.get("id", ""))
    issues: list[str] = []
    if not ID_RE.fullmatch(entry_id):
        issues.append(f"invalid normalized id: {entry_id!r}")
    if entry.get("dataset_id") != expected_dataset_id:
        issues.append(f"normalized entry dataset mismatch: {entry_id}")
    expected_kind = NORMALIZED_DATASET_KINDS.get(expected_dataset_id)
    if entry.get("kind") != expected_kind:
        issues.append(f"invalid normalized index kind: {entry_id}")
    if not isinstance(entry.get("title_ja"), str) or not entry["title_ja"].strip():
        issues.append(f"invalid normalized index title: {entry_id}")
    terms = entry.get("search_terms")
    if (
        not isinstance(terms, list)
        or any(not isinstance(value, str) or not value.strip() for value in terms)
        or len(terms) != len(set(terms))
    ):
        issues.append(f"invalid normalized index terms: {entry_id}")
    return issues


def _normalized_record_issues(
    record: object, *, expected_dataset_id: str
) -> list[str]:
    if not isinstance(record, dict):
        return [f"normalized record is not an object: {record!r}"]
    record_id = str(record.get("id", ""))
    issues: list[str] = []
    required = {"id", "kind", "title_ja", "search_terms", "source_id", "license"}
    missing = required - record.keys()
    if missing:
        issues.append(f"{record_id}: normalized record missing {sorted(missing)}")
    if not ID_RE.fullmatch(record_id):
        issues.append(f"invalid normalized record id: {record_id!r}")
    expected_kind = NORMALIZED_DATASET_KINDS.get(expected_dataset_id)
    if record.get("kind") != expected_kind:
        issues.append(f"invalid normalized record kind: {record_id}")
    for field in ("title_ja", "source_id", "license"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            issues.append(f"{record_id}: invalid normalized {field}")
    if isinstance(record.get("source_id"), str) and not ID_RE.fullmatch(
        record["source_id"]
    ):
        issues.append(f"{record_id}: invalid normalized source_id")
    terms = record.get("search_terms")
    if (
        not isinstance(terms, list)
        or any(not isinstance(value, str) or not value.strip() for value in terms)
        or len(terms) != len(set(terms))
    ):
        issues.append(f"{record_id}: invalid normalized search_terms")
    if expected_dataset_id in {"joyo_kanji", "grade_level_kanji_allocation"}:
        character = record.get("character")
        if (
            not isinstance(character, str)
            or len(character) != 1
            or unicodedata.normalize("NFC", character) != character
        ):
            issues.append(f"{record_id}: invalid normalized kanji character")
        else:
            codepoint = ord(character)
            if record.get("codepoint") != f"U+{codepoint:04X}":
                issues.append(f"{record_id}: kanji codepoint mismatch")
            expected_prefix = (
                "kanji.joyo."
                if expected_dataset_id == "joyo_kanji"
                else "kanji.grade-allocation."
            )
            if record_id != f"{expected_prefix}u{codepoint:x}":
                issues.append(f"{record_id}: kanji id/codepoint mismatch")
    if expected_dataset_id == "joyo_kanji":
        readings = record.get("readings")
        if (
            not isinstance(readings, list)
            or not readings
            or any(
                not isinstance(row, dict)
                or row.get("reading_type_ja") not in {"音読み", "訓読み"}
                or not isinstance(row.get("reading"), str)
                or not row["reading"].strip()
                for row in readings
            )
        ):
            issues.append(f"{record_id}: invalid joyo kanji readings")
    if expected_dataset_id == "grade_level_kanji_allocation":
        grade = record.get("school_grade")
        if (
            grade not in {1, 2, 3, 4, 5, 6}
            or record.get("school_grade_ja") != f"第{grade}学年"
            or record.get("allocation_scope") != "character_only"
        ):
            issues.append(f"{record_id}: invalid grade-level kanji allocation")
    return issues


def _host_allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def validate_against_schema(payload: dict[str, Any], label: str) -> list[str]:
    """共有JSON Schemaを実際に適用し、読みやすいエラーへ変換する。"""

    schema = load("schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    return [
        f"{label} schema {'.'.join(map(str, error.path)) or '$'}: {error.message}"
        for error in errors
    ]


def _human_facing_strings(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            found.extend(_human_facing_strings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_human_facing_strings(child))
    elif isinstance(value, str) and re.search(r"[ぁ-んァ-ヶ一-龯]", value):
        found.append(value)
    return found


def _unsafe_index_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not value or "\\" in value or path.is_absolute() or ".." in path.parts


def _resolve_bounded_path(
    root: Path,
    value: object,
    *,
    single_name: bool = False,
    suffix: str | None = None,
) -> Path:
    raw = str(value)
    relative = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or relative.is_absolute()
        or ".." in relative.parts
        or (single_name and (len(relative.parts) != 1 or raw != relative.name))
        or (suffix is not None and not relative.name.endswith(suffix))
    ):
        raise ValueError(raw)
    lexical_root = root.absolute()
    if lexical_root.is_symlink():
        raise ValueError(raw)
    cursor = lexical_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(raw)
    resolved_root = lexical_root.resolve()
    candidate = cursor.resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(raw)
    return candidate


def _forbidden_content_paths(value: object, path: str = "$") -> list[str]:
    """本文・歌詞等を示す禁止キーを入れ子も含めて列挙する。"""

    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in MIRRORED_CONTENT_KEYS:
                found.append(child_path)
            found.extend(_forbidden_content_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_content_paths(child, f"{path}[{index}]"))
    return found


def validate() -> list[str]:
    issues: list[str] = []
    for json_path in REFERENCE_DIR.rglob("*.json"):
        if json_path.is_symlink():
            issues.append(
                f"JSON symlink is forbidden: {json_path.relative_to(REFERENCE_DIR)}"
            )
        elif json_path.is_file():
            issues.extend(_duplicate_json_key_issues(json_path))
    sources_payload = load("sources.json")
    japanese = load("japanese_language_education.json")
    knowledge_payloads = {
        name: load(name)
        for name in (
            "japanese_grammar.json",
            "historical_kana_and_scripts.json",
            "makurakotoba.json",
            "japanese_poetry_forms.json",
        )
    }
    world = load("world_poetry.json")
    index_payload = load("index.json")
    for label, payload in (
        ("sources.json", sources_payload),
        ("japanese_language_education.json", japanese),
        *((name, payload) for name, payload in knowledge_payloads.items()),
        ("world_poetry.json", world),
        ("index.json", index_payload),
    ):
        issues.extend(validate_against_schema(payload, label))

    organizations = {row["id"]: row for row in sources_payload["organizations"]}
    sources = {row["id"]: row for row in sources_payload["sources"]}
    if len(organizations) != len(sources_payload["organizations"]):
        issues.append("duplicate organization id")
    if len(sources) != len(sources_payload["sources"]):
        issues.append("duplicate source id")

    all_records = (
        sources_payload["sources"]
        + japanese["resources"]
        + [
            record
            for payload in knowledge_payloads.values()
            for record in payload["entries"]
        ]
        + world["entries"]
    )
    seen: set[str] = set()
    for record in all_records:
        record_id = str(record.get("id", ""))
        if not ID_RE.fullmatch(record_id):
            issues.append(f"invalid id: {record_id!r}")
        if record_id in seen:
            issues.append(f"duplicate record id: {record_id}")
        seen.add(record_id)
        for required in ("kind", "title_ja", "aliases", "search_terms", "tags", "last_verified_at"):
            if required not in record:
                issues.append(f"{record_id}: missing {required}")

    for source in sources_payload["sources"]:
        source_id = source["id"]
        organization = organizations.get(source.get("organization_id"))
        if organization is None:
            issues.append(f"{source_id}: unknown organization")
            continue
        if not _host_allowed(source["canonical_url"], organization["official_domains"]):
            issues.append(f"{source_id}: canonical URL is outside registered official domains")
        rights = source.get("rights", {})
        for required in ("status", "terms_url", "attribution", "stored_content_scope", "checked_at"):
            if required not in rights:
                issues.append(f"{source_id}: missing rights.{required}")
        if (
            "institutional_repository" in source.get("tags", [])
            and not source.get("publication_role")
        ):
            issues.append(f"{source_id}: institutional repository source lacks publication_role")
        if source.get("publication_role") == "repository_host_only":
            issues.append(f"{source_id}: repository-host-only source must not be collected")
        if (
            source.get("publication_role")
            == "public_institution_content_on_official_platform"
            and not source.get("creators")
        ):
            issues.append(f"{source_id}: platform-hosted public content lacks creators")

    def check_source_refs(
        record: dict[str, Any],
        *,
        japanese_record: bool,
        factual_summary_required: bool = False,
    ) -> None:
        refs = record.get("source_refs")
        if not isinstance(refs, list) or not refs:
            issues.append(f"{record['id']}: source_refs must be non-empty")
            return
        for ref in refs:
            source = sources.get(ref.get("source_id"))
            if source is None:
                issues.append(f"{record['id']}: unknown source {ref.get('source_id')}")
                continue
            if not ref.get("supports"):
                issues.append(f"{record['id']}: source ref lacks supports")
            if not ref.get("locator"):
                issues.append(f"{record['id']}: source ref lacks locator")
            if japanese_record:
                organization = organizations[source["organization_id"]]
                if organization["type"] not in JP_ALLOWED_ORGANIZATION_TYPES:
                    issues.append(
                        f"{record['id']}: non-official Japanese source organization "
                        f"{organization['id']}"
                    )
                if source.get("publication_role") == "repository_host_only":
                    issues.append(
                        f"{record['id']}: repository-hosted third-party content is not an official knowledge source"
                    )
            if (
                factual_summary_required
                and source.get("publication_role")
                != "bibliographic_or_authority_record"
                and source.get("rights", {}).get("stored_content_scope")
                not in {
                    "metadata_links_and_factual_summary",
                    "official_data_and_normalized_extract",
                    "licensed_dataset_and_normalized_copy",
                }
            ):
                issues.append(
                    f"{record['id']}: source {source['id']} does not permit the recorded factual-summary scope"
                )

    if japanese.get("source_policy") != "official_and_public_institutions_only":
        issues.append("Japanese source policy is not fail-closed")
    for record in japanese["resources"]:
        if record.get("acquisition_mode") not in ACQUISITION_MODES:
            issues.append(f"{record['id']}: invalid acquisition_mode")
        if record.get("policy_role") != "advisory_only":
            issues.append(f"{record['id']}: policy_role must be advisory_only")
        if record.get("external_runtime_fetch_allowed") is not False:
            issues.append(f"{record['id']}: external runtime fetch must be disabled")
        if (
            record.get("acquisition_mode") == "manual_research_only"
            and record.get("local_runtime_allowed") is not False
        ):
            issues.append(f"{record['id']}: manual research resource cannot be a runtime input")
        check_source_refs(record, japanese_record=True)

    for label, payload in knowledge_payloads.items():
        if payload.get("source_policy") != "official_and_public_institutions_only":
            issues.append(f"{label}: source policy is not fail-closed")
        for record in payload["entries"]:
            check_source_refs(
                record,
                japanese_record=True,
                factual_summary_required=True,
            )
            for path in _forbidden_content_paths(record):
                issues.append(f"{record['id']}: mirrored content key is forbidden at {path}")
            if not record.get("rules"):
                issues.append(f"{record['id']}: knowledge record has no rules")
            machine_use = record.get("machine_use", {})
            if machine_use.get("candidate_only") and not machine_use.get("human_review_when"):
                issues.append(f"{record['id']}: candidate-only record lacks review condition")
            if record.get("structured_data", {}).get("source_scope") == "curriculum_statement":
                if record.get("definition_status") != "editorial_synthesis":
                    issues.append(
                        f"{record['id']}: curriculum-based explanation must be marked editorial_synthesis"
                    )
                if any(
                    rule.get("claim_status") != "editorial_guardrail"
                    for rule in record.get("rules", [])
                ):
                    issues.append(
                        f"{record['id']}: curriculum-based guardrails must be marked editorial_guardrail"
                    )
        for value in _human_facing_strings(payload):
            for suspect in SUSPECT_JAPANESE:
                if suspect in value:
                    issues.append(f"{label}: suspect Japanese {suspect!r} in {value!r}")

    world_ids = {row["id"] for row in world["entries"]}
    for record in world["entries"]:
        check_source_refs(
            record,
            japanese_record=False,
            factual_summary_required=True,
        )
        for related_id in record.get("related_ids", []):
            if related_id not in world_ids:
                issues.append(f"{record['id']}: unknown related id {related_id}")
        for relation in record.get("relations", []):
            target_id = relation.get("target_id")
            if target_id not in world_ids:
                issues.append(f"{record['id']}: unknown relation target {target_id}")
        if record.get("classification_status") not in {"source_stated", "editorial_synthesis"}:
            issues.append(f"{record['id']}: invalid classification_status")
        for path in _forbidden_content_paths(record):
            issues.append(f"{record['id']}: mirrored content key is forbidden at {path}")
        constraints = set(record.get("classification", {}).get("formal_constraints", []))
        profiles = record.get("structure", {}).get("constraint_profiles")
        if {"fixed", "open"} <= constraints and (
            not isinstance(profiles, list) or len(profiles) < 2
        ):
            issues.append(
                f"{record['id']}: fixed/open scope requires constraint_profiles"
            )
        if isinstance(profiles, list):
            profile_ids: list[str] = []
            profile_constraints: set[str] = set()
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                profile_ids.append(str(profile.get("id", "")))
                values = profile.get("formal_constraints", [])
                if isinstance(values, list):
                    scoped = {str(value) for value in values}
                    profile_constraints.update(scoped)
                    if {"fixed", "open"} <= scoped:
                        issues.append(
                            f"{record['id']}: one constraint profile cannot be both fixed and open"
                        )
            if len(profile_ids) != len(set(profile_ids)):
                issues.append(
                    f"{record['id']}: duplicate constraint profile id"
                )
            if profile_constraints != constraints:
                issues.append(
                    f"{record['id']}: profile constraints do not match classification"
                )

    for value in _human_facing_strings(world):
        for suspect in SUSPECT_JAPANESE:
            if suspect in value:
                issues.append(f"world_poetry.json: suspect Japanese {suspect!r} in {value!r}")

    for dataset in index_payload.get("datasets", []):
        if _unsafe_index_path(str(dataset.get("path", ""))):
            issues.append(f"index dataset path is unsafe: {dataset.get('path')!r}")
    for entry in index_payload.get("entries", {}).values():
        if _unsafe_index_path(str(entry.get("dataset_path", ""))):
            issues.append(f"{entry.get('id')}: index dataset path is unsafe")

    manifest_path = REFERENCE_DIR / "data" / "manifest.json"
    normalized_index_path = REFERENCE_DIR / "data" / "normalized" / "index.json"
    if not manifest_path.is_file() or not normalized_index_path.is_file():
        issues.append("normalized data manifest or index is missing")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        normalized_index = json.loads(normalized_index_path.read_text(encoding="utf-8"))
        if manifest.get("source_policy") != "official_and_public_institutions_only":
            issues.append("data manifest source policy is not fail-closed")
        if manifest.get("runtime_network_access") is not False:
            issues.append("data manifest must disable runtime network access")
        raw_root = REFERENCE_DIR / "data" / "raw"
        allowed_raw_paths: set[Path] = set()
        for row in manifest.get("raw_datasets", []):
            if row.get("source_id") not in sources:
                issues.append(f"unknown raw data source: {row.get('source_id')}")
            try:
                path = _resolve_bounded_path(raw_root, row.get("path", ""))
            except ValueError:
                issues.append(f"unsafe raw data path: {row.get('path')!r}")
                continue
            allowed_raw_paths.add(path)
            if not path.is_file():
                issues.append(f"missing raw data: {row.get('path')}")
                continue
            raw = path.read_bytes()
            if len(raw) != row.get("bytes"):
                issues.append(f"raw data size mismatch: {row.get('path')}")
            if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
                issues.append(f"raw data hash mismatch: {row.get('path')}")
            if license_value := row.get("license_path"):
                try:
                    license_path = _resolve_bounded_path(raw_root, license_value)
                except ValueError:
                    issues.append(f"unsafe raw data license path: {license_value!r}")
                    continue
                allowed_raw_paths.add(license_path)
                if not license_path.is_file():
                    issues.append(f"missing raw data license: {license_value}")
                elif hashlib.sha256(license_path.read_bytes()).hexdigest() != row.get(
                    "license_sha256"
                ):
                    issues.append(f"raw data license hash mismatch: {license_value}")
        actual_raw_paths: set[Path] = set()
        for candidate in raw_root.rglob("*"):
            if candidate.is_symlink():
                issues.append(
                    f"raw data symlink is forbidden: {candidate.relative_to(raw_root)}"
                )
            elif candidate.is_file():
                actual_raw_paths.add(candidate.resolve())
        if actual_raw_paths != allowed_raw_paths:
            for path in sorted(actual_raw_paths - allowed_raw_paths):
                issues.append(
                    f"unlisted raw data file: {path.relative_to(raw_root.resolve())}"
                )
            for path in sorted(allowed_raw_paths - actual_raw_paths):
                issues.append(f"listed raw data file is missing: {path}")
        editorial_root = REFERENCE_DIR / "data" / "editorial"
        allowed_editorial_paths: set[Path] = set()
        for row in manifest.get("editorial_inputs", []):
            if row.get("source_id") not in sources:
                issues.append(
                    f"unknown editorial input source: {row.get('source_id')}"
                )
            try:
                path = _resolve_bounded_path(
                    editorial_root,
                    row.get("path", ""),
                    single_name=True,
                    suffix=".json",
                )
            except ValueError:
                issues.append(f"unsafe editorial input path: {row.get('path')!r}")
                continue
            allowed_editorial_paths.add(path)
            if not path.is_file():
                issues.append(f"missing editorial input: {row.get('path')}")
                continue
            raw = path.read_bytes()
            if len(raw) != row.get("bytes"):
                issues.append(
                    f"editorial input size mismatch: {row.get('path')}"
                )
            if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
                issues.append(
                    f"editorial input hash mismatch: {row.get('path')}"
                )
        actual_editorial_paths: set[Path] = set()
        for candidate in editorial_root.rglob("*"):
            if candidate.is_symlink():
                issues.append(
                    "editorial input symlink is forbidden: "
                    f"{candidate.relative_to(editorial_root)}"
                )
            elif candidate.is_file():
                actual_editorial_paths.add(candidate.resolve())
        if actual_editorial_paths != allowed_editorial_paths:
            for path in sorted(actual_editorial_paths - allowed_editorial_paths):
                issues.append(
                    "unlisted editorial input file: "
                    f"{path.relative_to(editorial_root.resolve())}"
                )
            for path in sorted(allowed_editorial_paths - actual_editorial_paths):
                issues.append(f"listed editorial input file is missing: {path}")
        indexed_datasets = {
            row["id"]: row for row in normalized_index.get("datasets", [])
        }
        manifest_datasets = {
            row["id"]: row for row in manifest.get("normalized_datasets", [])
        }
        if indexed_datasets.keys() != manifest_datasets.keys():
            issues.append("normalized manifest/index dataset id set mismatch")
        if normalized_index.get("schema_version") != 2:
            issues.append("normalized index schema version must be 2")
        for label, rows in (
            ("normalized index", normalized_index.get("datasets", [])),
            ("normalized manifest", manifest.get("normalized_datasets", [])),
        ):
            row_ids = [str(row.get("id", "")) for row in rows]
            if len(row_ids) != len(set(row_ids)):
                issues.append(f"duplicate dataset id in {label}")
            for dataset_id in row_ids:
                if (
                    not ID_RE.fullmatch(dataset_id)
                    or dataset_id not in NORMALIZED_DATASET_KINDS
                ):
                    issues.append(f"invalid dataset id in {label}: {dataset_id!r}")
        normalized_seen_ids: set[str] = set()
        normalized_root = REFERENCE_DIR / "data" / "normalized"
        allowed_normalized_paths = {(normalized_root / "index.json").resolve()}
        for row in manifest.get("normalized_datasets", []):
            indexed = indexed_datasets.get(row.get("id"))
            if indexed != row:
                issues.append(f"normalized manifest/index mismatch: {row.get('id')}")
                continue
            raw_data_path = str(row.get("path", ""))
            relative = PurePosixPath(raw_data_path)
            if (
                not raw_data_path
                or "\\" in raw_data_path
                or relative.is_absolute()
                or ".." in relative.parts
                or len(relative.parts) != 1
                or relative.suffix != ".jsonl"
                or relative.name.endswith(".index.jsonl")
            ):
                issues.append(f"unsafe normalized data path: {raw_data_path!r}")
                continue
            try:
                path = _resolve_bounded_path(
                    normalized_root,
                    raw_data_path,
                    single_name=True,
                    suffix=".jsonl",
                )
            except ValueError:
                issues.append(f"unsafe normalized data path: {raw_data_path!r}")
                continue
            allowed_normalized_paths.add(path)
            if not path.is_file():
                issues.append(f"missing normalized data: {relative}")
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
                issues.append(f"normalized data hash mismatch: {relative}")
            if raw.count(b"\n") != row.get("record_count"):
                issues.append(f"normalized record count mismatch: {relative}")

            raw_index_path = str(row.get("index_path", ""))
            index_relative = PurePosixPath(raw_index_path)
            if (
                not raw_index_path
                or "\\" in raw_index_path
                or index_relative.is_absolute()
                or ".." in index_relative.parts
                or len(index_relative.parts) != 1
                or not index_relative.name.endswith(".index.jsonl")
            ):
                issues.append(f"unsafe normalized index path: {raw_index_path!r}")
                continue
            try:
                index_path = _resolve_bounded_path(
                    normalized_root,
                    raw_index_path,
                    single_name=True,
                    suffix=".index.jsonl",
                )
            except ValueError:
                issues.append(f"unsafe normalized index path: {raw_index_path!r}")
                continue
            allowed_normalized_paths.add(index_path)
            if not index_path.is_file():
                issues.append(f"missing normalized index: {index_relative}")
                continue
            index_raw = index_path.read_bytes()
            if hashlib.sha256(index_raw).hexdigest() != row.get("index_sha256"):
                issues.append(f"normalized index hash mismatch: {index_relative}")
            if index_raw.count(b"\n") != row.get("record_count"):
                issues.append(f"normalized index count mismatch: {index_relative}")
            with path.open("rb") as data_handle:
                for line_number, line in enumerate(index_raw.splitlines(), 1):
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        issues.append(
                            f"invalid normalized index JSON: {index_relative}:{line_number}: {error}"
                        )
                        continue
                    if not isinstance(entry, dict):
                        issues.extend(
                            _normalized_index_entry_issues(
                                entry,
                                expected_dataset_id=str(row.get("id", "")),
                            )
                        )
                        continue
                    entry_id = str(entry.get("id", ""))
                    issues.extend(
                        _normalized_index_entry_issues(
                            entry,
                            expected_dataset_id=str(row.get("id", "")),
                        )
                    )
                    if entry_id in normalized_seen_ids:
                        issues.append(f"duplicate normalized id: {entry_id}")
                    normalized_seen_ids.add(entry_id)
                    if (
                        entry.get("dataset_path") != row.get("path")
                    ):
                        issues.append(f"normalized entry dataset mismatch: {entry_id}")
                    index_terms = entry.get("search_terms")
                    offset = entry.get("byte_offset")
                    length = entry.get("byte_length")
                    if (
                        not isinstance(offset, int)
                        or not isinstance(length, int)
                        or offset < 0
                        or length < 2
                        or offset + length > len(raw)
                    ):
                        issues.append(f"invalid normalized locator: {entry_id}")
                        continue
                    data_handle.seek(offset)
                    try:
                        record = json.loads(data_handle.read(length))
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        issues.append(f"invalid normalized record at {entry_id}: {error}")
                        continue
                    if not isinstance(record, dict):
                        issues.extend(
                            _normalized_record_issues(
                                record,
                                expected_dataset_id=str(row.get("id", "")),
                            )
                        )
                        continue
                    issues.extend(
                        _normalized_record_issues(
                            record,
                            expected_dataset_id=str(row.get("id", "")),
                        )
                    )
                    if record.get("id") != entry_id:
                        issues.append(f"normalized locator id mismatch: {entry_id}")
                    if record.get("kind") != entry.get("kind"):
                        issues.append(f"normalized index kind mismatch: {entry_id}")
                    if record.get("title_ja") != entry.get("title_ja"):
                        issues.append(f"normalized index title mismatch: {entry_id}")
                    if record.get("source_id") not in sources:
                        issues.append(
                            f"{entry_id}: unknown normalized source {record.get('source_id')}"
                        )
                    record_terms = record.get("search_terms")
                    valid_record_terms = (
                        record_terms if isinstance(record_terms, list) else []
                    )
                    expected_terms = sorted(
                        {
                            value
                            for value in [record.get("title_ja"), *valid_record_terms]
                            if isinstance(value, str) and value
                        }
                    )
                    if index_terms != expected_terms:
                        issues.append(f"normalized index terms mismatch: {entry_id}")
                    serialized = json.dumps(record, ensure_ascii=False)
                    if any(marker in serialized for marker in ("〓", "<s>", "</s>", "</s/>")):
                        issues.append(f"raw annotation marker in normalized record: {entry_id}")
        joyo_path = normalized_root / "joyo_kanji.jsonl"
        grade_path = normalized_root / "grade_level_kanji_allocation.jsonl"
        if joyo_path.is_file() and grade_path.is_file():
            joyo_records = [
                json.loads(line)
                for line in joyo_path.read_text(encoding="utf-8").splitlines()
            ]
            grade_records = [
                json.loads(line)
                for line in grade_path.read_text(encoding="utf-8").splitlines()
            ]
            joyo_characters = {
                str(record.get("character", "")) for record in joyo_records
            }
            reading_counts = {"音読み": 0, "訓読み": 0}
            for record in joyo_records:
                for reading in record.get("readings", []):
                    reading_type = reading.get("reading_type_ja")
                    if reading_type in reading_counts:
                        reading_counts[reading_type] += 1
            if len(joyo_records) != 2_136 or len(joyo_characters) != 2_136:
                issues.append("常用漢字表の字種数は2,136字でなければならない")
            if reading_counts != {"音読み": 2_352, "訓読み": 2_036}:
                issues.append(
                    f"常用漢字表の音訓件数が不正: {reading_counts}"
                )
            grade_counts = {
                grade: sum(
                    record.get("school_grade") == grade for record in grade_records
                )
                for grade in range(1, 7)
            }
            expected_grade_counts = {
                1: 80,
                2: 160,
                3: 200,
                4: 202,
                5: 193,
                6: 191,
            }
            grade_characters = {
                str(record.get("character", "")) for record in grade_records
            }
            if (
                len(grade_records) != 1_026
                or len(grade_characters) != 1_026
                or grade_counts != expected_grade_counts
            ):
                issues.append(
                    "学年別漢字配当表の件数が不正: "
                    f"total={len(grade_records)}, grades={grade_counts}"
                )
            if not grade_characters <= joyo_characters:
                issues.append("学年別漢字配当表に常用漢字表外の字種がある")
        expected_normalized_count = sum(
            int(row.get("record_count", 0))
            for row in manifest.get("normalized_datasets", [])
        )
        if len(normalized_seen_ids) != expected_normalized_count:
            issues.append(
                "normalized index coverage mismatch: "
                f"{len(normalized_seen_ids)} != {expected_normalized_count}"
            )
        actual_normalized_paths: set[Path] = set()
        for candidate in normalized_root.rglob("*"):
            if candidate.is_symlink():
                issues.append(
                    "normalized data symlink is forbidden: "
                    f"{candidate.relative_to(normalized_root)}"
                )
            elif candidate.is_file():
                actual_normalized_paths.add(candidate.resolve())
        if actual_normalized_paths != allowed_normalized_paths:
            for path in sorted(actual_normalized_paths - allowed_normalized_paths):
                issues.append(
                    "unlisted normalized data file: "
                    f"{path.relative_to(normalized_root.resolve())}"
                )
            for path in sorted(allowed_normalized_paths - actual_normalized_paths):
                issues.append(f"listed normalized data file is missing: {path}")

    if not any(row.get("id") == "excluded.nichibun.personal-waka-db" for row in japanese.get("excluded", [])):
        issues.append("personal-origin waka database exclusion is not recorded")

    spec = importlib.util.spec_from_file_location(
        "build_reference_index", ROOT / "scripts" / "build_reference_index.py"
    )
    if spec is None or spec.loader is None:
        issues.append("cannot load index builder")
    else:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        expected = module.serialized_index()
        actual = (REFERENCE_DIR / "index.json").read_text(encoding="utf-8")
        if actual != expected:
            issues.append("index.json is stale")
    return issues


def main() -> int:
    issues = validate()
    if issues:
        print(f"{len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("reference catalog is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
