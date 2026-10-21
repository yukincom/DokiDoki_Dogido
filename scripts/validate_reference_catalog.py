"""詩の基礎知識参照カタログ専用の構造・権利境界チェッカー。"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
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
    knowledge_payloads = {
        name: load(name)
        for name in (
            "makurakotoba.json",
            "japanese_poetry_forms.json",
        )
    }
    world = load("world_poetry.json")
    index_payload = load("index.json")
    for label, payload in (
        ("sources.json", sources_payload),
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
