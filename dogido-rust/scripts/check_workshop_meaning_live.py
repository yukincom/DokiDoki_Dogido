#!/usr/bin/env python3
"""Opt-in meaning replay against a running model; never writes game/session state.

Uses the Python prompt/validator oracle compared with native Rust by the existing
golden tests. Contract acceptance is mechanical; plausibility requires reviewing
the returned speech against review_criteria. No expected answer enters the prompt.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

import workshop_oracle as oracle

CASES = Path(__file__).parent / "fixtures/workshop_meaning_cases.json"
ACTIONS = ["respond", "explain", "ask", "inspect", "propose_revision",
           "stage_player_edit", "show_current", "compare", "accept_pending",
           "reject_pending", "close_workshop", "unrelated", "stage_conversation_candidate"]


def make_frame(dataset, case):
    view = deepcopy(dataset["contexts"][case["context"]])
    view["dialogue"] = deepcopy(case.get("dialogue", []))
    return {"op": "prepare", "text": case["question"], "phase": "decide",
            "observation": None, "turn_steps": [], "allowed_actions": ACTIONS.copy(),
            "workshop": view}


def replay(dataset, case, base_url, model):
    frame = make_frame(dataset, case)
    original_view = deepcopy(frame["workshop"])
    attempts = []
    for _ in range(2):  # Same single schema repair as the runtime.
        frame["op"] = "prepare"
        messages = oracle.handle(frame)["messages"]
        request = {"model": model, "messages": messages, "temperature": 0.25,
                   "max_tokens": 420, "stream": False,
                   "chat_template_kwargs": {"enable_thinking": False}}
        started = time.monotonic()
        with urlopen(Request(base_url.rstrip("/") + "/chat/completions",
                             data=json.dumps(request).encode(),
                             headers={"Content-Type": "application/json"}), timeout=90) as response:
            output = json.load(response)
        choice = output["choices"][0]
        raw = choice["message"]["content"]
        attempt = {"elapsed_seconds": round(time.monotonic() - started, 3),
                   "finish_reason": choice.get("finish_reason"), "raw": raw,
                   "prompt_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()}
        attempts.append(attempt)
        if choice.get("finish_reason") in {"length", "max_tokens", "MAX_TOKENS"}:
            attempt["validation"] = {"reason": "output_truncated", "step": None}
            break
        try:
            # Accept a surrounding code fence, as the runtime object extractor does.
            start, end = raw.index("{"), raw.rindex("}") + 1
            payload = json.loads(raw[start:end])
        except (ValueError, TypeError):
            attempt["validation"] = {"reason": "invalid_json", "step": None}
            break
        frame.update(op="validate", payload=payload)
        validation = oracle.handle(frame)
        attempt.update(payload=payload, validation=validation)
        if not validation.get("contract_errors"):
            break
        frame["retry"] = {"errors": validation["contract_errors"], "payload": payload}
    assert frame["workshop"] == original_view, "meaning replay mutated its input"
    step = attempts[-1]["validation"].get("step") or {}
    findings = step.get("analysis", {}).get("findings", [])
    return {"name": case["name"], "question": case["question"],
            "verse": original_view["emission"]["reading_text"], "attempts": attempts,
            "speech": step.get("speech", ""),
            "contract_passed": bool(step.get("action") == "explain" and not findings),
            "input_unchanged": True, "plausibility": "requires_review"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    dataset = json.loads(CASES.read_text())
    selected = [c for c in dataset["cases"] if not args.case or c["name"] in args.case]
    unknown = set(args.case) - {c["name"] for c in selected}
    if unknown:
        parser.error(f"unknown cases: {sorted(unknown)}")
    report = {"model": args.model, "base_url": args.base_url,
              "fixture_sha256": hashlib.sha256(CASES.read_bytes()).hexdigest(),
              "review_criteria": dataset["review_criteria"], "results": []}
    for case in selected:
        result = replay(dataset, case, args.base_url, args.model)
        report["results"].append(result)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: result[k] for k in ["name", "contract_passed", "speech"]}, ensure_ascii=False), flush=True)
    return 0 if all(r["contract_passed"] for r in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
