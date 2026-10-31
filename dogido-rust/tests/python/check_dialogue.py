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

from request_classifier_fixture import mock_request_classifier, resolve_input_response

ROOT = Path(__file__).resolve().parents[2]


def request(base, path, body=None, method=None, sid=None, *, auto_voice_generation=True):
    # この模擬入力は直前のcontextを録音開始時として扱う。明示した古い世代は上書きしない。
    # 欠落世代の負例ではauto_voice_generation=Falseを指定し、実際に欠落したまま送る。
    if (auto_voice_generation and path == "/api/v1/player-input" and isinstance(body, dict)
            and body.get("source") == "voice" and "voice_generation" not in body):
        context = request(base, "/api/v1/voice-input/context")
        selected = body.get("session_id") or sid or context.get("session_id")
        if selected == context.get("session_id") and context.get("voice_generation") is not None:
            body = {**body, "voice_generation": context["voice_generation"]}
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-Dogido-Session-Id"] = sid
    req = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(),
                                 headers, method=method)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def register(base, preview=True, *, voice_enabled=None, body=False):
    text_mode = ({} if voice_enabled is None else
                 {"capabilities": ["dialogue_text.v1"], "voice_enabled": voice_enabled})
    return request(base, "/api/v1/adapter-sessions", {
        "adapter_name": "rust-conversation-preview" if preview else "fabric-test",
        "adapter_version": "test", "game": "none" if preview else "minecraft-java",
        "schema_version": "2026-05-24", "player_name": "試験", **text_mode,
        **({"player_uuid": "11111111-1111-4111-8111-111111111111"} if body else {})})["session_id"]


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


