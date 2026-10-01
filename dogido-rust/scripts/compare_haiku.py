#!/usr/bin/env python3
"""現行PythonとRustへ同じ句・検査票を渡す。実モデル、音声、保存は使わない。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.config import Settings
from dogido_server.haiku.generation import generate_grounded_haiku
from dogido_server.haiku.source_atoms import HaikuSourceAtom
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import GeneratedText

LINES = ["くさちのひ", "くろきつるぎの", "かげのさむさ"]
ALTERNATIVES = ["あさのひかり", "くろいつるぎの", "かげはさむし"]


def atom(index, **kwargs):
    return dict(atom_id=f"observation:haiku:source:{index}", text=("草地の日", "黒い剣", "冷たい影")[index % 3],
                source_ref=f"observation:{index}", field_path="observed_label", observation_role="test",
                kind="observation", claim_class="factual", claim_scopes=["observed_state"], basis_atom_ids=[], **kwargs)


def base():
    return {"details": {}, "source_atoms": [atom(i) for i in range(3)], "fallback_text": "まとまらんかった。。。",
            "max_tokens": 192, "grounding_max_tokens": 512, "generation_strategy": "three_slot",
            "max_regeneration_rounds": 6, "llm_enabled": True}


def passing(request):
    indices = [row["line_index"] for row in request.details["grounding_lines"]]
    atoms = request.details["source_atoms"]
    numbers = request.details["grounding_atom_numbers"]
    eligible = {atom["atom_id"] for atom in atoms}
    return {"verdicts": {str(i): "pass" for i in indices}, "assessments": [
        {"line_index": i, "atom_ids": [numbers[
            f"observation:haiku:source:{i}" if f"observation:haiku:source:{i}" in eligible
            else atoms[pos % len(atoms)]["atom_id"]]]}
        for pos, i in enumerate(indices)], "failure_reasons": {}}


def regeneration(request):
    return {"lines": [{"line_index": i, "text": ALTERNATIVES[i]} for i in request.details["failed_line_indices"]]}


class Scripted:
    def __init__(self, responses):
        self.script = iter(responses)
        self.requests, self.responses, self.prompts = [], [], []

    def generate_structured_json(self, request):
        self.requests.append(asdict(request))
        self.prompts.append(build_messages(request))
        response = next(self.script, {"__dogido_status": "generation_error"})
        response = response(request) if callable(response) else deepcopy(response)
        self.responses.append(response)
        if "raw" in response:
            class Parser(DogidoLLM):
                def _generate_backend_text(self, _request):
                    return GeneratedText(**response["raw"])
            return Parser(Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions")).generate_structured_json(request)
        if "error" in response:
            return dict(request.fallback_value, __dogido_status="generation_error")
        return response


def raw(text, reason="length"):
    return {"raw": {"text": text, "finish_reason": reason, "completion_tokens": 512, "prompt_tokens": 1234}}


def cases():
    output = []
    def add(name, responses, change=None):
        value = base()
        if change: change(value)
        output.append((name, value, [{"lines": LINES}] + responses))
    add("all_pass", [passing])
    add("grounding_budget_independent", [passing], lambda x: x.update(grounding_max_tokens=600))
    def missing(req):
        report = passing(req)
        report["assessments"] = [row for row in report["assessments"] if row["line_index"] != 1]
        return report
    add("missing_middle_recheck_original", [missing, passing])
    add("missing_middle_still_unavailable", [missing, {"__dogido_status": "output_truncated"}])
    add("transport_error_then_recheck", [{"error": "offline"}, passing, passing, passing])
    for refs in ([0], [4], [True], [1.0], ["1"], [1, 1], [], ["invented"]):
        def invalid(req, refs=refs):
            report = passing(req)
            report["assessments"][0]["atom_ids"] = refs
            return report
        add("invalid_reference_" + repr(refs), [invalid, passing])
    for verdict in ("meaning_fail", "japanese_fail", "both_fail"):
        def negative(req, verdict=verdict):
            report = passing(req)
            report["verdicts"]["1"] = verdict
            if verdict != "japanese_fail": report["assessments"][1]["atom_ids"] = []
            report["failure_reasons"] = {"1": "意味または日本語が不自然。", "0": "これは合格理由なので渡さない"}
            return report
        for strategy in ("three_slot", "whole_poem", "one_plus_two", "two_plus_one"):
            add(f"{strategy}_{verdict}", [negative, regeneration, passing], lambda x, s=strategy: x.update(generation_strategy=s))
    def legacy(req):
        p = passing(req)
        return {"assessments": [dict(row, meaning_retained=True, natural_japanese=True, reason="合格") for row in p["assessments"]]}
    add("legacy_booleans", [legacy])
    add("legacy_single_recheck", [missing, lambda r: dict(passing(r)["assessments"][0], meaning_retained=True, natural_japanese=True)])
    for bad in (None, "pass", {}, {"0": "maybe"}):
        add("invalid_verdict_" + repr(bad), [lambda r, b=bad: dict(passing(r), verdicts=b), passing, passing, passing])
    def conflict(req):
        p = passing(req)
        p["assessments"][0]["meaning_retained"] = False
        return p
    add("conflicting_bool", [conflict, passing])
    def original_ids(req):
        p = passing(req)
        for row, source in zip(p["assessments"], req.details["source_atoms"]): row["atom_ids"] = [source["atom_id"]]
        return p
    add("original_ids", [original_ids])
    def interpretation(x):
        row = deepcopy(x["source_atoms"][0]); row.update(atom_id="interpretation:scene", kind="poetic_interpretation", basis_atom_ids=[a["atom_id"] for a in x["source_atoms"]])
        x["source_atoms"].append(row)
    def shared(req):
        p = passing(req)
        number = req.details["grounding_atom_numbers"]["interpretation:scene"]
        for row in p["assessments"]: row["atom_ids"] = [number]
        return p
    add("shared_poetic_interpretation", [shared], interpretation)
    add("insufficient_independent_sources", [], lambda x: [a.update(basis_atom_ids=["same"]) for a in x["source_atoms"]])
    add("invalid_strategy", [], lambda x: x.update(generation_strategy="invented"))
    add("no_model", [], lambda x: x.update(llm_enabled=False))
    # 音数境界、文字種、hardとsoftを実際の採否経路で比較する。
    for text in ("あさ", "あさひ", "あさのひ", "あさのひかり", "あさのひかりよ", "ぁいう", "きゃくのひ", "っあさのひ", "草地の日", "クサチノヒ", "あいうえお", " / || ", "あさ\nのひ"):
        name = "meter_script_" + repr(text)
        output.append((name, base(), [{"lines": [text, *LINES[1:]]}, passing, regeneration, passing]))
    for constraint in ("hard", "soft"):
        def change(x, c=constraint):
            x["max_regeneration_rounds"] = 0
            x["details"]["haiku_constraints"] = ({"forbidden_terms": ["くさち"]} if c == "hard" else
                {"player_lessons": [{"forbidden_fragments": ["くさち"]}]})
        add(constraint + "_constraint", [passing], change)
    for limit in (-2, 0, 1, 6, 8, 99):
        def fail(req):
            p = passing(req)
            for i in p["verdicts"]: p["verdicts"][i] = "japanese_fail"
            return p
        def repeat(req): return {"lines": [{"line_index": i, "text": LINES[i]} for i in req.details["failed_line_indices"]]}
        add(f"bounded_repeated_candidate_{limit}", [fail] + [repeat] * 12, lambda x, l=limit: x.update(max_regeneration_rounds=l))
    for text in ("{\"verdicts\":", '{"verdicts":{"0":"pass"},"assessments":[',
                 '{"assessments":[{"line_index":0,"atom_ids":[1],"meaning_retained":true,"natural_japanese":true},',
                 '{"verdicts":{"0":"pass"},"assessments":[{"line_index":0,"atom_ids":[1]}],"verdicts":'):
        add("truncated_" + text, [raw(text), passing, passing, passing])
    def prefix(req):
        p = passing(req)
        return raw(json.dumps(p, ensure_ascii=False).rsplit('"failure_reasons"', 1)[0] + '"failure_reasons":{"1":"途中')
    add("complete_verdict_and_evidence_prefix", [prefix])
    add("closed_fence", [lambda r: raw("```json\n" + json.dumps(passing(r)) + "\n```", "stop")])
    for duplicate in (LINES[0], "クサチノヒ", "く さ ち の ひ"):
        output.append(("duplicate_line_" + duplicate, base(), [{"lines": [LINES[0], LINES[1], duplicate]}, passing, regeneration, passing]))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=ROOT / "dogido-rust/target/debug/examples/replay_haiku")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    fixtures, expected, names = [], [], []
    for name, value, responses in cases():
        llm = Scripted(responses)
        options = deepcopy(value); options.pop("llm_enabled")
        options["source_atoms"] = tuple(HaikuSourceAtom(**a) for a in options["source_atoms"])
        result = generate_grounded_haiku(llm if value["llm_enabled"] else None, **options)
        if name in {"all_pass", "missing_middle_recheck_original", "legacy_single_recheck",
                    "complete_verdict_and_evidence_prefix", "closed_fence", "shared_poetic_interpretation"}:
            assert result.accepted and result.text.splitlines() == LINES and result.regeneration_rounds == 0, name
        if name == "missing_middle_still_unavailable":
            assert not result.accepted and result.failure_reason == "grounding_unavailable" and result.regeneration_rounds == 0
        if any(name == f"{strategy}_{verdict}" for strategy in ("three_slot", "whole_poem", "one_plus_two", "two_plus_one")
               for verdict in ("meaning_fail", "japanese_fail", "both_fail")):
            assert result.accepted and result.regeneration_rounds == 1, name
        fixtures.append({"input": value, "responses": llm.responses})
        expected.append({"result": json.loads(json.dumps(asdict(result))), "requests": llm.requests, "prompts": llm.prompts, "remaining_responses": 0})
        names.append(name)
    process = subprocess.run([str(args.binary), sys.executable], input="".join(json.dumps(case, ensure_ascii=False) + "\n" for case in fixtures),
                             text=True, capture_output=True, timeout=120)
    if process.returncode: raise RuntimeError(process.stderr)
    actual = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(actual) == len(expected), (len(actual), len(expected))
    failures = [{"name": name, "expected": want, "actual": got, "fixture": fixture}
                for name, want, got, fixture in zip(names, expected, actual, fixtures) if want != got]
    report = {"cases": len(names), "matched": len(names) - len(failures), "failures": failures, "names": names}
    path = ROOT / "dogido-rust/reports/haiku-parity.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"haiku parity: {report['matched']}/{report['cases']}; {path}")
    for failure in failures[:6]: print("MISMATCH:", failure["name"])
    if failures: raise SystemExit(1)


if __name__ == "__main__":
    main()
