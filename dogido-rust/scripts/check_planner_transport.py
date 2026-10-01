#!/usr/bin/env python3
"""Rigと実HTTPでplannerの再試行上限・timeout・終了情報を確認。モデルは呼ばない。"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time

from planner_cases import ROOT, cases, repair_payload
from compare_provider import response
from compare_planner import normal_payload
from dogido_server.config import Settings
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import GeneratedText, StructuredGenerationRequest
from dogido_server.dialogue.player_chat_planner import _parse_model_plan


@contextmanager
def endpoint(responses):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            incoming = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "body": incoming})
            row = responses[min(len(seen) - 1, len(responses) - 1)]
            time.sleep(row.get("delay", 0))
            raw = json.dumps(response(row["text"], finish=row.get("finish", "stop")), ensure_ascii=False).encode()
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


def python_reference(item, rows):
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions",
                        llm_provider="local", llm_model="default_model", audio_enabled=False, memory_enabled=False)
    client = DogidoLLM(settings)
    seen = []
    def backend(request):
        seen.append(build_messages(request))
        row = rows[min(len(seen) - 1, len(rows) - 1)]
        if row.get("status", 200) != 200 or row.get("delay", 0): raise RuntimeError("synthetic transport failure")
        return GeneratedText(row["text"], finish_reason=row.get("finish", "stop"), completion_tokens=19, prompt_tokens=1234)
    client._generate_backend_text = backend
    fallback = item["fallback"]
    value = client.generate_structured_json(StructuredGenerationRequest(kind="player_chat_plan", details=item["details"],
            fallback_value={k: fallback[k] for k in ("action", "focus", "entity_query", "evidence", "confidence")},
            temperature=0.0, route="chat", max_tokens=640))
    parsed = _parse_model_plan(value, item["details"])
    if parsed:
        result = asdict(parsed); result["presence_challenged"] = fallback["presence_challenged"]
    else: result = fallback
    return json.loads(json.dumps(result)), seen


def main():
    logging.disable(logging.CRITICAL)
    fixtures = cases()
    normal = normal_payload(fixtures["casual"]["details"])
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
            item = fixtures[fixture]
            want_plan, want_messages = python_reference(item, rows)
            path.write_text(json.dumps(item, ensure_ascii=False))
            with endpoint(rows) as (url, seen):
                done = subprocess.run([str(binary), "plan-chat", str(path), "--base-url", url, "--timeout-ms", "60" if name == "timeout_no_retry" else "2000"], capture_output=True, text=True, env=env, timeout=8)
            assert done.returncode == 0, (name, done.stderr)
            got = json.loads(done.stdout)
            assert got["result"] == status, (name, got)
            assert got["calls"] == len(seen) == len(want_messages), (name, got, seen)
            assert got["plan"] == want_plan, (name, got["plan"], want_plan)
            for index, sent in enumerate(seen):
                body = sent["body"]
                assert sent["path"] == "/v1/chat/completions"
                assert (body["max_tokens"], body["temperature"], body["model"], body["stream"]) == (640, 0, "default_model", False)
                assert body["chat_template_kwargs"] == {"enable_thinking": False}
                if name == "shape_retry_once" and index == 1:
                    # Serdeの型エラー名だけ異なる。prompt本文・schema・入力は同じ。
                    actual_last = body["messages"][-1]["content"].splitlines()
                    expected_last = want_messages[index][-1]["content"].splitlines()
                    assert actual_last[:1] + actual_last[2:] == expected_last[:1] + expected_last[2:]
                    assert body["messages"][:-1] == want_messages[index][:-1]
                else: assert body["messages"] == want_messages[index], name
            for attempt in got["attempts"]:
                assert attempt["generated"]["completion_tokens"] == 19
                assert attempt["generated"]["prompt_tokens"] == 1234
            results.append({"name": name, "calls": got["calls"], "result": got["result"]})
    report = {"passed": len(results), "cases": results, "model_calls": 0, "temporary_servers_stopped": len(results)}
    path = ROOT / "reports/planner-transport.json"; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__": main()
