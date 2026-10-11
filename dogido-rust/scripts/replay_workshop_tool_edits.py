#!/usr/bin/env python3
"""Replay the two recorded Gemma edit calls through Rust without model calls or saving."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "partial_edit": (["さくらのは", "くろいおのへと", "あさのいろ"], "くろいをしろいに変えて"),
    "new_replacement": (["やみのなか", "ひかりかかして", "つちをさす"], "そうだね光かざしてに変えてみましょう"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    requests = []
    names = []
    for case in json.loads(args.record.read_text())["cases"]:
        if case["case"] not in CASES:
            continue
        texts, raw = CASES[case["case"]]
        calls = case["response"]["choices"][0]["message"].get("tool_calls", [])
        if len(calls) != 1 or calls[0]["function"]["name"] != "stage_player_edit":
            raise ValueError(f"unexpected recorded call: {case['case']}")
        # Context is the original test fixture, never parsed from model arguments.
        lines = [{"line_id": f"line_{i+1}", "line_index": i, "position": position,
                  "canonical_name": name, "surface_text": text, "reading_text": text,
                  "source_atom_ids": [], "source_atoms": [], "provenance": "generated"}
                 for i, (text, position, name) in enumerate(zip(texts,
                     ("upper", "middle", "lower"), ("上五", "中七", "下五")))]
        requests.append({"workshop": {"current_lines": lines, "emission": {"lines": lines},
                                     "materials": {}, "followup": "discussion"},
                         "text": raw, "arguments": json.loads(calls[0]["function"]["arguments"])})
        names.append(case["case"])
    if sorted(names) != sorted(CASES):
        raise ValueError("both recorded edit cases are required exactly once")
    with args.output.open("x") as output:
        run = subprocess.run([str(args.validator.resolve()), "--python", str(args.python.resolve()),
                              "--helper", str(args.helper.resolve())], cwd=ROOT,
                             input="".join(json.dumps(r, ensure_ascii=False)+"\n" for r in requests),
                             text=True, capture_output=True, timeout=60, check=True)
        results = [json.loads(line) for line in run.stdout.splitlines()]
        if len(results) != len(names):
            raise ValueError("one validator result is required per request")
        report = {"scope": "recorded responses; no model calls or saving",
                  "cases": [{"case": name, "request": request, "result": result}
                            for name, request, result in zip(names, requests, results)]}
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    for case in report["cases"]:
        print(json.dumps({"case": case["case"], **case["result"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
