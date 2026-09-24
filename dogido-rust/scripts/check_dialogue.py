#!/usr/bin/env python3
"""実HTTP・模擬LLM/TTS/playerで通常会話の所有権と後始末を確認する。実マイクなし。"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit
import wave

ROOT = Path(__file__).resolve().parents[1]


def request(base, path, body=None, method=None, sid=None):
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-Dogido-Session-Id"] = sid
    req = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(),
                                 headers, method=method)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def register(base, preview=True):
    return request(base, "/api/v1/adapter-sessions", {
        "adapter_name": "rust-conversation-preview" if preview else "fabric-test",
        "adapter_version": "test", "game": "none" if preview else "minecraft-java",
        "schema_version": "2026-05-24", "player_name": "試験"})["session_id"]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError("condition timed out")


def snapshot(base):
    return request(base, "/api/v1/rust-dialogue/snapshot")


def row(base, turn, statuses):
    return next((r for r in snapshot(base)["utterances"]
                 if r["turn_id"] == turn and r["playback_status"] in statuses), None)


def submit(base, sid, text="こんにちは"):
    got = request(base, "/api/v1/player-input", {"text": text, "source": "voice", "session_id": sid})
    assert got["accepted"], got
    return got["turn_id"]


@contextmanager
def dependencies():
    controls = {"delay": 0, "fail_tts": False, "leaf": "こんにちは。話しかけてくれてうれしいわ。",
                "tts_gates": {}, "fail_sentence": None}
    seen = []
    wav = io.BytesIO()
    with wave.open(wav, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(b"\0\0" * 2400)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            incoming = json.loads(body) if body else {}
            seen.append({"path": self.path, "body": incoming})
            status, mime = 200, "application/json"
            if self.path == "/v1/chat/completions":
                time.sleep(controls["delay"])
                if incoming["max_tokens"] == 640:
                    # 現行prompt中のcurrentだけを読む。テストが入力にないevidenceを作らない。
                    decoder = json.JSONDecoder()
                    current = None
                    for m in incoming["messages"]:
                        for match in re.finditer(r'\{\s*"turn_id"\s*:\s*"current"', m["content"]):
                            candidate, _ = decoder.raw_decode(m["content"][match.start():])
                            if "text" in candidate: current = candidate
                    assert current, incoming
                    text = json.dumps({"action": "continue_conversation", "focus": "会話を続ける",
                        "entity_query": "", "evidence": [{"turn_id": "current", "quote": current["text"]}],
                        "confidence": .95}, ensure_ascii=False)
                else:
                    text = controls["leaf"]
                raw = json.dumps({"id": "mock", "object": "chat.completion", "created": 0,
                    "model": "mock-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}).encode()
            elif self.path.startswith("/audio_query?"):
                text = parse_qs(urlsplit(self.path).query)["text"][0]
                raw = json.dumps({"test_text": text}).encode()
                if controls["fail_tts"]: status = 503
            elif self.path.startswith("/synthesis?"):
                raw, mime = wav.getvalue(), "audio/wav"
                text = incoming["test_text"]
                gate = controls["tts_gates"].get(text)
                if gate is not None and not gate.wait(timeout=12):
                    status = 504
                if text == controls["fail_sentence"]:
                    status = 503
            else:
                status, raw = 404, b'{}'
            try:
                self.send_response(status); self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}); t.start()
    try: yield f"http://127.0.0.1:{server.server_port}", controls, seen
    finally:
        for gate in controls["tts_gates"].values(): gate.set()
        server.shutdown(); server.server_close(); t.join(timeout=3); assert not t.is_alive()


@contextmanager
def running(binary, directory, dependency, *, live=False, player=None):
    log_path = directory / "runtime.log"
    env = dict(os.environ)
    env.pop("DOGIDO_AUTH_TOKEN", None); env.pop("DOGIDO_LLM_API_KEY", None)
    if player is None:
        player = directory / "player"
        player.write_text(f"#!{sys.executable}\nimport time,sys\nfrom pathlib import Path\np=Path(__file__).with_name('player_mode')\nmode=p.read_text() if p.exists() else 'ok'\ntime.sleep(4 if mode=='slow' else .15)\nsys.exit(1 if mode=='fail' else 0)\n")
        player.chmod(0o700)
    with log_path.open("w") as log:
        p = subprocess.Popen([str(binary), "serve-dialogue", "--listen", "127.0.0.1:0", "--python", sys.executable,
            "--base-url", dependency + "/v1", "--voicevox-url", "http://127.0.0.1:50021" if live else dependency,
            "--audio-player", str(player), "--audio-dir", str(directory / "audio")],
            stdout=subprocess.PIPE, stderr=log, text=True, env=env)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=8), "no server startup"
                line = p.stdout.readline(); assert line, log_path.read_text()
            ready = json.loads(line)
            assert ready["phase"] == "dialogue_preview"
            yield "http://" + ready["address"], p, log_path
        finally:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
                try: p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill(); p.wait(); raise AssertionError("owned server failed to stop")
            p.stdout.close()
        assert p.returncode == 0, log_path.read_text()
    logs = log_path.read_text()
    assert "dialogue_stopped" in logs
    # 起動したhelperとplayerの実PIDが終了していることをOSへ確認。
    pids = set(re.findall(r'event="(?:helper_started|audio_started)" pid=Some\((\d+)\)', logs))
    assert pids, logs
    for pid in pids:
        try: os.kill(int(pid), 0)
        except ProcessLookupError: continue
        raise AssertionError(f"owned child still alive: {pid}")
    assert not list((directory / "audio").glob("*.wav")), "temporary playback file remains"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    args = parser.parse_args()
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-dialogue-") as temp, dependencies() as (dep, control, seen):
        directory = Path(temp)
        with running(args.binary.resolve(), directory, dep) as (base, process, log):
            sid = register(base)
            assert request(base, "/api/v1/voice-input/context")["session_id"] == sid
            turn = submit(base, sid)
            reply = wait_for(lambda: row(base, turn, {"completed"}))
            assert reply["text"] == control["leaf"], (reply, log.read_text())
            assert reply["llm_reports"][0]["result"] == "accepted", reply
            assert [r["role"] for r in snapshot(base)["sessions"][0]["history"]] == ["user", "assistant"]
            assert len([r for r in seen if r["path"] == "/v1/chat/completions"]) == 2, seen
            assert [r["generated"]["finish_reason"] for r in reply["llm_reports"] if "generated" in r] == ["stop"]
            passed.append("one_planner_one_leaf_completed_history")
            first_tts = len([r for r in seen if r["path"].startswith("/synthesis")])
            turn = submit(base, sid, "うん、ありがとう")
            wait_for(lambda: row(base, turn, {"completed"}))
            assert len([r for r in seen if r["path"].startswith("/synthesis")]) == first_tts
            assert control["leaf"] in json.dumps(seen[-1]["body"], ensure_ascii=False)
            passed.append("completed_history_in_next_prompt_and_lazy_audio_cache")
            (directory / "player_mode").write_text("fail")
            turn = submit(base, sid, "そうなんだね")
            wait_for(lambda: row(base, turn, {"failed"}))
            assert len(snapshot(base)["sessions"][0]["history"]) == 5
            passed.append("failed_playback_excluded_from_history")
            (directory / "player_mode").write_text("slow")
            turn = submit(base, sid, "少し話そう")
            wait_for(lambda: row(base, turn, {"started"}))
            request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
            wait_for(lambda: row(base, turn, {"cancelled"}))
            assert len(snapshot(base)["sessions"][0]["history"]) == 6
            passed.append("cancelled_playback_excluded_and_player_reaped")
            (directory / "player_mode").write_text("ok")
            control["delay"] = .6
            turn = submit(base, sid, "ゆっくり聞いて")
            duplicate = submit(base, sid, "ゆっくり聞いて")
            assert duplicate == turn
            time.sleep(.2)
            new_turn = submit(base, sid, "次の話にしよう")
            wait_for(lambda: row(base, turn, {"cancelled"}))
            wait_for(lambda: row(base, new_turn, {"completed"}))
            # 取消途中の同文を、現在の入力と取り違えて重複扱いしない。
            old = submit(base, sid, "もう一度話すね")
            middle = submit(base, sid, "別のことだった")
            newest = submit(base, sid, "もう一度話すね")
            assert newest != old
            wait_for(lambda: row(base, old, {"cancelled"}))
            wait_for(lambda: row(base, middle, {"cancelled"}))
            wait_for(lambda: row(base, newest, {"completed"}))
            passed.append("duplicate_input_and_superseded_generation")
            control["delay"] = 0
            turn = submit(base, sid, "剣に持ち替えて")
            wait_for(lambda: row(base, turn, {"unsupported"}))
            passed.append("world_operations_not_routed_to_chat")
            control["fail_tts"] = True; control["leaf"] = "そうやな。また話しかけてな。"
            turn = submit(base, sid, "またね")
            wait_for(lambda: row(base, turn, {"failed"}))
            control["fail_tts"] = False
            passed.append("tts_error_visible_without_false_completion")
            control["delay"] = .6
            turn = submit(base, sid, "接続を切り直すよ")
            time.sleep(.15)
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            wait_for(lambda: row(base, turn, {"cancelled"}))
            sid = register(base, preview=False)
            assert snapshot(base)["sessions"][0]["history"] == []
            passed.append("session_close_cancels_and_new_session_has_no_history")
            assert not request(base, "/api/v1/player-input", {"text": "こんにちは"})["accepted"]
            def event(seq, stale=False, dark=False):
                return {"schema_version":"2026-05-24", "adapter":"fabric", "sequence":seq,
                    "observed_at": (datetime.now(timezone.utc)-timedelta(seconds=30 if stale else 0)).isoformat(),
                    "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                    "world":{"danger_darkness_score": .9 if dark else .0}}
            request(base, "/api/v1/game-events", event(1, stale=True), sid=sid)
            assert not request(base, "/api/v1/player-input", {"text": "こんにちは"})["accepted"]
            request(base, "/api/v1/game-events", event(2), sid=sid)
            assert request(base, "/api/v1/game-events", event(2), sid=sid)["deduplicated"]
            turn = submit(base, sid, "こんにちは")
            request(base, "/api/v1/game-events", event(3, dark=True), sid=sid)
            wait_for(lambda: row(base, turn, {"cancelled"}))
            passed.append("fresh_observation_required_and_danger_cancels")
            request(base, "/api/v1/game-events", event(4), sid=sid)
            control["delay"] = 0
            (directory / "player_mode").write_text("slow")
            stopping_turn = submit(base, sid, "停止を確認するよ")
            wait_for(lambda: row(base, stopping_turn, {"started"}))
        assert re.search(r'turn_id="'+stopping_turn+r'" playback_status="cancelled"', log.read_text()), log.read_text()
        passed.append("shutdown_joins_helpers_players_and_removes_wav")
    output = ROOT / "reports/dialogue-check.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({"passed": passed, "model":"mock", "tts":"mock", "playback":"mock", "all_owned_processes_stopped":True}, ensure_ascii=False, indent=2)+"\n")
    print(f"PASS {len(passed)} dialogue/lifecycle checks; all owned processes stopped")


if __name__ == "__main__": main()
