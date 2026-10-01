#!/usr/bin/env python3
"""既存Python providerとRust/Rigの実HTTP送受信を、モデルなしで比較する。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

import httpx

from dogido_server.config import Settings
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.providers import generate_chat_completions_text
from dogido_server.llm.types import LeafGenerationRequest, StructuredGenerationRequest


def response(content, *, finish="stop", usage=True):
    body = {
        "id": "comparison-only",
        "model": "synthetic-model",
        "object": "chat.completion",
        "created": 0,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 1234, "completion_tokens": 19, "total_tokens": 1253}
    return body


def cases():
    fixtures = json.loads((ROOT.parent / "tests/fixtures/shared_llm_requests.json").read_text())
    result = []
    for case in fixtures:
        kwargs = dict(kind=case["kind"], details=case["details"], temperature=case["temperature"],
                      route=case["route"], max_tokens=case["max_tokens"])
        request = (LeafGenerationRequest(fallback_text=case["fallback"], **kwargs)
                   if case["request_type"] == "leaf" else
                   StructuredGenerationRequest(fallback_value=case["fallback"], **kwargs))
        messages = build_messages(request)
        assert case["prompt_fragment"] in messages[1]["content"]
        content = case["response"]
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        result.append((case["id"], {
            "schema_version": 1, "kind": case["kind"], "model": "default_model",
            "messages": messages, "temperature": case["temperature"],
            "max_tokens": case["max_tokens"], "enable_thinking": False,
        }, response(content)))
    base = deepcopy(result[0][1])
    grounding = deepcopy(base)
    grounding.update(kind="haiku_line_grounding", max_tokens=512)
    truncated = response('{"verdicts":[true,false,true],"failures":[', finish="length")
    truncated["usage"].update(completion_tokens=512, total_tokens=1746)
    result.append(("truncated_grounding_512", grounding, truncated))
    result.append(("missing_usage", base, response("確認できたで。", usage=False)))
    result.append(("text_parts", base, response([
        {"type": "text", "text": "そうやな。"}, {"type": "text", "text": ""},
        {"type": "text", "text": "帰ろか。"}
    ])))
    reasoning = response("")
    reasoning["choices"][0]["message"]["reasoning_content"] = "確認できたで。"
    result.append(("reasoning_fallback", base, reasoning))
    history = deepcopy(base)
    history["messages"] = [
        {"role": "system", "content": "短く話して。"},
        {"role": "developer", "content": "原文の日本語と改行を保持。"},
        {"role": "user", "content": "猫の話やで。\n🐈"},
        {"role": "assistant", "content": "猫のことやな。"},
        {"role": "user", "content": "そうそう。"},
    ]
    result.append(("history_roles_unicode", history, response("猫の話、聞いてるで。")))
    return result


def python_reference(request, body):
    seen = []

    def handler(incoming):
        assert incoming.url.path == "/v1/chat/completions"
        seen.append(json.loads(incoming.content))
        return httpx.Response(200, json=body)

    client = httpx.Client
    def factory(*args, **kwargs):
        return client(*args, transport=httpx.MockTransport(handler), **kwargs)

    settings = Settings(
        _env_file=None, llm_enabled=True, llm_backend="chat_completions", llm_provider="local",
        llm_base_url="http://127.0.0.1:8080/v1", llm_model=request["model"], llm_api_key="",
        llm_max_tokens=request["max_tokens"], audio_enabled=False, memory_enabled=False,
    )
    with patch("dogido_server.llm.providers.httpx.Client", side_effect=factory):
        result = generate_chat_completions_text(settings, request["messages"],
                                                temperature=request["temperature"])
    assert len(seen) == 1
    # 終了情報を返さない旧Python版でも本文・送信比較は実行できる。
    # その場合は終了情報だけfixtureとの照合とし、Python比較済みとは記録しない。
    metadata = {
        "finish_reason": body["choices"][0].get("finish_reason"),
        "completion_tokens": body.get("usage", {}).get("completion_tokens"),
        "prompt_tokens": body.get("usage", {}).get("prompt_tokens"),
    }
    metadata_compared = all(hasattr(result, key) for key in metadata)
    if metadata_compared:
        actual_metadata = {key: getattr(result, key) for key in metadata}
        assert actual_metadata == metadata, (actual_metadata, metadata)
        metadata = actual_metadata
    return seen[0], {"text": str(result), **metadata}, metadata_compared


@contextmanager
def mock_endpoint(body, *, status=200, delay=0):
    seen = []
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append({"path": self.path,
                         "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                         "authorization": self.headers.get("Authorization")})
            time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # timeout試験ではクライアントが先に閉じる。

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive(), "comparison server did not stop"


def run(binary, request, endpoint, *, timeout_ms=2000, api_key=None):
    env = dict(os.environ)
    env.pop("DOGIDO_LLM_API_KEY", None)
    if api_key:
        env["DOGIDO_LLM_API_KEY"] = api_key
    with tempfile.TemporaryDirectory(prefix="dogido-rig-comparison-") as directory:
        path = Path(directory) / "request.json"
        path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        return subprocess.run([str(binary), "generate", str(path), "--base-url", endpoint,
                               "--timeout-ms", str(timeout_ms)],
                              env=env, capture_output=True, text=True, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/provider-comparison.json")
    args = parser.parse_args()
    binary = args.binary.resolve()
    assert binary.is_file(), f"build first: {binary}"
    passed = []
    metadata_comparisons = []
    positive = cases()
    for name, request, body in positive:
        expected_wire, expected_result, metadata_compared = python_reference(request, body)
        key = "comparison-only-key" if name == "history_roles_unicode" else None
        with mock_endpoint(body) as (endpoint, seen):
            completed = run(binary, request, endpoint, api_key=key)
        assert completed.returncode == 0, (name, completed.stderr)
        assert len(seen) == 1, (name, "unexpected retry", len(seen))
        assert seen[0]["path"] == "/v1/chat/completions", seen[0]["path"]
        assert seen[0]["body"] == expected_wire, (name, "wire differs", seen[0]["body"], expected_wire)
        assert seen[0]["authorization"] == f"Bearer {key or 'dogido-local-no-key'}"
        report = json.loads(completed.stdout)
        assert report["generated"] == expected_result, (name, report["generated"], expected_result)
        assert report["response_model"] == body["model"]
        assert report["requested_model"] == request["model"]
        assert report["response_id"] == body["id"]
        if metadata_compared:
            metadata_comparisons.append(name)
        passed.append(name)

    request = positive[0][1]
    for name, body, status, delay, timeout_ms in [
        ("http_500_no_retry", {"error": "synthetic"}, 500, 0, 2000),
        ("http_429_no_retry", {"error": "synthetic"}, 429, 0, 2000),
        ("invalid_json", b'{"choices":', 200, 0, 2000),
        ("empty_content", response(" "), 200, 0, 2000),
        ("timeout_no_retry", response("遅延応答"), 200, 0.5, 150),
    ]:
        with mock_endpoint(body, status=status, delay=delay) as (endpoint, seen):
            completed = run(binary, request, endpoint, timeout_ms=timeout_ms)
        assert completed.returncode != 0, name
        assert not completed.stdout.strip(), (name, "error was reported as success")
        assert len(seen) == 1, (name, "unexpected retry", len(seen))
        passed.append(name)

    invalid = deepcopy(request)
    invalid["max_tokens"] = 0
    with mock_endpoint(response("未送信であること")) as (endpoint, seen):
        completed = run(binary, invalid, endpoint)
    assert completed.returncode != 0 and not seen, "invalid request reached the provider"
    passed.append("invalid_request_no_network")

    report = {"schema_version": 1, "scope": "provider_transport_only", "passed": passed,
              "python_metadata_comparisons": metadata_comparisons,
              "model_calls": 0, "comparison_servers_stopped": True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(passed)} provider comparisons; model calls=0; all test servers stopped")


if __name__ == "__main__":
    main()
