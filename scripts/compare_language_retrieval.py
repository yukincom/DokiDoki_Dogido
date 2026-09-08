"""Offline paired text experiment; never imports or calls the browser/audio service.

The candidate policies below were frozen before the independent new cases were seen.
This script is not imported by the application.

Historical harness for the 2026-09-07 pre-consent controller. Its one-turn
web.calls metric does not cover the later consent/playback gate. Do not bypass
the baseline-policy self-test to run it against the current controller.
"""

from __future__ import annotations

import hashlib


CURRENT_GAP_POLICY = """知らない部分はmissingに記し、textでも短く限界を伝える。答えられる部分まで捨てない。
資料では答えを支えられない、または解釈に確信を持てない場合はpartial/unsupportedとmissingで知らせる。
この後に外部の資料を調べられる場合がある。分からないのにanswerとして断言しない。
資料で確認できた説明はanswer、一部だけならpartial、不足ならunsupported。"""

REQUIRED_GAP_POLICY = """statusとmissingは、今回の質問に答えるために必要な内容だけについて判定する。
資料の一般規則を質問の例に適用して必要な答えを出せればanswer、missingは空文字。
質問されていない語源、別の用法、隣接する話題、追加の詳説が資料にないことは不足に数えない。
裏付けのない補足は付け足さず省く。補足を自分で増やしてから、その根拠不足を検索理由にしない。
主質問の結論を決めるために必要な事実が不足する場合だけpartial/unsupportedとする。
その場合missingには、結論に必要なのに確認できていない具体的な点を一つ記す。
答えられる部分はtextで短く答え、残る限界も短く伝える。"""

INTERNAL_KNOWLEDGE_REPLACEMENTS = {
    "使える知識はfactsに限る。": (
        "資料factsと、確かに知っている基礎的な知識を使ってよい。"
        "factsが空でも、知っていることに答えてよい。推測を事実にしない。"
    ),
    "それ以外の意味比較も必ず資料を必要とする。applicationを書くだけで根拠の代わりにはならない。": (
        "意味比較には資料または確かに知っている知識を使う。"
        "自分の知識だけによる説明に資料のidを付けない。"
    ),
    "資料の一般規則を質問の例に適用して必要な答えを出せればanswer、missingは空文字。": (
        "資料または確かに知っている一般規則を質問の例に適用して必要な答えを出せればanswer、missingは空文字。"
    ),
    "裏付けのない補足は付け足さず省く。補足を自分で増やしてから、その根拠不足を検索理由にしない。": (
        "確かでない補足は省く。資料に載っていないことと、自分が知らないことを区別する。"
        "補足を自分で増やしてから、その根拠不足を検索理由にしない。"
    ),
    "fact_idsは実際に説明を支える資料のidをコピー。IDの存在だけでは意味の裏付けにならない。": (
        "fact_idsは提供されたfactsのうち実際に説明を支えるidだけをコピーする。"
        "内部知識だけで答えた場合は空配列でよい。存在しないidを作らない。"
        "IDの存在だけでは意味の裏付けにならない。"
    ),
}


def adapt_policy(messages, variant):
    """Keep the actual format and contract-retry prompt; change only reply policy."""
    if variant == "A":
        return messages
    changed = [dict(message) for message in messages]
    system = changed[0]["content"]
    assert system.count(CURRENT_GAP_POLICY) == 1, "Baseline policy changed; re-register comparison"
    system = system.replace(CURRENT_GAP_POLICY, REQUIRED_GAP_POLICY)
    system = system.replace("未確認部分。なければ空文字", "主質問に必要な未確認部分だけ。なければ空文字")
    if variant in {"C", "D"}:
        for old, new in INTERNAL_KNOWLEDGE_REPLACEMENTS.items():
            assert system.count(old) == 1, old
            system = system.replace(old, new)
    else:
        assert variant == "B"
    changed[0]["content"] = system
    return changed


def policy_hash():
    return hashlib.sha256(
        (REQUIRED_GAP_POLICY + repr(INTERNAL_KNOWLEDGE_REPLACEMENTS)).encode()
    ).hexdigest()


# Everything below is evaluation plumbing, not application behaviour.
import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from dogido_server.config import Settings
from dogido_server.language_dialogue.controller import Focus, LanguageDialogue
from dogido_server.language_dialogue.retrieval import LocalDialogueSearch, SearchResult
from dogido_server.language_dialogue.web_research import WebResult
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.types import StructuredGenerationRequest

