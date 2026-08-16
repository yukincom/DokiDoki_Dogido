from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.review_training_dataset import append_annotation, promote_approved


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class TrainingReviewTests(unittest.TestCase):
    def test_only_latest_human_approval_is_promoted_and_groups_do_not_leak(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = [
                {
                    "candidate_id": "candidate_good",
                    "task": "conversation_reply",
                    "group_id": "shared_session",
                    "input": {"user_text": "丸石やな"},
                    "candidate_output": {"assistant_text": "そうやな"},
                    "source": {"file": "test"},
                },
                {
                    "candidate_id": "candidate_edit",
                    "task": "conversation_reply",
                    "group_id": "shared_session",
                    "input": {"user_text": "どっち？"},
                    "candidate_output": {"assistant_text": "知らん"},
                    "source": {"file": "test"},
                },
                {
                    "candidate_id": "candidate_reject",
                    "task": "response_quality_review",
                    "group_id": "another_session",
                    "input": {"kind": "player_chat"},
                    "candidate_output": {"quality_label": "good_example"},
                    "source": {"file": "test"},
                },
            ]
            _write_jsonl(root / "candidates" / "sample.jsonl", candidates)

            append_annotation(
                root,
                candidate_id="candidate_good",
                status="approved",
                tags=["natural"],
            )
            append_annotation(
                root,
                candidate_id="candidate_edit",
                status="edited",
                corrected_output={"assistant_text": "こっちやと思うで"},
            )
            # 最後の判断を正にする。キー評価もレビューも追記式で履歴を消さない。
            append_annotation(root, candidate_id="candidate_reject", status="approved")
            append_annotation(root, candidate_id="candidate_reject", status="rejected")

            summary = promote_approved(root)

            self.assertEqual(summary["approved_count"], 2)
            self.assertEqual(summary["rejected_count"], 1)
            approved = [
                json.loads(line)
                for line in (root / "approved" / "all.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            by_id = {row["candidate_id"]: row for row in approved}
            self.assertEqual(
                by_id["candidate_edit"]["approved_output"],
                {"assistant_text": "こっちやと思うで"},
            )
            self.assertNotIn("candidate_reject", by_id)

            containing_splits: list[str] = []
            for split in ("train", "validation", "test"):
                split_path = root / "splits" / f"{split}.jsonl"
                rows = [
                    json.loads(line)
                    for line in split_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                ids = {row["candidate_id"] for row in rows}
                if ids & {"candidate_good", "candidate_edit"}:
                    containing_splits.append(split)
                    self.assertEqual(ids & {"candidate_good", "candidate_edit"}, {
                        "candidate_good",
                        "candidate_edit",
                    })
            self.assertEqual(len(containing_splits), 1)

    def test_edited_review_requires_corrected_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_jsonl(
                root / "candidates" / "sample.jsonl",
                [
                    {
                        "candidate_id": "candidate_1",
                        "task": "conversation_reply",
                        "group_id": "session_1",
                        "input": {},
                        "candidate_output": {"assistant_text": "元"},
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "corrected_output"):
                append_annotation(root, candidate_id="candidate_1", status="edited")


if __name__ == "__main__":
    unittest.main()
