#!/usr/bin/env python3
"""Rig＋川柳エンジンの模擬HTTP検証。既存モデル・音声・Minecraftは使わない。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

from compare_haiku import base, LINES, ALTERNATIVES

ROOT = Path(__file__).resolve().parents[1]


def report(indices=(0, 1, 2), numbers=None):
    return {"verdicts": {str(i): "pass" for i in indices}, "assessments": [
        {"line_index": i, "atom_ids": [(numbers or {}).get(i, i + 1)]} for i in indices], "failure_reasons": {}}


@contextmanager
def mock(script, delay=0):
    seen, errors = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                incoming = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                position = len(seen)
                seen.append({"path": self.path, "body": incoming})
                time.sleep(delay)
                payload = script[position]
                if payload == "http_error":
                    self.send_response(503); self.end_headers(); return
                content, finish = payload if isinstance(payload, tuple) else (json.dumps(payload, ensure_ascii=False), "stop")
                body = json.dumps({"id": f"mock-{position}", "object": "chat.completion", "created": 0,
                    "model": incoming["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                    "finish_reason": finish}], "usage": {"prompt_tokens": 1234, "completion_tokens": 512 if finish == "length" else 32,
                    "total_tokens": 1746 if finish == "length" else 1266}}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            except Exception as error:
                errors.append(str(error)); self.send_error(500)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # テスト自身の処理threadも終了を待つ。
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
    thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        assert not thread.is_alive()
        assert not errors, errors


def run(folder, url, *options, stop_signal=None):
    config = {"input": base(), "chat": {"base_url": url + "/chat/v1", "model": "chat-checker", "max_tokens": 72, "timeout_ms": 5000},
              "haiku": {"base_url": url + "/haiku/v1", "model": "verse-generator", "max_tokens": 192, "timeout_ms": 5000}}
    path = folder / "request.json"; path.write_text(json.dumps(config, ensure_ascii=False))
    env = dict(os.environ)
    for key in ("DOGIDO_LLM_API_KEY", "DOGIDO_HAIKU_API_KEY"): env.pop(key, None)
    command = [str(ROOT / "target/debug/examples/generate_haiku"), str(path), "--python", sys.executable, *options]
    if stop_signal is None:
        result = subprocess.run(command, text=True, capture_output=True, env=env, timeout=15)
    else:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) as process:
            try:
                time.sleep(1.5)
                assert process.poll() is None, "generation ended before stop signal"
                process.send_signal(stop_signal)
                stdout, stderr = process.communicate(timeout=10)
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
    pids = re.findall(r'haiku_helper_started.*?pid=Some\((\d+)\)', result.stderr)
    assert len(pids) == 1, result.stderr
    assert "haiku_helper_stopped" in result.stderr, result.stderr
    for pid in pids:
        try: os.kill(int(pid), 0)
        except ProcessLookupError: pass
        else: raise AssertionError(f"helper {pid} is still running")
    return result


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix="dogido-haiku-") as temp:
        folder = Path(temp)
        prefix = json.dumps(report(), ensure_ascii=False).rsplit('"failure_reasons"', 1)[0] + '"failure_reasons":{"1":"途中'
        with mock([{"lines": LINES}, (prefix, "length")]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert value["result"]["accepted"] and value["result"]["text"].splitlines() == LINES, value
            assert len(seen) == 2
            assert [v["path"] for v in seen] == ["/haiku/v1/chat/completions", "/chat/v1/chat/completions"]
            assert [v["body"]["max_tokens"] for v in seen] == [192, 512]
            assert [v["body"]["temperature"] for v in seen] == [.6, 0.0]
            assert all(v["body"]["chat_template_kwargs"] == {"enable_thinking": False} for v in seen)
            assert value["reports"][1]["generated"]["finish_reason"] == "length"
            assert value["reports"][1]["generated"]["completion_tokens"] == 512
            checks.append("routes_budget_complete_prefix_and_usage")
        with mock([{"lines": LINES}, report((0, 2)), report((1,))]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert value["result"]["accepted"] and value["result"]["regeneration_rounds"] == 0
            assert value["requests"][2]["details"]["grounding_lines"] == [{"line_index": 1, "text": LINES[1]}]
            assert len(seen) == 3
            checks.append("missing_middle_recheck_original")
        with mock([{"lines": LINES}, report((0, 2)), ('{"verdicts":', "length")]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert value["result"]["failure_reason"] == "grounding_unavailable"
            assert value["result"]["regeneration_rounds"] == 0 and len(seen) == 3
            checks.append("repeated_check_failure_never_rewrites")
        negative = report(); negative["verdicts"]["2"] = "japanese_fail"; negative["failure_reasons"] = {"2": "造語になっている。"}
        with mock([{"lines": LINES}, negative, {"lines": [{"line_index": 2, "text": ALTERNATIVES[2]}]}, report((2,), {2: 1})]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert value["result"]["accepted"] and value["result"]["regeneration_rounds"] == 1
            assert value["result"]["text"].splitlines() == LINES[:2] + [ALTERNATIVES[2]]
            assert len(seen) == 4
            checks.append("only_failed_line_regenerated")
        with mock(["http_error"]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert not value["result"]["accepted"] and len(seen) == 1
            checks.append("http_error_no_automatic_transport_retry")
        for option in ("--cancel-after-ms", "--timeout-ms"):
            with mock([{"lines": LINES}], delay=3) as (url, seen):
                process = run(folder, url, option, "1500")
                assert process.returncode != 0 and len(seen) == 1, process.stderr
                assert "cancelled" in process.stderr if option == "--cancel-after-ms" else "timed out" in process.stderr
                checks.append(option[2:] + "_reaps_helper")
        for stop in (signal.SIGINT, signal.SIGTERM):
            with mock([{"lines": LINES}], delay=3) as (url, seen):
                process = run(folder, url, stop_signal=stop)
                assert process.returncode != 0 and "cancelled" in process.stderr and len(seen) == 1, process.stderr
                checks.append(stop.name + "_reaps_helper")
    path = ROOT / "reports/haiku-bridge-check.json"; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"checks": checks, "passed": len(checks)}, ensure_ascii=False, indent=2) + "\n")
    print(f"haiku mock HTTP: {len(checks)} passed; own helpers and listeners stopped")


if __name__ == "__main__": main()