FROZEN_POLICY_HASH = "54666546a02635a64712893cb0a932bc5c0c55234d3597535e48e9ec01fdd98b"
MODEL_ID = "mlx-community/Qwen3.6-35B-A3B-4bit-DWQ"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ExperimentModel(DogidoLLM):
    variant = "A"

    def __init__(self, settings):
        super().__init__(settings)
        self.attempts = []

    def _build_messages(self, request):
        messages = super()._build_messages(request)
        return adapt_policy(messages, self.variant) if request.kind == "language_dialogue_reply" else messages

    def _generate_backend_text(self, request):
        messages = self._build_messages(request)
        started = time.perf_counter()
        entry = {"kind": request.kind, "variant": self.variant,
                 "request": asdict(request), "messages_hash": digest(messages),
                 "system_hash": digest(messages[0]), "input_hash": digest(messages[1:]),
                 "temperature": request.temperature, "max_tokens": request.max_tokens}
        try:
            raw = super()._generate_backend_text(request)
            entry["raw"] = raw
            return raw
        except Exception as exc:
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.attempts.append(entry)


class RecordingWeb:
    """No real network implementation exists in this evaluation double."""

    def __init__(self):
        self.calls = []

    def search(self, target, terms, facet, *, known_urls=(), web_query="", cancelled=None, emit=None):
        self.calls.append({"target": target, "terms": terms, "facet": facet,
                           "known_urls": list(dict.fromkeys(known_urls))})
        return WebResult(status="audit_only", search_status="not_executed")


class FrozenSearch:
    def __init__(self, frozen=None):
        self.frozen = frozen
        self.calls = []

    def search(self, terms, *, facet, target):
        query = {"terms": terms, "facet": facet, "target": target}
        started = time.perf_counter()
        if self.frozen is None:
            result = LocalDialogueSearch().search(terms, facet=facet, target=target)
        else:
            assert len(self.calls) < len(self.frozen), "Unexpected search call"
            entry = self.frozen[len(self.calls)]
            assert query == entry["query"], "Frozen interpretation/search drift"
            result = SearchResult(**deepcopy(entry["result"]))
        # Snapshot before the controller optionally appends a direct character comparison.
        self.calls.append({"query": deepcopy(query), "result": deepcopy(asdict(result)),
                           "duration_ms": round((time.perf_counter() - started) * 1000, 3)})
        return result


class ControllerModel:
    def __init__(self, model, *, prepared=None):
        self.model = model
        self.prepared = prepared
        self.calls = []

    def generate_structured_json(self, request):
        assert request.kind in {"language_dialogue_interpretation", "language_dialogue_reply"}
        started = time.perf_counter()
        if request.kind == "language_dialogue_interpretation" and self.prepared is not None:
            original = self.prepared[0]
            assert asdict(request) == original["request"], "Interpretation input changed"
            result = deepcopy(original["result"])
            replayed = True
        elif request.kind == "language_dialogue_reply" and self.prepared is None:
            result = {"__dogido_status": "not_executed"}
            replayed = True
        else:
            result = self.model.generate_structured_json(request)
            replayed = False
        self.calls.append({"request": deepcopy(asdict(request)), "result": deepcopy(result),
                           "replayed_or_suppressed": replayed,
                           "duration_ms": round((time.perf_counter() - started) * 1000, 3)})
        return result


def make_dialogue(model, search, web, case):
    dialogue = LanguageDialogue(model, search=search, web=web)
    for index, entry in enumerate(case.get("history", []), 1):
        dialogue.history.append({"turn_id": f"h{index}", "role": entry["role"],
                                 "text": entry["content"], "source": "text"})
    if dialogue.history:
        dialogue.mode = "language"
        questions = [t["text"] for t in dialogue.history if t["role"] == "user"]
        dialogue.focus = Focus(question=questions[-1] if questions else "")
    return dialogue


def run_turn(dialogue, case):
    return dialogue.turn(case["text"], turn_id="t1", source=case.get("source", "text"))


