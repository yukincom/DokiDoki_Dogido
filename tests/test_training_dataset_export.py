from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.export_training_dataset import export_dataset


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class TrainingDatasetExportTests(unittest.TestCase):
    def test_export_builds_private_review_candidates_without_overwriting_annotations(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            output = root / "training"
            _write_jsonl(
                memory / "short_term" / "current_session.jsonl",
                [
                    {
                        "type": "player_input",
                        "session_id": "ses_private_value",
                        "sequence": 1,
                        "text": "丸石を持ってる",
                        "biome": "meadow",
                    },
                    {
                        "type": "dogido_speech",
                        "session_id": "ses_private_value",
                        "sequence": 1,
                        "text": "丸石やな。何を作るん？",
                        "layer": "speech",
                        "biome": "meadow",
                    },
                    # 同じ内容は occurrences にまとめる。
                    {
                        "type": "player_input",
                        "session_id": "ses_second_private_value",
                        "sequence": 2,
                        "text": "丸石を持ってる",
                        "biome": "meadow",
                    },
                    {
                        "type": "dogido_speech",
                        "session_id": "ses_second_private_value",
                        "sequence": 2,
                        "text": "丸石やな。何を作るん？",
                        "layer": "speech",
                        "biome": "meadow",
                    },
                    # 複数返答がある組は対応が曖昧なので候補にしない。
                    {
                        "type": "player_input",
                        "session_id": "ses_private_value",
                        "sequence": 3,
                        "text": "どっち？",
                    },
                    {
                        "type": "dogido_speech",
                        "session_id": "ses_private_value",
                        "sequence": 3,
                        "text": "こっちや",
                    },
                    {
                        "type": "dogido_speech",
                        "session_id": "ses_private_value",
                        "sequence": 3,
                        "text": "あっちや",
                    },
                ],
            )
            _write_jsonl(
                memory / "long_term" / "haiku_entries.jsonl",
                [
                    {
                        "id": "hk_1",
                        "text": "はるのかぜ\nひつじがあるく\nよるのつき",
                        "world": {"biome": "meadow"},
                    }
                ],
            )
            _write_jsonl(
                memory / "long_term" / "haiku_critiques.jsonl",
                [
                    {
                        "id": "hcrit_1",
                        "entry_id": "hk_1",
                        "kind": "praise",
                        "player_text": "いい句だね",
                        "surface_at_time": "はるのかぜ\nひつじがあるく\nよるのつき",
                    }
                ],
            )
            _write_jsonl(
                memory / "long_term" / "haiku_revisions.jsonl",
                [
                    {
                        "id": "hrev_1",
                        "haiku_id": "hk_1",
                        "source": "player_line_confirmed",
                        "base_text": "はるのかぜ\nひつじがあるく\nよるのつき",
                        "revised_text": "はるのかぜ\nひつじがあるく\nつきあかり",
                    }
                ],
            )

            manifest = export_dataset(memory, output)

            self.assertEqual(manifest["candidate_counts"]["conversation_reply"], 1)
            self.assertEqual(manifest["candidate_counts"]["workshop_feedback_classification"], 1)
            self.assertEqual(manifest["candidate_counts"]["haiku_quality"], 1)
            self.assertEqual(manifest["candidate_counts"]["haiku_revision_preference"], 1)
            self.assertEqual(manifest["candidate_counts"]["response_quality_review"], 0)
            self.assertEqual(manifest["candidate_counts"]["stt_transcription"], 0)
            self.assertEqual(
                manifest["candidate_distributions"]["workshop_critique_kind"],
                {"praise": 1},
            )
            self.assertEqual(
                manifest["conversation_extraction"]["ambiguous_paired_groups_skipped"],
                1,
            )

            conversation_text = (output / "candidates" / "conversation_reply.jsonl").read_text(
                encoding="utf-8"
            )
            conversation = json.loads(conversation_text)
            self.assertEqual(conversation["signals"]["occurrences"], 2)
            self.assertNotIn("ses_private_value", conversation_text)
            self.assertNotIn("ses_second_private_value", conversation_text)

            annotations = output / "reviews" / "annotations.jsonl"
            annotations.write_text('{"candidate_id":"manual","status":"approved"}\n', encoding="utf-8")
            export_dataset(memory, output)
            self.assertIn("manual", annotations.read_text(encoding="utf-8"))

    def test_export_keeps_only_latest_key_label_for_each_response(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = root / "memory"
            output = root / "training"
            _write_jsonl(
                output / "inbox" / "evaluation_flags.jsonl",
                [
                    {
                        "flag_id": "flag_good",
                        "target_id": "target_1",
                        "group_id": "session_hash_only",
                        "label": "good_example",
                        "pressed_at": "2026-08-16T12:00:00+09:00",
                        "target": {
                            "kind": "player_chat",
                            "input": {"raw_text": "丸石やな"},
                            "output": {"actions": [{"text": "そうやな"}]},
                        },
                    },
                    {
                        "flag_id": "flag_review",
                        "target_id": "target_1",
                        "group_id": "session_hash_only",
                        "label": "needs_review",
                        "pressed_at": "2026-08-16T14:00:00+09:00",
                        "supersedes_flag_id": "flag_good",
                        "target": {
                            "kind": "player_chat",
                            "input": {"raw_text": "丸石やな"},
                            "output": {"actions": [{"text": "そうやな"}]},
                        },
                    },
                    # HTTP到着順が逆転しても、物理的に後で押した時刻を正にする。
                    {
                        "flag_id": "flag_stale_arrival",
                        "target_id": "target_1",
                        "group_id": "session_hash_only",
                        "label": "good_example",
                        "pressed_at": "2026-08-16T13:00:00+09:00",
                        "target": {
                            "kind": "player_chat",
                            "input": {"raw_text": "丸石やな"},
                            "output": {"actions": [{"text": "そうやな"}]},
                        },
                    },
                ],
            )

            manifest = export_dataset(memory, output)

            self.assertEqual(manifest["candidate_counts"]["response_quality_review"], 1)
            self.assertEqual(
                manifest["candidate_distributions"]["human_key_label"],
                {"needs_review": 1},
            )
            self.assertEqual(
                manifest["human_evaluation_extraction"]["superseded_flags"],
                2,
            )
            candidate = json.loads(
                (output / "candidates" / "response_quality_review.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(candidate["candidate_output"]["quality_label"], "needs_review")
            self.assertEqual(candidate["signals"]["superseded_flag_count"], 2)
            self.assertFalse(candidate["signals"]["quality_confirmed"])


if __name__ == "__main__":
    unittest.main()
