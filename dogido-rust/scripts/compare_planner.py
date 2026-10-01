#!/usr/bin/env python3
"""通常会話plannerの根拠検証・prompt・観測照合を現行Pythonと比較。ネットワークなし。"""
from copy import deepcopy
from dataclasses import asdict
from itertools import product
from pathlib import Path
import json
import logging
import subprocess

from planner_cases import ROOT, HISTORY, TARGET, cases, repair_payload
from dogido_server.dialogue import conversation_repair as repair
from dogido_server.dialogue.player_chat_planner import (
    PLAYER_CHAT_PLAN_ACTIONS, PlayerChatPlan, _parse_model_plan,
    fixed_grounded_player_chat_reply, ground_player_chat_entity,
)
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.structured_contracts import validate_structured_payload, STRUCTURED_CONTRACT_RETRY_KEY
from dogido_server.llm.types import StructuredGenerationRequest


def normal_payload(details, action="continue_conversation", confidence=0.95):
    query = details["current"]["text"] if action in {"check_entity_presence", "identify_entity", "correct_previous_reply"} else ""
    return {"action": action, "focus": "今回の発話の焦点", "entity_query": query,
            "evidence": [{"turn_id": "current", "quote": details["current"]["text"]}], "confidence": confidence}


def main():
    logging.disable(logging.CRITICAL)
    requests, expected = [], []
    counts = {}
    def add(op, want, **data):
        requests.append(dict(op=op, **data))
        expected.append(want)
        counts[op] = counts.get(op, 0) + 1
    def evaluate(details, payload):
        contract = validate_structured_payload("player_chat_plan", payload, details=details)
        plan = _parse_model_plan(payload, details) if contract.accepted else None
        add("evaluate", {"accepted": contract.accepted, "plan": asdict(plan) if plan else None}, details=details, payload=payload)
    fixtures = cases()
    for fixture in fixtures.values():
        d = fixture["details"]
        add("prepared", True, input=fixture)
        add("messages", build_messages(StructuredGenerationRequest(kind="player_chat_plan", fallback_value={}, details=d)), details=d)
        for action, confidence in product(PLAYER_CHAT_PLAN_ACTIONS, [0, 0.61, 0.62, 0.77, 0.78, 0.81, 0.82, 0.95, 1]):
            evaluate(d, normal_payload(d, action, confidence))
        evaluate(d, repair_payload())
        for errors in [["repair:ungrounded"], ["action:not_allowed", "evidence:current_required"]]:
            previous = repair_payload()
            retry = deepcopy(d)
            retry[STRUCTURED_CONTRACT_RETRY_KEY] = {"errors": errors, "previous_payload": json.dumps(previous, ensure_ascii=False)}
            add("messages", build_messages(StructuredGenerationRequest(kind="player_chat_plan", fallback_value={}, details=retry)), details=d, errors=errors, previous=previous)
    d = fixtures["explicit_repair"]["details"]
    seeds = [repair_payload(), normal_payload(d)]
    # 型・未知キー・根拠・信頼度・コード側routingの拒否を確認。
    for seed in seeds:
        for field in ["action", "focus", "entity_query", "evidence", "confidence", "repair"]:
            for value in [None, True, False, 1, 0.8, "", "invented", [], {}, [None], "🐈" * 181]:
                p = deepcopy(seed); p[field] = value; evaluate(d, p)
            p = deepcopy(seed); p.pop(field, None); evaluate(d, p)
        p = deepcopy(seed); p["extra"] = "ignored?"; evaluate(d, p)
    for field, values in {
        "turn_id": ["current", "old:reply", "invented", " current", "current "],
        "quote": ["違う", "う", "創作", "違う、仲間になるのは無理ってこと", "\t違う", ""],
    }.items():
        for value in values:
            p = repair_payload(); p["evidence"][0][field] = value; evaluate(d, p)
    for field in ["target_turn_id", "target_quote", "signal_quote", "replacement_quote"]:
        for value in [None, True, 1, "", "current", "invented", "違う", "うん", "🐈" * 181]:
            p = repair_payload(); p["repair"][field] = value; evaluate(d, p)
    # 日本語引用位置と短い否定のUnicode境界。本人原文にない意味を採用しない。
    signals = ["違う", "ちがう", "ちゃう", "そういう意味ではない", "聞きまちが", "のことです", "って意味", "という意味", "違", "い", "うん", ""]
    for signal, quote in product(signals, [("", ""), ("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'), ("「", "")]):
        raw = f"🐈{quote[0]}{signal}{quote[1]}。仲間にはなれないってこと"
        add("signal", repair.has_repair_signal(raw), raw=raw)
        for occurrence in [raw, raw + signal, signal]:
            details = deepcopy(d); details["current"]["raw_text"] = occurrence
            p = repair_payload(signal=signal, replacement="仲間にはなれないってこと")["repair"]
            parsed = repair.parse_conversation_repair("repair_conversation", p, details)
            add("repair", {"repair": asdict(parsed) if parsed else None, "fallback": repair.repair_fallback(parsed) if parsed else None, "note": repair.repair_note(parsed.prompt_fields()) if parsed else None}, details=details, action="repair_conversation", payload=p)
    for word, prefix, suffix in product(["違う", "そうじゃない", "そういう意味ではない", "うん", "はい", "いいえ", "仲間"], ["", "いや、", "いや\u001c"], ["", "ですよね", "！\u3000", "んだって", "ってこと"]):
        replacement = prefix + word + suffix
        details = deepcopy(d); details["current"]["raw_text"] = "違う、" + replacement
        p = repair_payload(replacement=replacement)["repair"]
        parsed = repair.parse_conversation_repair("repair_conversation", p, details)
        add("repair", {"repair": asdict(parsed) if parsed else None, "fallback": repair.repair_fallback(parsed) if parsed else None, "note": repair.repair_note(parsed.prompt_fields()) if parsed else None}, details=details, action="repair_conversation", payload=p)
    for name in ["pending_explanation", "pending_ack", "unplayed_clarification", "stale_clarification"]:
        details = fixtures[name]["details"]
        add("pending", repair.pending_repair(details["history"]), history=details["history"])
        p = repair_payload(signal="", replacement=details["current"]["text"])
        evaluate(details, p)
    cat = {"entry_id": "minecraft:cat", "label_ja": "猫", "score": 1.0}
    goat = {"entry_id": "goat", "label": "ヤギ", "score": 0.9}
    hits_sets = [[], [cat], [cat, goat], [cat, dict(goat, score=1)], [cat, cat, goat], [{"entry_id": "outpost", "label": "前哨基地", "score": 1}, {"entry_id": "pillager", "score": 0.8}]]
    observed_sets = [[], [{"entity_id": "cat", "label": "猫"}], [{"entity_id": "goat", "label": "ヤギ"}], [{"entity_id": "pillager", "label": "ピリジャー"}]]
    for action, challenged, hits, observed in product(PLAYER_CHAT_PLAN_ACTIONS, [False, True], hits_sets, observed_sets):
        p = deepcopy(fixtures["unobserved_cat"]["fallback"])
        p.update(action=action, presence_challenged=challenged)
        plan = PlayerChatPlan(**p)
        grounding = ground_player_chat_entity(plan, topic_hits=hits, observed_entities=observed)
        add("ground", {"grounding": asdict(grounding), "fixed_reply": fixed_grounded_player_chat_reply(plan, grounding)}, plan=p, hits=hits, observed=observed)
    for text in ["", "null", "[]", "{}", '{"a":1}', '```json\n{"a":1}\n```', '前置き {"a":1} あと', '{"broken":', '{"broken": [{"a":1}', '[{"a":1}]', '{"a":1} {"b":2}', '"{escaped}"']:
        add("extract", DogidoLLM._extract_json_object(object.__new__(DogidoLLM), text), text=text)
    binary = ROOT / "target/debug/examples/check_planner"
    proc = subprocess.run([str(binary)], input="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests), capture_output=True, text=True, timeout=40)
    assert proc.returncode == 0, proc.stderr
    actual = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(actual) == len(expected)
    for index, (got, want) in enumerate(zip(actual, expected)):
        if requests[index]["op"] == "evaluate": got.pop("errors")
        want = json.loads(json.dumps(want, ensure_ascii=False))
        assert got == want, json.dumps({"index": index, "input": requests[index], "actual": got, "expected": want}, ensure_ascii=False, indent=2)
    report = {"passed": len(requests), "operations": counts, "model_calls": 0, "servers_started": 0,
              "limits": ["state-machine context/catalog projection supplied by Python reference", "native schema-shape diagnostics use Rust error codes; dynamic evidence diagnostics match Python"]}
    path = ROOT / "reports/planner-comparison.json"; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__": main()
