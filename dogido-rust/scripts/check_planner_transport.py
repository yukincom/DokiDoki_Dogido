#!/usr/bin/env python3
"""Rigと実HTTPでplannerの再試行上限・timeout・終了情報を確認。モデルは呼ばない。"""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from test_support import ROOT, completion_response


def fixtures():
    return {name: json.loads((ROOT / f"fixtures/planner/{name}.json").read_text())
            for name in ("casual", "explicit_repair")}


def normal_payload(details):
    return {"action": "continue_conversation", "focus": "今回の発話の焦点", "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": details["current"]["text"]}], "confidence": .95}


def repair_payload():
    return {"action": "repair_conversation", "focus": "本人の言い直し", "entity_query": "",
            "evidence": [{"turn_id": "current", "quote": "違う"},
                         {"turn_id": "old:reply", "quote": "1位になれんでもええやん。"}],
            "confidence": .95,
            "repair": {"target_turn_id": "old:reply", "target_quote": "1位になれんでもええやん。",
                       "signal_quote": "違う", "replacement_quote": "仲間になるのは無理ってこと"}}


def json_fragments(messages):
    decoder = json.JSONDecoder()
    values = []
    for message in messages:
        text = message["content"]
        for pos, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[pos:])
                values.append(value)
            except json.JSONDecodeError:
                pass
    return values


@contextmanager
def endpoint(responses):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            incoming = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "body": incoming})
            row = responses[min(len(seen) - 1, len(responses) - 1)]
            time.sleep(row.get("delay", 0))
            raw = json.dumps(completion_response(row["text"], finish=row.get("finish", "stop")), ensure_ascii=False).encode()
            try:
                self.send_response(row.get("status", 200))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self, *args): pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}/v1", seen
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
        assert not thread.is_alive(), "temporary HTTP server still running"


def expected_plan(item, payload):
    # The synthetic accepted responses contain literal input fields. No planner,
    # classifier, validation or policy is reimplemented here.
    expected = {**deepcopy(payload), "source": "model", "status": "accepted",
                "presence_challenged": item["fallback"]["presence_challenged"]}
    if "repair" in payload:
        expected["repair"] = {**payload["repair"], "action": payload["action"],
                              "current_text": item["details"]["current"]["raw_text"]}
    else:
        expected["repair"] = None
    return expected


def main():
    inputs = fixtures()
    normal = normal_payload(inputs["casual"]["details"])
    valid = {"text": json.dumps(normal, ensure_ascii=False)}
    wrong = deepcopy(normal); wrong["evidence"][0]["quote"] = "本人が言っていない"
    bad = {"text": json.dumps(wrong, ensure_ascii=False)}
    low = deepcopy(normal); low["confidence"] = 0.61
    malformed = deepcopy(normal); malformed["confidence"] = True
    tests = [
        ("accepted", "casual", [valid], "accepted"),
        ("repair", "explicit_repair", [{"text": json.dumps(repair_payload(), ensure_ascii=False)}], "accepted"),
        ("low_confidence_no_retry", "casual", [{"text": json.dumps(low)}], "consumer_rejected"),
        ("invalid_json_no_retry", "casual", [{"text": "JSONなし"}], "invalid_json"),
        ("truncated_no_retry", "casual", [{"text": '{"action":', "finish": "length"}], "output_truncated"),
        ("evidence_retry_once", "casual", [bad, valid], "contract_retry_accepted"),
        ("shape_retry_once", "casual", [{"text": json.dumps(malformed)}, valid], "contract_retry_accepted"),
        ("retry_exhausted", "casual", [bad, bad], "schema_contract_error"),
        ("retry_invalid_json", "casual", [bad, {"text": "JSONなし"}], "schema_contract_error"),
        ("retry_transport_error", "casual", [bad, {"text": "error", "status": 503}], "schema_contract_error"),
        ("transport_error_no_retry", "casual", [{"text": "error", "status": 503}], "generation_error"),
        ("timeout_no_retry", "casual", [{"text": "遅延", "delay": 0.2}], "generation_error"),
    ]
    results = []
    binary = ROOT / "target/debug/dogido-rust"
    env = dict(os.environ); env.pop("DOGIDO_LLM_API_KEY", None)
    with tempfile.TemporaryDirectory(prefix="dogido-planner-") as directory:
        path = Path(directory) / "input.json"
        for name, fixture, rows, status in tests:
            item = inputs[fixture]
            accepted = status in {"accepted", "contract_retry_accepted"}
            want_plan = expected_plan(item, json.loads(rows[-1]["text"])) if accepted else item["fallback"]
            want_calls = 2 if name in {"evidence_retry_once", "shape_retry_once", "retry_exhausted",
                                      "retry_invalid_json", "retry_transport_error"} else 1
            path.write_text(json.dumps(item, ensure_ascii=False))
            with endpoint(rows) as (url, seen):
                done = subprocess.run([str(binary), "plan-chat", str(path), "--base-url", url, "--timeout-ms", "60" if name == "timeout_no_retry" else "2000"], capture_output=True, text=True, env=env, timeout=8)
            assert done.returncode == 0, (name, done.stderr)
            got = json.loads(done.stdout)
            assert got["result"] == status, (name, got)
            assert got["calls"] == len(seen) == want_calls, (name, got, seen)
            assert got["plan"] == want_plan, (name, got["plan"], want_plan)
            for index, sent in enumerate(seen):
                body = sent["body"]
                assert sent["path"] == "/v1/chat/completions"
                assert (body["max_tokens"], body["temperature"], body["model"], body["stream"]) == (640, 0, "default_model", False)
                assert body["chat_template_kwargs"] == {"enable_thinking": False}
                messages = body["messages"]
                assert messages and all(m["role"] in {"system", "user"} and m["content"] for m in messages)
                if index == 0:
                    fragments = json_fragments(messages)
                    for field in ("current", "history", "allowed_actions", "observations", "routing_hints"):
                        assert item["details"][field] in fragments, (name, field, messages)
                else:
                    first = seen[0]["body"]["messages"]
                    assert messages[:-1] == first, (name, messages)
                    feedback = messages[-1]
                    assert feedback["role"] == "user"
                    assert feedback["content"].startswith("前回の返答は、内容の採否以前に現行JSON契約へ一致しなかった。")
                    assert "現行JSON Schema:" in feedback["content"]
                    assert json.loads(rows[0]["text"]) in json_fragments([feedback]), feedback
                    assert got["validation_errors"][0], got
                    assert all(error in feedback["content"] for error in got["validation_errors"][0]), feedback
            for attempt in got["attempts"]:
                assert attempt["generated"]["completion_tokens"] == 19
                assert attempt["generated"]["prompt_tokens"] == 1234
            results.append({"name": name, "calls": got["calls"], "result": got["result"]})
    report = {"passed": len(results), "cases": results, "model_calls": 0, "temporary_servers_stopped": len(results)}
    path = ROOT / "reports/planner-transport.json"; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__": main()
