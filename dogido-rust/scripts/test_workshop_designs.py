"""State/patch boundaries and the real pure edit checker; no live model calls."""
import json
import os
from pathlib import Path
import unittest
from compare_workshop_designs import (load_helpers, check_edit, merge_edit, remember, lookup,
                                     focus_context, parse, valid_edit, raw_diagnostic)
from workshop_design_cases import SAKURA, EYES, edit, focus_cases


class DesignBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = Path(os.environ.get("DOGIDO_TRIAL_REPO", str(Path(__file__).resolve().parents[2])))
        cls.handle, cls.intent_check = map(staticmethod, load_helpers(repo))

    def test_delta_preserves_untouched_target(self):
        previous = edit(1, "くろいめ", "あかるいめ")
        result = merge_edit(previous, {"replacement_text": "ひかるめ"}, "delta")
        self.assertEqual(result, edit(1, "くろいめ", "ひかるめ"))
        self.assertEqual(previous["replacement_text"], "あかるいめ")

    def test_patch_has_no_arbitrary_document_access(self):
        for update in ({"save": True}, {"line_id": None}, {}, {"replacement_text": ""}):
            with self.assertRaises(ValueError):
                merge_edit(edit(1, "くろいめ", "あかるいめ"), update, "delta")
        with self.assertRaises(ValueError):
            merge_edit(edit(1, "くろいめ", "あかるいめ"), {"replacement_text": "ひかるめ"}, "whole")

    def test_existing_checker_applies_only_the_requested_span(self):
        result = self.handle_edit(EYES, edit(1, "くろいめ", "ひかるめ"))
        self.assertEqual(result["text"].splitlines(), ["ひかるめが", *EYES[1:]])

    def handle_edit(self, lines, proposal):
        return check_edit(self.handle, lines, proposal)

    def test_invalid_meter_idea_survives_but_is_not_usable(self):
        proposal = edit(3, "あさのいろ", "あさのひかり")
        validation = self.handle_edit(SAKURA, proposal)
        self.assertFalse(validation["text"])
        shelf, candidate = remember([], proposal, SAKURA, "あさのひかりという案", 1, "shelf", validation)
        self.assertFalse(candidate["validation"]["usable"])
        self.assertEqual(lookup(shelf, "c1", SAKURA)["edit"], proposal)
        self.assertTrue(candidate["validation"]["reasons"])

    def test_multiple_candidates_keep_identity_and_source(self):
        shelf = []
        for i, word in enumerate(("さくらいろ", "はるのいろ"), 1):
            proposal = edit(1, "さくらのは", word)
            shelf, _ = remember(shelf, proposal, SAKURA, word, i, "shelf", self.handle_edit(SAKURA, proposal))
        self.assertEqual(lookup(shelf, "c1", SAKURA)["edit"]["replacement_text"], "さくらいろ")
        self.assertEqual(lookup(shelf, "c2", SAKURA)["source_text"], "はるのいろ")
        last, _ = remember(shelf, edit(1, "さくらのは", "さくらいろ"), SAKURA, "さくらいろ", 3, "last_only", {"text": "valid"})
        with self.assertRaisesRegex(ValueError, "candidate_not_available"):
            lookup(last, "c1", SAKURA)

    def test_stale_and_invented_candidates_are_rejected(self):
        proposal = edit(1, "さくらのは", "さくらいろ")
        shelf, _ = remember([], proposal, SAKURA, "さくらいろ", 1, "shelf", {"text": "valid"})
        with self.assertRaisesRegex(ValueError, "stale_candidate"):
            lookup(shelf, "c1", ["はるのいろ", *SAKURA[1:]])
        with self.assertRaisesRegex(ValueError, "ungrounded_offer"):
            remember([], proposal, SAKURA, "なんとかして", 1, "shelf", {"text": "valid"})
        self.assertFalse(valid_edit(edit(1, "ないことば", "さくらいろ"), SAKURA))

    def test_existing_intent_check_blocks_reported_and_hypothetical_edits(self):
        for text in ("最初の案にしてと言われた", "『ひかるめ』に変えたらどうなる？ まだ変更しないで。"):
            self.assertFalse(self.intent_check(text, text))
        self.assertTrue(self.intent_check("最初の案にして", "最初の案にして"))

    def test_closed_focus_cannot_restore_a_stale_question(self):
        case = next(c for c in focus_cases() if c["name"] == "closed_does_not_reopen")
        self.assertIsNone(focus_context(case, "focus")["suspended_main_question"])

    def test_focus_payload_contains_no_gold_answers(self):
        for case in focus_cases():
            for mode in ("history", "focus"):
                data = focus_context(case, mode)
                self.assertNotIn("expected", json.dumps(data))
                self.assertEqual(len(data["recent_history"]), 4)

    def test_semantic_diagnostic_cannot_hide_missing_evidence(self):
        content = '{"action":"update","update":{"replacement_text":"ひかるめ"}}'
        context = {"previous_edit": edit(1, "くろいめ", "あかるいめ")}
        row = {"family": "patch", "mode": "delta", "content": content,
               "request": {"messages": [{}, {"content": json.dumps(context)}]},
               "expected": edit(1, "くろいめ", "ひかるめ")}
        self.assertTrue(raw_diagnostic(row)["raw_meaning_correct"])
        with self.assertRaisesRegex(ValueError, "invalid_fields"):
            parse(content, ("action", "update", "evidence"), "ひかるめにして")

    def test_evidence_from_history_is_not_current_evidence(self):
        with self.assertRaisesRegex(ValueError, "invalid_evidence"):
            parse('{"action":"select","evidence":"最初の案にして"}', ("action", "evidence"), "意味を教えて")


if __name__ == "__main__":
    unittest.main()
