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

from test_support import base, LINES, ALTERNATIVES
from storage_fixture import isolated_storage

ROOT = Path(__file__).resolve().parents[2]


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


def run(folder, url, *options, stop_signal=None, max_rounds=6):
    poem_input = {**base(), "max_regeneration_rounds": max_rounds}
    config = {"input": poem_input, "chat": {"base_url": url + "/chat/v1", "model": "chat-checker", "max_tokens": 72, "timeout_ms": 5000},
              "haiku": {"base_url": url + "/haiku/v1", "model": "verse-generator", "max_tokens": 192, "timeout_ms": 5000}}
    path = folder / "request.json"; path.write_text(json.dumps(config, ensure_ascii=False))
    # 実workerは使うが生成は模擬HTTP。親の認証・採取・保存設定を継承しない。
    _, env = isolated_storage(folder, None, ())
    command = [str(ROOT / "target/debug/examples/generate_haiku"), str(path), "--python", sys.executable, *options]
    if stop_signal is None:
        result = subprocess.run(command, text=True, capture_output=True, env=env, cwd=folder, timeout=15)
    else:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=folder) as process:
            try:
                time.sleep(1.5)
                assert process.poll() is None, "generation ended before stop signal"
                process.send_signal(stop_signal)
                stdout, stderr = process.communicate(timeout=10)
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
    pids = re.findall(r'python_worker_started.*?pid=Some\((\d+)\)', result.stderr)
    assert len(pids) == 1, result.stderr
    assert "python_worker_stopped" in result.stderr, result.stderr
    for pid in pids:
        try: os.kill(int(pid), 0)
        except ProcessLookupError: pass
        else: raise AssertionError(f"helper {pid} is still running")
    return result


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix="dogido-haiku-") as temp:
        folder = Path(temp)
        # 合格行は一回の初稿で採用する。模擬HTTPと実workerで、会話側への採点要求がなく句用の予算・終了理由が記録されることを確かめる。
        with mock([(json.dumps({"lines": LINES}, ensure_ascii=False), "length")]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert value["result"]["accepted"] and value["result"]["text"].splitlines() == LINES
            assert value["result"]["regeneration_rounds"] == 0 and len(seen) == 1
            assert seen[0]["path"] == "/haiku/v1/chat/completions"
            assert seen[0]["body"]["max_tokens"] == 192 and seen[0]["body"]["temperature"] == .6
            assert seen[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}
            assert value["reports"][0]["generated"]["finish_reason"] == "length"
            assert value["reports"][0]["generated"]["completion_tokens"] == 512
            checks.append("draft_only_routes_budget_and_usage")
        # 中七だけが短ければ、上五・下五を固定する。二回目の模擬要求の対象が中七だけで、採用本文の他の二行が不変なら合格。
        retry = {"lines": [{"line_index": 1, "text": ALTERNATIVES[1]}]}
        with mock([{"lines": [LINES[0], "ひ", LINES[2]]}, retry]) as (url, seen):
            process = run(folder, url); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert value["result"]["accepted"] and value["result"]["regeneration_rounds"] == 1
            assert value["result"]["text"].splitlines() == [LINES[0], ALTERNATIVES[1], LINES[2]]
            assert value["requests"][1]["details"]["failed_line_indices"] == [1]
            assert [r["body"]["temperature"] for r in seen] == [.6, .3]
            assert all(r["path"] == "/haiku/v1/chat/completions" for r in seen)
            checks.append("only_short_middle_line_regenerated")
        # 再生成枠0の通信失敗は初稿1件で終わる。実HTTPの失敗とプロセス回収を調べる。
        with mock(["http_error"]) as (url, seen):
            process = run(folder, url, max_rounds=0); assert process.returncode == 0, process.stderr
            value = json.loads(process.stdout)
            assert not value["result"]["accepted"] and value["result"]["failure_reason"] == "max_regeneration_rounds"
            assert len(seen) == 1
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
    path = ROOT / "reports/python-worker-check.json"; path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"checks": checks, "passed": len(checks)}, ensure_ascii=False, indent=2) + "\n")
    print(f"haiku mock HTTP: {len(checks)} passed; own helpers and listeners stopped")


if __name__ == "__main__": main()