def measure_call(model, request):
    start_index = len(model.attempts)
    started = time.perf_counter()
    result = model.generate_structured_json(request)
    return {"result": result, "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "attempts": deepcopy(model.attempts[start_index:])}


def run_case(model, case, index):
    # Interpret and retrieve once, before either candidate answer is generated.
    model.variant = "A"
    start_index = len(model.attempts)
    preparing = ControllerModel(model)
    search = FrozenSearch()
    preparation = run_turn(make_dialogue(preparing, search, RecordingWeb(), case), case)
    row = {"case_id": case["id"], "cohort": case["cohort"], "case": deepcopy(case),
           "preparation": {"record": preparation, "calls": preparing.calls,
                           "search_calls": search.calls,
                           "attempts": deepcopy(model.attempts[start_index:])},
           "variants": {}, "probes": {}}
    reply_requests = [c["request"] for c in preparing.calls
                      if c["request"]["kind"] == "language_dialogue_reply"]
    row["reached_reply_stage"] = bool(reply_requests)
    row["execution_order"] = ["A", "B"] if index % 2 == 0 else ["B", "A"]
    for variant in row["execution_order"]:
        model.variant = variant
        start_index = len(model.attempts)
        replay = ControllerModel(model, prepared=preparing.calls)
        replay_search = FrozenSearch(search.calls)
        web = RecordingWeb()
        record = run_turn(make_dialogue(replay, replay_search, web, case), case)
        assert record.get("effective_interpretation") == preparation.get("effective_interpretation")
        assert record["search"] == preparation["search"]
        assert len(replay_search.calls) == len(search.calls)
        live_calls = [c for c in replay.calls if not c["replayed_or_suppressed"]]
        row["variants"][variant] = {"record": record, "web_dispatches": web.calls,
            "calls": replay.calls, "reply_duration_ms": sum(c["duration_ms"] for c in live_calls),
            "attempts": deepcopy(model.attempts[start_index:]),
            "audited_reply": record.get("reply_analysis", {}).get("text", "") if web.calls else record.get("reply", ""),
            "post_web_reply_is_synthetic": bool(web.calls)}
    if reply_requests:
        a = [c["request"] for c in row["variants"]["A"]["calls"] if c["request"]["kind"] == "language_dialogue_reply"]
        b = [c["request"] for c in row["variants"]["B"]["calls"] if c["request"]["kind"] == "language_dialogue_reply"]
        assert a == b == reply_requests, "A/B did not receive identical answer input"
    effective = preparation.get("effective_interpretation")
    if effective and preparation["status"] not in {"clarify", "handoff"}:
        # Counterfactual capability probes: deliberately bypass the code's no-facts skip.
        details = deepcopy(preparing.calls[0]["request"]["details"])
        details.update(interpretation=effective, facts=preparation["search"]["facts"],
                       search_status=preparation["search"]["status"])
        probe_order = ["C", "D"] if index % 2 == 1 else ["D", "C"]
        if not details["facts"]:
            probe_order = ["C"]  # No evidence difference: do not count a duplicate generation.
        row["probe_order"] = probe_order
        for variant in probe_order:
            model.variant = variant
            probe_details = deepcopy(details)
            if variant == "C":
                probe_details.update(facts=[], search_status="not_requested")
            request = StructuredGenerationRequest(kind="language_dialogue_reply", fallback_value={},
                details=probe_details, route="chat", temperature=0.0, max_tokens=700)
            result = measure_call(model, request)
            allowed_ids = {f["id"] for f in probe_details["facts"]}
            result["unknown_fact_ids"] = [fid for fid in result["result"].get("fact_ids", []) if fid not in allowed_ids]
            result["input_details"] = probe_details
            row["probes"][variant] = result
        if probe_order == ["C"]:
            row["probes"]["D"] = {"not_run": "no_supplied_facts", "equivalent_to": "C"}
    return row


def load_cases(path):
    original = json.loads((ROOT / "tests/fixtures/language_dialogue/cases.json").read_text())["cases"]
    cases = [{"id": c["id"], "cohort": "regression", **c["turns"][0], "review": c["review"]}
             for c in original if c["id"].startswith("boundary_")]
    fresh = json.loads(path.read_text())["cases"]
    assert len(cases) == 15 and len(fresh) == 30
    cases += [dict(c, cohort="new") for c in fresh]
    assert len({c["id"] for c in cases}) == 45
    return cases


def summarize(rows):
    summary = {"note": "Web dispatch intentions only; no external requests or audio executed.",
               "cohorts": {}, "external_requests_executed": 0}
    for cohort in ("regression", "new"):
        selected = [r for r in rows if r["cohort"] == cohort]
        section = {"cases": len(selected), "reached_reply": sum(r["reached_reply_stage"] for r in selected),
                   "variants": {}}
        for variant in ("A", "B"):
            results = [r["variants"][variant] for r in selected]
            times = sorted(r["reply_duration_ms"] for r in results if r["calls"] and any(not c["replayed_or_suppressed"] for c in r["calls"]))
            section["variants"][variant] = {
                "web_intentions": sum(bool(r["web_dispatches"]) for r in results),
                "reasons": dict(Counter(r["record"].get("web", {}).get("trigger_reason", "none") for r in results)),
                "reply_statuses": dict(Counter(r["record"].get("reply_status", "skipped") for r in results)),
                "reply_median_ms": statistics.median(times) if times else None,
                "reply_max_ms": max(times) if times else None,
                "backend_attempts": sum(len(r["attempts"]) for r in results),
            }
        summary["cohorts"][cohort] = section
    return summary


def self_test():
    assert policy_hash() == FROZEN_POLICY_HASH
    request = StructuredGenerationRequest(kind="language_dialogue_reply", fallback_value={}, details={
        "current": {"turn_id": "t1", "role": "user", "text": "これは？"}, "facts": []})
    from dogido_server.llm.prompts import build_messages
    baseline = build_messages(request)
    assert adapt_policy(baseline, "A") == baseline
    for variant in ("B", "C", "D"):
        modified = adapt_policy(baseline, variant)
        assert modified[1:] == baseline[1:]
        assert "expected_action" not in str(modified)
    assert adapt_policy(baseline, "C") == adapt_policy(baseline, "D")
    original = SearchResult(["語"], [{"id": "fact1", "text_ja": "資料"}])
    snapshot = [{"query": {"terms": ["語"], "facet": "meaning", "target": "語"}, "result": asdict(original)}]
    replay = FrozenSearch(snapshot)
    replay.search(["語"], facet="meaning", target="語").facts.append({"id": "input-character"})
    assert snapshot[0]["result"]["facts"] == original.facts
    assert len(RecordingWeb().search("x", [], "meaning").pages) == 0
    print("SELF_TEST passed: frozen policies, identical user inputs, probe parity, snapshot isolation, Web disabled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-cases", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--limit", type=int, help="Smoke testing only; omitted for the registered run")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        return
    if not args.new_cases or not args.output:
        parser.error("--new-cases and --output are required")
    cases = load_cases(args.new_cases)
    if args.limit:
        cases = cases[:args.limit]
    settings = Settings().llm_route_settings("chat")
    if settings.llm_backend != "mlx" or settings.mlx_model_id != MODEL_ID:
        raise SystemExit("Chat model differs from registered model; configuration not changed")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"created_at": datetime.now(timezone.utc).isoformat(), "model": MODEL_ID,
        "thinking": False, "temperature": 0.0, "policy_hash": policy_hash(), "case_hash": digest(cases),
        "cases": len(cases), "no_audio": True, "web": "record_only",
        "A": "current_policy_facts_only", "B": "required_gap_policy_facts_only",
        "C": "internal_knowledge_allowed_no_facts_counterfactual",
        "D": "internal_knowledge_allowed_retrieved_facts_counterfactual",
        "counterfactuals_are_not_live_controller_results": True,
        "histories": "synthetic primed contexts, not generated preceding turns",
        "hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
            Path(__file__), ROOT / "dogido_server/language_dialogue/controller.py",
            ROOT / "dogido_server/language_dialogue/prompts.py", ROOT / "dogido_server/language_dialogue/retrieval.py",
            ROOT / "dogido_server/language_dialogue/source_cards.json", ROOT / "dogido_server/llm/client.py"]}}
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    (output / "cases.json").write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n")
    model = ExperimentModel(settings)
    print(f"MODEL {MODEL_ID}; {len(cases)} cases; browser/audio disabled", flush=True)
    started = time.perf_counter()
    if not model.preload():
        raise SystemExit(f"Model unavailable: {model.disabled_reason()}")
    metadata["preload_ms"] = round((time.perf_counter() - started) * 1000, 3)
    rows = []
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for index, case in enumerate(cases):
            row = run_case(model, case, index)
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps({"done": index + 1, "case_id": case["id"],
                "reply_stage": row["reached_reply_stage"],
                "A_web": bool(row["variants"]["A"]["web_dispatches"]),
                "B_web": bool(row["variants"]["B"]["web_dispatches"]),
                "probes": list(row["probes"])}, ensure_ascii=False), flush=True)
    (output / "summary.json").write_text(json.dumps(summarize(rows), ensure_ascii=False, indent=2) + "\n")
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print("COMPLETE " + json.dumps(summarize(rows), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
