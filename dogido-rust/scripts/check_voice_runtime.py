#!/usr/bin/env python3
"""Rust音声入力の実プロセス／HTTP試験。録音・実モデル・TTSは使わない。"""
import argparse
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from test_support import assert_voice_diagnostic


def wait(predicate, description, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate(): return
        time.sleep(.02)
    raise AssertionError(f"待機失敗: {description}")


def running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


class Harness:
    def __init__(self, binary):
        self.binary = binary
        self.requests = []
        self.options = {}
        self.errors = []
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def response(self, value, status=200):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try: self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError): pass
            def do_GET(self):
                parent.requests.append((self.path, None, self.headers.get("Authorization")))
                self.response({"prompt_mode": parent.options.get("mode", "normal")}, parent.options.get("context_status", 200))
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                parent.requests.append((self.path, value, self.headers.get("Authorization")))
                if self.path.endswith("diagnostics"):
                    try: assert_voice_diagnostic(value)
                    except Exception as error:
                        parent.errors.append(str(error))
                        return self.response({"error": "invalid diagnostic"}, 400)
                    if parent.options.get("slow_diagnostics"): time.sleep(2)
                    self.response({"accepted": True})
                else:
                    self.response(parent.options.get("delivery", {"accepted": True, "session_id": "test-session"}), parent.options.get("delivery_status", 200))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def deliveries(self): return [v for path, v, _ in self.requests if path.endswith("player-input")]
    def diagnostics(self, reason): return [v for path, v, _ in self.requests if v and v.get("reason") == reason and path.endswith("diagnostics")]

    @contextlib.contextmanager
    def case(self, name, **options):
        self.requests = []
        self.errors = []
        self.options = options
        with tempfile.TemporaryDirectory(prefix="dogido-voice-test-") as directory:
            folder = Path(directory)
            config = folder / "case.json"
            config.write_text(json.dumps({"folder": str(folder), **options}))
            stub = ROOT / "dogido-rust/fixtures/voice_stub.py"
            for role in ("whisper", "vad"):
                target = folder / role
                target.write_text(f"#!{sys.executable}\n" + stub.read_text())
                target.chmod(0o700)
            model = folder / "fake-model.bin"
            model.touch()
            settings = dict(capture_command=[sys.executable, str(stub), "capture"],
                whisper_cli=str(folder / "whisper"), whisper_model=str(model),
                vad={"cli": str(folder / "vad"), "model": str(model), "threshold": .5} if options.get("vad") else None,
                base_url=f"http://127.0.0.1:{self.server.server_port}", rms_threshold=700, silence_ms=800,
                minimum_ms=350, maximum_ms=30000, max_pending=1, max_age_sec=options.get("max_age", 8),
                no_speech_threshold=.6, retry_threshold=options.get("retry_threshold", 1.0),
                wake_word=options.get("wake_word", ""), normal_prompt="通常会話", workshop_prompt="川柳推敲", use_gpu=True)
            env = {**os.environ, "DOGIDO_VOICE_TEST_CONFIG": str(config), "DOGIDO_AUTH_TOKEN": "test-token", "TMPDIR": str(folder)}
            command = [str(self.binary), "voice-input", "--settings", json.dumps(settings)]
            if options.get("check"): command.append("--check")
            with (folder / "runtime.log").open("w+") as output:
                process = subprocess.Popen(command, env=env, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                def child_log():
                    path = folder / "children.jsonl"
                    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
                try:
                    yield process, folder, child_log
                except BaseException:
                    output.flush(); output.seek(0)
                    print(f"FAIL {name}\n{output.read()}")
                    raise
                finally:
                    if process.poll() is None: process.send_signal(signal.SIGTERM)
                    try: process.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                        raise AssertionError(f"{name}: Rust入力プロセスが終了しない")
                    pids = {event.get("child_pid", event["pid"]) for event in child_log()}
                    wait(lambda: not any(running(pid) for pid in pids), f"{name}: 所有する子の回収", timeout=3)
                    assert not list(folder.glob("dogido-voice-*.wav")), f"{name}: WAVが残った"
                    assert all(auth == "Bearer test-token" for _, _, auth in self.requests)
                    assert not self.errors, self.errors
            print(f"OK {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "dogido-rust/target/debug/dogido-rust")
    args = parser.parse_args()
    h = Harness(args.binary.resolve())
    count = 0
    try:
        with h.case("non-recording-check", check=True) as (p, folder, logs):
            assert p.wait(timeout=5) == 0
            assert not logs() and not h.requests
        count += 1
        for name, options in [
            ("basic-partial-pipe-reads", {}), ("workshop-prompt", {"mode": "haiku_workshop"}),
            ("empty-only-retry", {"empty_first": True}), ("bad-utf8-log", {"bad_log": True}),
            ("nonzero-with-valid-text", {"whisper_exit": 3}), ("silero-pass", {"vad": True}),
            ("silero-fail-open", {"vad": True, "vad_failure": True}),
            ("context-failure-fallback", {"context_status": 503}),
            ("server-rejection", {"delivery": {"accepted": False, "reason": "select_one_session"}}),
            ("http-conflict-rejection", {"delivery_status": 409, "delivery": {"accepted": False, "reason": "select_one_session"}}),
        ]:
            with h.case(name, **options) as (p, folder, logs):
                wait(lambda: len(h.deliveries()) == 1, name)
                assert "模擬録音" in h.diagnostics("aec_started")[0]["detail"]
                assert h.deliveries() == [{"text": "ドギド 900", "source": "voice"}]
                calls = [v for v in logs() if v["role"] == "whisper" and v["event"] == "started"]
                assert len(calls) == (2 if options.get("empty_first") else 1)
                first = calls[0]["args"]
                assert first[first.index("--prompt") + 1] == ("川柳推敲" if options.get("mode") else "通常会話")
                if len(calls) == 2:
                    retry = calls[1]["args"]
                    assert retry[retry.index("--prompt") + 1] == ""
                    assert float(retry[retry.index("--no-speech-thold") + 1]) == 1.0
                assert "--no-gpu" not in first
                if name in {"server-rejection", "http-conflict-rejection"}: wait(lambda: h.diagnostics("select_one_session"), name)
            count += 1
        for name, options, reason in [
            ("noise-no-retry", {"output": "[00 --> 01] Thank you!"}, "known_noise_text"),
            ("invalid-text-no-retry", {"bad_text": True}, "whisper_invalid_utf8"),
            ("wake-word", {"wake_word": "別の呼び名"}, "wake_word_missing"),
            ("silero-reject", {"vad": True, "vad_count": 0}, "no_speech_segment"),
            ("empty-retry-disabled", {"empty_first": True, "retry_threshold": None}, "no_result"),
        ]:
            with h.case(name, **options) as (p, folder, logs):
                wait(lambda: h.diagnostics(reason), name)
                assert not h.deliveries()
                calls = [v for v in logs() if v["role"] == "whisper" and v["event"] == "started"]
                assert len(calls) == (0 if name == "silero-reject" else 1)
            count += 1
        with h.case("latest-pending-only", extra_segments=[1000, 1100], first_delay=.5) as (p, folder, logs):
            wait(lambda: len(h.deliveries()) == 2, "latest input")
            assert [v["text"] for v in h.deliveries()] == ["ドギド 900", "ドギド 1100"]
            assert h.diagnostics("pending_segment_replaced")
        count += 1
        with h.case("stale-pending-dropped", extra_segments=[1000], first_delay=1.5, max_age=1) as (p, folder, logs):
            wait(lambda: h.diagnostics("stale_segment_dropped"), "stale input")
            assert len(h.deliveries()) == 1
        count += 1
        for sig in (signal.SIGINT, signal.SIGTERM):
            with h.case(f"cancel-active-stt-{sig.name}", hang_whisper=True) as (p, folder, logs):
                wait(lambda: (folder / "whisper-started").exists(), "recognition started")
                p.send_signal(sig)
                assert p.wait(timeout=5) == 0
                assert not h.deliveries()
            count += 1
        for name, options in [("partial-eof", {"partial_only": True}),
            ("eof-cancels-stt", {"eof_during_stt": True, "hang_whisper": True}),
            ("stalled-capture-cancels-stt", {"stall_capture": True, "hang_whisper": True})]:
            with h.case(name, **options) as (p, folder, logs):
                assert p.wait(timeout=7) != 0
                assert not h.deliveries()
                assert h.diagnostics("aec_failed")
            count += 1
        with h.case("slow-diagnostic-does-not-block-capture", slow_diagnostics=True) as (p, folder, logs):
            wait(lambda: len(h.deliveries()) == 1, "delivery while diagnostics blocked", timeout=2)
        count += 1
    finally:
        h.close()
    report = {"cases": count, "passed": count, "real_microphone": False, "real_model": False, "owned_processes_remaining": 0}
    report_path = ROOT / "dogido-rust/reports/voice-runtime.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))

if __name__ == "__main__": main()