def submit(base, sid, text="こんにちは", *, source="voice"):
    got = request(base, "/api/v1/player-input", {"text": text, "source": source, "session_id": sid})
    assert got["accepted"], got
    # 警告中の通常質問/知識は元IDのまま保留・自動再開する。
    # 専用入力へ振り分けられた場合は、明示された配送先を追う。
    got = resolve_input_response(base, got)
    while got.get("reason") == "reply_input_routing":
        routing = got
        def classified():
            item = next((r for r in snapshot(base)["utterances"] if r["turn_id"] == routing["turn_id"]), None)
            if not item: return None
            if "forwarded_input" in item: return item["forwarded_input"]
            if item["playback_status"] in {"waiting_for_warning", "generating", "queued", "started", "completed", "failed", "cancelled"}:
                return {**routing, "reason":item["playback_status"]}
            return None
        got = resolve_input_response(base, wait_for(classified))
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
                structured = (controls["structured_handler"](incoming) if "structured_handler" in controls
                              else controls.get("structured", {}).get(incoming["max_tokens"]))
                if structured is not None:
                    text = json.dumps(structured, ensure_ascii=False)
                else:
                    text = controls["leaf"]
                    if any('"action":"silent"' in m["content"] for m in incoming["messages"] if m["role"] == "system"):
                        text = json.dumps({"action":"speak","speech":text}, ensure_ascii=False)
                message = (structured if isinstance(structured, dict) and structured.get("role") == "assistant"
                           and "tool_calls" in structured else {"role": "assistant", "content": text})
                raw = json.dumps({"id": "mock", "object": "chat.completion", "created": 0,
                    "model": "mock-model", "choices": [{"index": 0, "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}).encode()
                if "completion_override" in controls:
                    actual = controls["completion_override"](incoming)
                    if actual is not None:
                        raw = json.dumps(actual, ensure_ascii=False).encode()
                if incoming.get("stream"):
                    # 同じ模擬応答をSSEへ配送。gateは最初の確定文の後だけを止められる。
                    response = json.loads(raw)
                    complete = response["choices"][0]
                    content = complete["message"].get("content", "")
                    chunks = controls.get("stream_chunks", [content])
                    gate = controls.get("stream_gate")
                    try:
                        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Connection", "close"); self.end_headers()
                        def event(delta, *, finish=None, usage=None):
                            frame = {"id":response.get("id", "mock"), "object":"chat.completion.chunk", "created":0,
                                "model":response.get("model", "mock-model"),
                                "choices":[{"index":0,"delta":delta,"finish_reason":finish}]}
                            if usage is not None: frame["usage"] = usage
                            self.wfile.write(("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode())
                            self.wfile.flush()
                        event({"reasoning_content":"この内容は発声しない。"})
                        for index, chunk in enumerate(chunks):
                            if index == 1 and gate is not None: gate.wait(timeout=12)
                            event({"content":chunk})
                        if not controls.get("stream_truncate"):
                            event({}, finish=complete.get("finish_reason", "stop"), usage=response.get("usage"))
                            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
                        controls["stream_finished"] = True
                    except (BrokenPipeError, ConnectionResetError): pass
                    self.close_connection = True
                    return
            elif self.path.startswith("/audio_query?"):
                text = parse_qs(urlsplit(self.path).query)["text"][0]
                raw = json.dumps({"test_text": text}).encode()
                if controls["fail_tts"]: status = 503
            elif self.path.startswith("/synthesis?"):
                raw, mime = wav.getvalue(), "audio/wav"
                text = incoming["test_text"]
                gate = controls["tts_gates"].get(text)
                if gate is None and controls.get("tts_gate_selector") is not None:
                    gate = controls["tts_gate_selector"](text)
                    if gate is not None:
                        # Preserve finalizer release even if the selecting test fails.
                        controls["tts_gates"][text] = gate
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
        if controls.get("stream_gate") is not None: controls["stream_gate"].set()
        server.shutdown(); server.server_close(); t.join(timeout=3); assert not t.is_alive()


@contextmanager
def running(binary, directory, dependency, *, live=False, player=None, warning_settings=None, combat_settings=None, haiku_settings=None, output_sampling_rate=None, extra_args=()):
    log_path = directory / "runtime.log"
    env = dict(os.environ)
    env.pop("DOGIDO_AUTH_TOKEN", None); env.pop("DOGIDO_LLM_API_KEY", None)
    env.pop("DOGIDO_LLM_HAIKU_API_KEY", None)
    if player is None:
        player = directory / "player"
        player.write_text(f"#!{sys.executable}\nimport time,sys\nfrom pathlib import Path\np=Path(__file__).with_name('player_mode')\nmode=p.read_text() if p.exists() else 'ok'\ntime.sleep(4 if mode=='slow' else .15)\nsys.exit(1 if mode=='fail' else 0)\n")
        player.chmod(0o700)
    with log_path.open("w") as log:
        p = subprocess.Popen([str(binary), "serve-dialogue", "--listen", "127.0.0.1:0", "--python", sys.executable,
            "--base-url", dependency + "/v1", "--voicevox-url", "http://127.0.0.1:50021" if live else dependency,
            "--audio-player", str(player), "--audio-dir", str(directory / "audio"),
            "--warning-settings", json.dumps(warning_settings or {}),
            "--combat-settings", json.dumps(combat_settings or {}),
            "--haiku-settings", json.dumps(haiku_settings or {}),
            *(["--output-sampling-rate", str(output_sampling_rate)] if output_sampling_rate is not None else []), *extra_args],
            stdout=subprocess.PIPE, stderr=log, text=True, env=env)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=8), "no server startup"
                line = p.stdout.readline(); assert line, log_path.read_text()
            ready = json.loads(line)
            assert ready["phase"] == "dialogue"
            yield "http://" + ready["address"], p, log_path
        except BaseException:
            print(log_path.read_text()[-6000:], file=sys.stderr)
            raise
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
            pids = set(re.findall(r'event="(?:helper_started|python_worker_started|audio_started)" pid=Some\((\d+)\)', logs))
            # Native fixed/disabled turns may own no child at all.
            for pid in pids:
                try: os.kill(int(pid), 0)
                except ProcessLookupError: continue
                raise AssertionError(f"owned child still alive: {pid}")
            assert not list((directory / "audio").glob("*.wav")), "temporary playback file remains"


def submit_text(base, sid, text):
    return submit(base, sid, text, source="text")


def display_row(base, turn):
    return next((r for r in snapshot(base)["utterances"]
                 if r["turn_id"] == turn and r.get("display_id")), None)


def acknowledge(base, sid, reply):
    result = request(base, "/api/v1/rust-dialogue/displayed",
                     {"session_id": sid, "display_ids": [reply["display_id"]]})
    assert result["displayed"] == [reply["display_id"]] and not result["rejected"], result


def check_text_delivery(binary, passed):
    with tempfile.TemporaryDirectory(prefix="dogido-text-dialogue-") as temp, dependencies() as (dep, control, seen):
        directory = Path(temp)
        with running(binary, directory, dep, haiku_settings={"enabled": False, "memory_enabled": False}) as (base, process, log):
            sid = register(base, voice_enabled=False)
            def history():
                return next(s["history"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
            def mode(enabled):
                result = request(base, "/api/v1/rust-dialogue/voice", {"session_id": sid, "enabled": enabled})
                assert result["accepted"], result
                return result["voice_generation"]
            turn = submit_text(base, sid, "こんにちは")
            reply = wait_for(lambda: display_row(base, turn))
            assert reply["output_mode"] == "text" and not reply["text_displayed"], reply
            assert [r["role"] for r in history()] == ["user"], history()
            acknowledge(base, sid, reply)
            acknowledge(base, sid, reply)
            delivered = wait_for(lambda: row(base, turn, {"audio_disabled"}))
            assert delivered["text_displayed"] and delivered["delivery_confirmed"], delivered
            assert [r["role"] for r in history()] == ["user", "assistant"], history()
            assert not any(r["path"].startswith(("/audio_query", "/synthesis")) for r in seen), seen
            passed.append("text_ack_commits_history_once_without_tts_or_fake_playback")

            turn = submit_text(base, sid, "うん、ありがとう")
            reply = wait_for(lambda: display_row(base, turn))
            prompt = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"][-1]
            assert control["leaf"] in json.dumps(prompt, ensure_ascii=False), prompt
            acknowledge(base, sid, reply)
            wait_for(lambda: row(base, turn, {"audio_disabled"}))
            passed.append("text_history_reaches_next_prompt")

            old = submit_text(base, sid, "少し話そう")
            old_reply = wait_for(lambda: display_row(base, old))
            new = submit_text(base, sid, "次の話にしよう")
            result = request(base, "/api/v1/rust-dialogue/displayed",
                             {"session_id": sid, "display_ids": [old_reply["display_id"]]})
            assert result["rejected"] == [old_reply["display_id"]], result
            wait_for(lambda: row(base, old, {"cancelled"}))
            acknowledge(base, sid, wait_for(lambda: display_row(base, new)))
            wait_for(lambda: row(base, new, {"audio_disabled"}))
            assert not any(r["turn_id"] == old + ":reply" for r in history()), history()
            passed.append("superseded_text_ack_cannot_enter_history")

            generation = mode(True)
            for supplied in (None, "old-generation"):
                body = {"session_id": sid, "source": "voice", "text": "聞こえるかな"}
                if supplied is not None: body["voice_generation"] = supplied
                rejected = request(base, "/api/v1/player-input", body, auto_voice_generation=False)
                assert not rejected["accepted"] and rejected["reason"] == "stale_voice_generation", rejected
            (directory / "player_mode").write_text("slow")
            voice = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice",
                            "text": "聞こえるかな", "voice_generation": generation})
            assert voice["accepted"], voice
            turn = voice["turn_id"]
            reply = wait_for(lambda: display_row(base, turn))
            acknowledge(base, sid, reply)
            wait_for(lambda: row(base, turn, {"started"}))
            state = next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)
            assert [r["text"] for r in state["voice_inputs"]] == ["聞こえるかな"], state
            mode(False)
            cancelled = wait_for(lambda: row(base, turn, {"cancelled"}))
            assert cancelled["text_displayed"] and cancelled["delivery_confirmed"], cancelled
            assert len([r for r in history() if r["turn_id"] == turn + ":reply"]) == 1, history()
            rejected = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice",
                               "text": "古い認識", "voice_generation": generation})
            assert not rejected["accepted"] and rejected["reason"] == "voice_disabled", rejected
            assert mode(True) != generation
            passed.append("voice_generation_echo_and_mode_cancel_preserve_displayed_reply")

            (directory / "player_mode").write_text("ok")
            turn = submit_text(base, sid, "もう一度こんにちは")
            reply = wait_for(lambda: display_row(base, turn))
            acknowledge(base, sid, reply)
            completed = wait_for(lambda: row(base, turn, {"completed"}))
            acknowledge(base, sid, completed)
            assert len([r for r in history() if r["turn_id"] == turn + ":reply"]) == 1, history()
            passed.append("audio_and_text_completion_share_one_history_entry")

            control["fail_tts"] = True
            control["leaf"] = "またゆっくり話そな。"
            turn = submit_text(base, sid, "別の話をしよう")
            reply = wait_for(lambda: display_row(base, turn))
            acknowledge(base, sid, reply)
            failed = wait_for(lambda: row(base, turn, {"failed"}))
            assert failed["text_displayed"] and failed["delivery_confirmed"], failed
            assert len([r for r in history() if r["turn_id"] == turn + ":reply"]) == 1, history()
            passed.append("tts_failure_preserves_confirmed_text_history")

            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            sid = register(base, preview=False, voice_enabled=False, body=True)
            # 環境警告は現在の共通モデル経路。配送検査の本文は模擬モデルから返す。
            control["leaf"] = "触るとあちちやで！"
            audio_before = len([r for r in seen if r["path"].startswith(("/audio_query", "/synthesis"))])
            from body_senses_fixture import attach_body
            request(base, "/api/v1/game-events", attach_body({
                "schema_version": "2026-05-24", "adapter": "fixture", "sequence": 1,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                "player": {"name": "試験", "dimension": "minecraft:overworld", "health": 20},
                "world": {"weather": "clear", "biome": "plains", "sky_visible": True,
                          "ceiling_height": 20, "local_light": 15, "time_phase": "day",
                          "nearby_damaging_light_source_count": 1, "nearest_damaging_light_source_distance": 1.73},
                "combat": {"combat_active_hint": False}}, activity="holding"), sid=sid)
            warning = wait_for(lambda: next((r for r in snapshot(base)["utterances"]
                               if r["session_id"] == sid and r.get("display_id")
                               and r.get("text") == "触るとあちちやで！"), None))
            assert not history(), history()
            acknowledge(base, sid, warning)
            acknowledge(base, sid, warning)
            wait_for(lambda: row(base, warning["turn_id"], {"audio_disabled"}))
            assert len([r for r in history() if r["text"] == warning["text"]]) == 1, history()
            assert len([r for r in seen if r["path"].startswith(("/audio_query", "/synthesis"))]) == audio_before
            passed.append("environment_warning_text_ack_enters_history_once_without_tts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    parser.add_argument("--text-only", action="store_true", help="テキスト配送の追加確認だけ実行")
    args = parser.parse_args()
    passed = []
    check_text_delivery(args.binary.resolve(), passed)
    if args.text_only:
        print(json.dumps({"passed": passed, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))
        return
    with tempfile.TemporaryDirectory(prefix="dogido-dialogue-") as temp, dependencies() as (dep, control, seen), mock_request_classifier({"剣に持ち替えて":"equip_sword"}) as (bert_args, _):
        directory = Path(temp)
        with running(args.binary.resolve(), directory, dep, extra_args=bert_args) as (base, process, log):
            sid = register(base)
            assert request(base, "/api/v1/voice-input/context")["session_id"] == sid
            turn = submit(base, sid)
            reply = wait_for(lambda: row(base, turn, {"completed", "failed"}))
            assert reply["playback_status"] == "completed", (reply, log.read_text())
            assert reply["text"] == control["leaf"], (reply, log.read_text())
            assert reply["dialogue_outcome"] == "accepted", reply
            assert [r["role"] for r in snapshot(base)["sessions"][0]["history"]] == ["user", "assistant"]
            assert len([r for r in seen if r["path"] == "/v1/chat/completions"]) == 1, seen
            assert [r["generated"]["finish_reason"] for r in reply["llm_reports"] if "generated" in r] == ["stop"]
            passed.append("one_generation_completed_history")
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
            # BERTの剣依頼も現在観測を受けてから既存assistへ進む。能力不足だけを検査する。
            request(base, "/api/v1/game-events", {
                "schema_version": "2026-05-24", "adapter": "fixture", "sequence": 1,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "event": {"name": "status_snapshot", "source_kind": "system",
                          "priority_hint": "background", "certainty": "high"},
                "player": {"name": "試験"},
                "world": {"sky_visible": True, "ceiling_height": 20,
                          "local_light": 15, "time_phase": "day"}}, sid=sid)
            before = len([r for r in seen if r["path"] == "/v1/chat/completions"])
            accepted = request(base, "/api/v1/player-input", {"session_id":sid, "source":"voice", "text":"剣に持ち替えて"})
            routed = resolve_input_response(base, accepted)
            assert routed["reason"] == "assist_input" and routed["assist"]["detail_code"] == "capability_missing", routed
            assert routed["assist"]["intent"]["source"] == "modernbert" and routed["assist"]["command"] is None, routed
            assert len([r for r in seen if r["path"] == "/v1/chat/completions"]) == before
            wait_for(lambda: any(r["playback_status"] == "completed" and r.get("text") == "今のアダプターでは、まだ持ち替え操作が使えへんで。" for r in snapshot(base)["utterances"]))
            passed.append("bert_sword_request_checks_capability_without_chat_or_world_command")
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
            def event(seq, stale=False, danger=False):
                return {"schema_version":"2026-05-24", "adapter":"fabric", "sequence":seq,
                    "observed_at": (datetime.now(timezone.utc)-timedelta(seconds=30 if stale else 0)).isoformat(),
                    "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                    "combat":{"recent_damage_ms": 0 if danger else 10000}}
            request(base, "/api/v1/game-events", event(1, stale=True), sid=sid)
            assert not request(base, "/api/v1/player-input", {"text": "こんにちは"})["accepted"]
            request(base, "/api/v1/game-events", event(2), sid=sid)
            assert request(base, "/api/v1/game-events", event(2), sid=sid)["deduplicated"]
            turn = submit(base, sid, "こんにちは")
            # 被弾でpanicになっても、緊急警告actionのないフレームでは通常回答を切らない。
            request(base, "/api/v1/game-events", event(3, danger=True), sid=sid)
            wait_for(lambda: row(base, turn, {"completed"}))
            passed.append("fresh_observation_required_and_combat_mode_keeps_reply")
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            sid = register(base, preview=False)
            control["delay"] = 0
            focused = event(1)
            focused["world"] = {"biome":"desert", "sky_visible":True, "local_light":15, "time_phase":"day"}
            focused["look_target"] = {"kind":"block", "name":"cactus", "distance":2.0}
            request(base, "/api/v1/game-events", focused, sid=sid)
            control["leaf"] = "尖っていて緑色やな。"
            before = len([r for r in seen if r["path"] == "/v1/chat/completions"])
            turn = submit(base, sid, "これは何かな")
            reply = wait_for(lambda: row(base, turn, {"completed", "failed"}))
            assert reply["playback_status"] == "completed" and reply["text"] == control["leaf"], reply
            assert len([r for r in seen if r["path"] == "/v1/chat/completions"]) - before == 1
            assert snapshot(base)["sessions"][0]["history"][-1]["text"] == control["leaf"]
            passed.append("crosshair_in_single_request_without_code_rewriting_reply")
            generated = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"][-1]
            assert "サボテン" in json.dumps(generated, ensure_ascii=False)
            control["leaf"] = "そうやな。また話しかけてな。"
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
