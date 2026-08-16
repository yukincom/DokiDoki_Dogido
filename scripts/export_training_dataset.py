#!/usr/bin/env python3
"""Dogido の私的ログを、人間確認前の学習候補へ正規化する。

生成物は既定で .dogido_training/ に置く。このディレクトリは Git 対象外。
候補を学習正解とはみなさず、reviews/annotations.jsonl の人間確認を必須にする。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "dogido-training-candidate-v1"
NEGATIVE_CRITIQUE_KINDS = {"forced_compress", "off_context", "unreadable"}


def _make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not path.exists():
        return rows, errors
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                {
                    "file": path.name,
                    "line": line_number,
                    "error": f"invalid_json:{exc.msg}",
                }
            )
            continue
        if not isinstance(row, dict):
            errors.append(
                {"file": path.name, "line": line_number, "error": "not_an_object"}
            )
            continue
        rows.append(row)
    return rows, errors


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    materialized = list(rows)
    _make_private_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return len(materialized)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _make_private_dir(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _stable_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}_{sha256(encoded).hexdigest()[:16]}"


def _session_group(session_id: object) -> str:
    return _stable_id("session", str(session_id or "missing"))


def _text(row: dict[str, Any], key: str = "text") -> str:
    return str(row.get(key) or "").strip()


def _distribution(rows: Iterable[dict[str, Any]], path: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        value: object = row
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        counts[str(value or "unknown")] += 1
    return dict(sorted(counts.items()))


def build_conversation_candidates(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """同じ session/sequence に1入力・1返答だけある組を候補にする。"""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        session_id = str(row.get("session_id") or "").strip()
        sequence = row.get("sequence")
        if not session_id or sequence is None:
            continue
        grouped[(session_id, str(sequence))].append(row)

    candidates_by_id: dict[str, dict[str, Any]] = {}
    ambiguous = 0
    for (session_id, sequence), group_rows in grouped.items():
        player_rows = [row for row in group_rows if row.get("type") == "player_input" and _text(row)]
        speech_rows = [row for row in group_rows if row.get("type") == "dogido_speech" and _text(row)]
        if not player_rows and not speech_rows:
            continue
        if len(player_rows) != 1 or len(speech_rows) != 1:
            if player_rows and speech_rows:
                ambiguous += 1
            continue
        player = player_rows[0]
        speech = speech_rows[0]
        user_text = _text(player)
        assistant_text = _text(speech)
        context = {
            "biome": player.get("biome") or speech.get("biome"),
            "structure": player.get("structure") or speech.get("structure"),
        }
        candidate_id = _stable_id(
            "conv",
            user_text,
            assistant_text,
            context,
        )
        existing = candidates_by_id.get(candidate_id)
        if existing is not None:
            existing["signals"]["occurrences"] += 1
            continue
        candidates_by_id[candidate_id] = {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "task": "conversation_reply",
            "group_id": _session_group(session_id),
            "input": {
                "user_text": user_text,
                "context": context,
            },
            "candidate_output": {
                "assistant_text": assistant_text,
                "layer": speech.get("layer"),
            },
            "signals": {
                "occurrences": 1,
                "automatically_paired": True,
                "quality_confirmed": False,
            },
            "source": {
                "file": "short_term/current_session.jsonl",
                "record_group": f"{_session_group(session_id)}:{sequence}",
            },
            "review_requirements": [
                "reply_matches_user_intent",
                "natural_japanese",
                "dogido_voice",
                "no_hallucination",
                "privacy_check",
            ],
        }
    candidates = sorted(candidates_by_id.values(), key=lambda row: row["candidate_id"])
    return candidates, {
        "source_groups": len(grouped),
        "ambiguous_paired_groups_skipped": ambiguous,
        "deduplicated_candidates": len(candidates),
    }


def build_workshop_feedback_candidates(
    critiques: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in critiques:
        player_text = _text(row, "player_text")
        verse = _text(row, "surface_at_time")
        if not player_text:
            continue
        source_id = str(row.get("id") or _stable_id("critique_source", player_text, verse))
        candidates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": _stable_id("workshop", source_id),
                "task": "workshop_feedback_classification",
                "group_id": str(row.get("entry_id") or _stable_id("entry", verse)),
                "input": {
                    "current_verse": verse or None,
                    "player_text": player_text,
                    "materials_snapshot": row.get("materials_snapshot") or {},
                },
                "candidate_output": {
                    "critique_kind": str(row.get("kind") or "other"),
                },
                "signals": {
                    "persisted_runtime_outcome": True,
                    "quality_confirmed": False,
                },
                "source": {
                    "file": "long_term/haiku_critiques.jsonl",
                    "record_id": source_id,
                },
                "review_requirements": [
                    "label_matches_full_utterance",
                    "verse_matches_turn",
                    "stt_error_check",
                    "privacy_check",
                ],
            }
        )
    return sorted(candidates, key=lambda row: row["candidate_id"])


def _quality_hint(kinds: set[str], has_revision: bool) -> str:
    positive = "praise" in kinds
    negative = bool(kinds & NEGATIVE_CRITIQUE_KINDS)
    if positive and negative:
        return "mixed_feedback"
    if has_revision:
        return "revised_after_feedback"
    if positive:
        return "positive_feedback"
    if negative:
        return "negative_feedback"
    return "unrated"


def build_haiku_quality_candidates(
    entries: list[dict[str, Any]],
    critiques: list[dict[str, Any]],
    revisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    critique_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    revision_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in critiques:
        entry_id = str(row.get("entry_id") or "").strip()
        if entry_id:
            critique_by_entry[entry_id].append(row)
    for row in revisions:
        entry_id = str(row.get("haiku_id") or "").strip()
        if entry_id:
            revision_by_entry[entry_id].append(row)

    candidates: list[dict[str, Any]] = []
    for row in entries:
        entry_id = str(row.get("id") or "").strip()
        if not entry_id:
            continue
        entry_critiques = critique_by_entry.get(entry_id, [])
        entry_revisions = revision_by_entry.get(entry_id, [])
        critique_kinds = {str(item.get("kind") or "other") for item in entry_critiques}
        candidates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": _stable_id("haiku", entry_id),
                "task": "haiku_quality",
                "group_id": entry_id,
                "input": {
                    "world": row.get("world") or {},
                    "materials_snapshot": row.get("materials_snapshot") or {},
                    "interpretation": row.get("interpretation"),
                    "trigger": row.get("trigger") or {},
                },
                "candidate_output": {
                    "surface_text": row.get("surface_text") or row.get("text"),
                    "reading_text": row.get("reading_text") or row.get("text"),
                    "lines": row.get("lines"),
                },
                "signals": {
                    "quality_hint": _quality_hint(critique_kinds, bool(entry_revisions)),
                    "critique_kinds": sorted(critique_kinds),
                    "critique_count": len(entry_critiques),
                    "revision_count": len(entry_revisions),
                    "quality_confirmed": False,
                },
                "feedback": [
                    {
                        "record_id": item.get("id"),
                        "kind": item.get("kind"),
                        "player_text": item.get("player_text"),
                        "surface_at_time": item.get("surface_at_time"),
                    }
                    for item in entry_critiques
                ],
                "source": {
                    "file": "long_term/haiku_entries.jsonl",
                    "record_id": entry_id,
                },
                "review_requirements": [
                    "scene_fidelity",
                    "natural_japanese",
                    "meter_and_reading",
                    "originality",
                    "feedback_label",
                ],
            }
        )
    return sorted(candidates, key=lambda row: row["candidate_id"])


def build_revision_preference_candidates(
    revisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in revisions:
        base_text = _text(row, "base_text")
        revised_text = _text(row, "revised_text")
        if not base_text or not revised_text or base_text == revised_text:
            continue
        source_id = str(row.get("id") or _stable_id("revision_source", base_text, revised_text))
        source_kind = str(row.get("source") or "unknown")
        candidates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": _stable_id("revision", source_id),
                "task": "haiku_revision_preference",
                "group_id": str(row.get("haiku_id") or _stable_id("haiku", base_text)),
                "input": {
                    "base_text": base_text,
                    "base_surface_text": row.get("base_surface_text") or row.get("original_text"),
                    "comment": row.get("comment"),
                    "world": row.get("world") or {},
                },
                "candidate_output": {
                    "chosen_text": revised_text,
                    "chosen_surface_text": row.get("revised_surface_text") or revised_text,
                    "lines": row.get("lines"),
                    "edits": row.get("edits"),
                },
                "rejected_output": {
                    "text": base_text,
                },
                "signals": {
                    "source": source_kind,
                    "player_confirmed": source_kind
                    in {"formal", "conversational", "generated_confirmed", "player_line_confirmed"},
                    "quality_confirmed": False,
                },
                "source": {
                    "file": "long_term/haiku_revisions.jsonl",
                    "record_id": source_id,
                },
                "review_requirements": [
                    "preference_is_intentional",
                    "stt_error_check",
                    "other_lines_unchanged_when_required",
                    "natural_japanese",
                ],
            }
        )
    return sorted(candidates, key=lambda row: row["candidate_id"])


def build_human_evaluation_candidates(
    flags: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """↑/↓をtarget単位に畳み、最後に押した評価だけを候補へ反映する。"""

    latest_by_target: dict[str, tuple[tuple[float, int], dict[str, Any]]] = {}
    revisions_by_target: dict[str, int] = defaultdict(int)
    invalid = 0
    for row_index, row in enumerate(flags):
        target_id = str(row.get("target_id") or "").strip()
        group_id = str(row.get("group_id") or "").strip()
        label = str(row.get("label") or "").strip()
        target = row.get("target")
        if (
            not target_id
            or not group_id
            or label not in {"good_example", "needs_review"}
            or not isinstance(target, dict)
        ):
            invalid += 1
            continue
        if target_id in latest_by_target:
            revisions_by_target[target_id] += 1
        raw_at = row.get("pressed_at") or row.get("flagged_at")
        timestamp = float("-inf")
        if raw_at:
            try:
                parsed_at = datetime.fromisoformat(str(raw_at))
                if parsed_at.tzinfo is None:
                    parsed_at = parsed_at.replace(tzinfo=timezone.utc)
                timestamp = parsed_at.timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
        ordering = (timestamp, row_index)
        current = latest_by_target.get(target_id)
        if current is None or ordering > current[0]:
            latest_by_target[target_id] = (ordering, row)

    candidates: list[dict[str, Any]] = []
    for target_id, (_, row) in latest_by_target.items():
        label = str(row["label"])
        target = row["target"]
        candidates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": _stable_id("human_eval", target_id),
                "task": "response_quality_review",
                "group_id": str(row["group_id"]),
                "input": target,
                "candidate_output": {
                    "quality_label": label,
                },
                "signals": {
                    "human_key_flag": True,
                    "superseded_flag_count": revisions_by_target[target_id],
                    # キーは強いsignalだが、誤操作・個人情報・学習目的との適合を別途確認する。
                    "quality_confirmed": False,
                },
                "source": {
                    "file": "inbox/evaluation_flags.jsonl",
                    "record_id": row.get("flag_id"),
                    "target_id": target_id,
                },
                "review_requirements": [
                    "key_label_matches_intent",
                    "input_output_pair_complete",
                    "privacy_check",
                    "training_task_fit",
                ],
            }
        )
    return sorted(candidates, key=lambda candidate: candidate["candidate_id"]), {
        "source_flags": len(flags),
        "invalid_flags_skipped": invalid,
        "superseded_flags": sum(revisions_by_target.values()),
        "latest_targets": len(candidates),
    }


def export_dataset(memory_dir: Path, output_dir: Path) -> dict[str, Any]:
    short_rows, short_errors = _read_jsonl(memory_dir / "short_term" / "current_session.jsonl")
    entries, entry_errors = _read_jsonl(memory_dir / "long_term" / "haiku_entries.jsonl")
    critiques, critique_errors = _read_jsonl(memory_dir / "long_term" / "haiku_critiques.jsonl")
    revisions, revision_errors = _read_jsonl(memory_dir / "long_term" / "haiku_revisions.jsonl")
    feedback_flags, feedback_errors = _read_jsonl(output_dir / "inbox" / "evaluation_flags.jsonl")
    errors = (
        short_errors
        + entry_errors
        + critique_errors
        + revision_errors
        + feedback_errors
    )

    conversations, conversation_stats = build_conversation_candidates(short_rows)
    workshop = build_workshop_feedback_candidates(critiques)
    haiku_quality = build_haiku_quality_candidates(entries, critiques, revisions)
    revision_preferences = build_revision_preference_candidates(revisions)
    human_evaluations, human_evaluation_stats = build_human_evaluation_candidates(
        feedback_flags
    )

    candidate_dir = output_dir / "candidates"
    counts = {
        "conversation_reply": _write_jsonl(
            candidate_dir / "conversation_reply.jsonl", conversations
        ),
        "workshop_feedback_classification": _write_jsonl(
            candidate_dir / "workshop_feedback_classification.jsonl", workshop
        ),
        "haiku_quality": _write_jsonl(candidate_dir / "haiku_quality.jsonl", haiku_quality),
        "haiku_revision_preference": _write_jsonl(
            candidate_dir / "haiku_revision_preference.jsonl", revision_preferences
        ),
        "response_quality_review": _write_jsonl(
            candidate_dir / "response_quality_review.jsonl", human_evaluations
        ),
        # 現行 voice_input は一時wavを削除するため、音声教師データはまだ0件。
        "stt_transcription": _write_jsonl(candidate_dir / "stt_transcription.jsonl", []),
    }

    review_dir = output_dir / "reviews"
    _make_private_dir(output_dir)
    _make_private_dir(review_dir)
    annotations = review_dir / "annotations.jsonl"
    annotations.touch(exist_ok=True)
    try:
        os.chmod(annotations, 0o600)
    except OSError:
        pass
    _make_private_dir(output_dir / "approved")
    _make_private_dir(output_dir / "splits")
    _make_private_dir(output_dir / "audio")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private_output": True,
        "source": {
            "memory_directory": memory_dir.name,
            "rows": {
                "short_term": len(short_rows),
                "haiku_entries": len(entries),
                "haiku_critiques": len(critiques),
                "haiku_revisions": len(revisions),
                "evaluation_flags": len(feedback_flags),
            },
            "read_errors": errors,
        },
        "candidate_counts": counts,
        "candidate_distributions": {
            "workshop_critique_kind": _distribution(
                workshop,
                ("candidate_output", "critique_kind"),
            ),
            "haiku_quality_hint": _distribution(
                haiku_quality,
                ("signals", "quality_hint"),
            ),
            "revision_source": _distribution(
                revision_preferences,
                ("signals", "source"),
            ),
            "human_key_label": _distribution(
                human_evaluations,
                ("candidate_output", "quality_label"),
            ),
        },
        "conversation_extraction": conversation_stats,
        "human_evaluation_extraction": human_evaluation_stats,
        "readiness": {
            "conversation": "review_required",
            "workshop": "review_required",
            "haiku": "review_required",
            "human_key_flags": "review_required",
            "stt": "not_ready_missing_audio",
        },
        "notes": [
            "candidate files are not training-ready labels",
            "reviews/annotations.jsonl is never overwritten by export",
            "split by group_id only after human approval",
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    readme_path = output_dir / "README.txt"
    readme_path.write_text(
        "PRIVATE TRAINING DATA - Git対象外\n"
        "inbox と candidates は未確認です。学習投入前に docs/training-data-plan.md に従ってレビューしてください。\n"
        "操作: python scripts/review_training_dataset.py list / mark / promote\n",
        encoding="utf-8",
    )
    try:
        os.chmod(readme_path, 0o600)
    except OSError:
        pass
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", type=Path, default=Path(".dogido_memory"))
    parser.add_argument("--output-dir", type=Path, default=Path(".dogido_training"))
    args = parser.parse_args()
    manifest = export_dataset(args.memory_dir, args.output_dir)
    print(json.dumps(manifest["candidate_counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
