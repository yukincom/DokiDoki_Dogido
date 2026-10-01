"""Scorer checks; these do not test the model's understanding."""
import json
import unittest
from compare_workshop_references import evaluate, fixtures, messages


class ReferenceScoringTests(unittest.TestCase):
    def test_gold_resolves_equally_in_both_conditions(self):
        for case in fixtures():
            for mode in ("text", "id"):
                refs = case["expected_ids"] if mode == "id" else [
                    {"line_id": c["line_id"], "replacement": c["replacement"]}
                    for c in case["candidates"] if c["id"] in case["expected_ids"]]
                content = json.dumps({"action": case["expected_action"], "references": refs, "evidence": case["input"]})
                self.assertTrue(evaluate(case, mode, content)["correct"], (case["name"], mode))

    def test_hallucinated_reference_and_nonverbatim_text_fail(self):
        case = fixtures()[0]
        for mode, refs in [("id", ["c99"]), ("text", [{"line_id": "line_1", "replacement": "桜色"}])]:
            output = {"action": "stage", "references": refs, "evidence": case["input"]}
            self.assertFalse(evaluate(case, mode, json.dumps(output))["valid"])

    def test_wrong_selection_and_quoted_instruction_are_errors(self):
        first, reported = fixtures()[0], fixtures()[7]
        for case, selected in [(first, "c42"), (reported, "c17")]:
            score = evaluate(case, "id", json.dumps({"action": "stage", "references": [selected], "evidence": case["input"]}))
            self.assertTrue(score["incorrect_stage"])
            self.assertFalse(score["correct"])

    def test_model_payload_does_not_include_gold(self):
        for case in fixtures():
            for mode in ("text", "id"):
                prompt = json.dumps(messages(case, mode), ensure_ascii=False)
                self.assertNotIn("expected_action", prompt)
                self.assertNotIn("expected_ids", prompt)
                if mode == "text":
                    self.assertNotIn('\\"id\\"', prompt)

    def test_malformed_output_is_not_success(self):
        for content in ("[]", "null", "not json", '{"action":"stage"}'):
            self.assertFalse(evaluate(fixtures()[0], "id", content)["valid"])

    def test_invalid_evidence_does_not_hide_an_incorrect_stage(self):
        case = fixtures()[9]
        score = evaluate(case, "id", json.dumps({"action": "stage", "references": ["c17"], "evidence": "過去の発話"}))
        self.assertFalse(score["valid"])
        self.assertTrue(score["incorrect_stage"])


if __name__ == "__main__":
    unittest.main()
