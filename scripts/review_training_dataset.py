#!/usr/bin/env python3
"""未確認候補をレビューし、承認済みデータをgroup単位で分割する。

評価キーやruntimeログは候補にすぎない。このスクリプトで人が明示的に
approved / edited とした候補だけを approved/ と splits/ へ出す。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


ANNOTATION_SCHEMA_VERSION = "dogido-training-review-v1"
APPROVED_SCHEMA_VERSION = "dogido-training-approved-v1"
VALID_STATUSES = {"approved", "edited", "rejected"}


def _make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: JSON object required")
        rows.append(value)
    return rows


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


def load_candidates(root: Path) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "candidates").glob("*.jsonl")):
        for row in _read_jsonl(path):
            candidate_id = str(row.get("candidate_id") or "").strip()
            if not candidate_id:
                raise ValueError(f"{path}: candidate_id is required")
            if candidate_id in candidates:
                raise ValueError(f"duplicate candidate_id: {candidate_id}")
            candidates[candidate_id] = row
    return candidates


def latest_annotations(root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(root / "reviews" / "annotations.jsonl"):
        candidate_id = str(row.get("candidate_id") or "").strip()
        status = str(row.get("status") or "").strip()
        if not candidate_id or status not in VALID_STATUSES:
            # 旧形式や作業メモは消さず、昇格判断からだけ除外する。
            continue
        latest[candidate_id] = row
    return latest


def append_annotation(
    root: Path,
    *,
    candidate_id: str,
    status: str,
    corrected_output: object | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    candidates = load_candidates(root)
    if candidate_id not in candidates:
        raise ValueError(f"unknown candidate_id: {candidate_id}")
    if status not in VALID_STATUSES:
        raise ValueError(f"unsupported status: {status}")
    if status == "edited" and corrected_output is None:
        raise ValueError("edited status requires corrected_output")
    if status == "edited" and not isinstance(corrected_output, dict):
        raise ValueError("edited corrected_output must be a JSON object")
    if status != "edited" and corrected_output is not None:
        raise ValueError("corrected_output is allowed only for edited status")

    row = {
        "schema_version": ANNOTATION_SCHEMA_VERSION,
        "review_id": f"review_{uuid4().hex}",
        "candidate_id": candidate_id,
        "status": status,
        "corrected_output": corrected_output,
        "tags": sorted(set(tags or [])),
        "notes": (notes or "").strip() or None,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    path = root / "reviews" / "annotations.jsonl"
    _make_private_dir(root)
    _make_private_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return row


def _split_for_group(group_id: str) -> str:
    """同じ会話・句を跨がせない、安定した80/10/10分割。"""

    bucket = int(sha256(group_id.encode("utf-8")).hexdigest()[:8], 16) % 10
    if bucket == 0:
        return "validation"
    if bucket == 1:
        return "test"
    return "train"


def promote_approved(root: Path) -> dict[str, Any]:
    candidates = load_candidates(root)
    annotations = latest_annotations(root)
    approved_rows: list[dict[str, Any]] = []
    rejected = 0
    orphan_annotations = 0
    for candidate_id in annotations:
        if candidate_id not in candidates:
            orphan_annotations += 1

    for candidate_id, candidate in sorted(candidates.items()):
        review = annotations.get(candidate_id)
        if review is None:
            continue
        status = str(review["status"])
        if status == "rejected":
            rejected += 1
            continue
        approved_output = (
            review.get("corrected_output")
            if status == "edited"
            else candidate.get("candidate_output")
        )
        if approved_output is None:
            raise ValueError(f"candidate has no approved output: {candidate_id}")
        group_id = str(candidate.get("group_id") or candidate_id)
        approved_rows.append(
            {
                "schema_version": APPROVED_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "task": candidate.get("task"),
                "group_id": group_id,
                "input": candidate.get("input"),
                "approved_output": approved_output,
                "rejected_output": candidate.get("rejected_output"),
                "review": {
                    "status": status,
                    "review_id": review.get("review_id"),
                    "reviewed_at": review.get("reviewed_at"),
                    "tags": review.get("tags") or [],
                    "notes": review.get("notes"),
                },
                "source": candidate.get("source"),
            }
        )

    _write_jsonl(root / "approved" / "all.jsonl", approved_rows)
    split_rows = {"train": [], "validation": [], "test": []}
    for row in approved_rows:
        group_id = str(row.get("group_id") or row["candidate_id"])
        split_rows[_split_for_group(group_id)].append(row)
    split_counts = {
        split: _write_jsonl(root / "splits" / f"{split}.jsonl", rows)
        for split, rows in split_rows.items()
    }
    summary = {
        "schema_version": APPROVED_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(candidates),
        "reviewed_count": len(annotations),
        "approved_count": len(approved_rows),
        "rejected_count": rejected,
        "orphan_annotation_count": orphan_annotations,
        "status_counts": dict(
            sorted(Counter(str(row.get("status")) for row in annotations.values()).items())
        ),
        "split_counts": split_counts,
        "notes": [
            "only human-approved or human-edited candidates are included",
            "all rows sharing group_id are assigned to the same split",
        ],
    }
    manifest = root / "approved" / "manifest.json"
    _make_private_dir(manifest.parent)
    manifest.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(manifest, 0o600)
    except OSError:
        pass
    return summary


def _parse_json_argument(raw: str | None) -> object | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--corrected-output-json is invalid: {exc.msg}") from exc


def _list_rows(root: Path, *, task: str | None, status: str, limit: int) -> list[dict[str, Any]]:
    candidates = load_candidates(root)
    reviews = latest_annotations(root)
    rows: list[dict[str, Any]] = []
    for candidate_id, candidate in sorted(candidates.items()):
        candidate_status = str(reviews.get(candidate_id, {}).get("status") or "unreviewed")
        if task and candidate.get("task") != task:
            continue
        if status != "all" and candidate_status != status:
            continue
        rows.append(
            {
                "candidate_id": candidate_id,
                "task": candidate.get("task"),
                "status": candidate_status,
                "candidate_output": candidate.get("candidate_output"),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".dogido_training"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="候補と現在のレビュー状態を表示")
    list_parser.add_argument("--task")
    list_parser.add_argument(
        "--status",
        choices=["all", "unreviewed", *sorted(VALID_STATUSES)],
        default="unreviewed",
    )
    list_parser.add_argument("--limit", type=int, default=20)

    show_parser = subparsers.add_parser("show", help="候補1件をJSON表示")
    show_parser.add_argument("candidate_id")

    mark_parser = subparsers.add_parser("mark", help="人間レビューを追記")
    mark_parser.add_argument("candidate_id")
    mark_parser.add_argument("status", choices=sorted(VALID_STATUSES))
    mark_parser.add_argument("--corrected-output-json")
    mark_parser.add_argument("--notes")
    mark_parser.add_argument("--tag", action="append", default=[])

    subparsers.add_parser("promote", help="承認済みだけをapproved/splitsへ出力")
    args = parser.parse_args()

    if args.command == "list":
        result: object = _list_rows(
            args.root,
            task=args.task,
            status=args.status,
            limit=max(1, args.limit),
        )
    elif args.command == "show":
        candidates = load_candidates(args.root)
        if args.candidate_id not in candidates:
            parser.error(f"unknown candidate_id: {args.candidate_id}")
        result = {
            "candidate": candidates[args.candidate_id],
            "review": latest_annotations(args.root).get(args.candidate_id),
        }
    elif args.command == "mark":
        try:
            result = append_annotation(
                args.root,
                candidate_id=args.candidate_id,
                status=args.status,
                corrected_output=_parse_json_argument(args.corrected_output_json),
                notes=args.notes,
                tags=args.tag,
            )
        except ValueError as exc:
            parser.error(str(exc))
    else:
        result = promote_approved(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
