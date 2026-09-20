#!/usr/bin/env python3
"""Rust接続サーバーを一時ポートで確認し終了する。モデル・TTSへは接続しない。"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

# app/serviceはimportしない。型検査と副作用のないHUD投影だけを比較する。
from dogido_server.models import (
    AdapterSessionCreateRequest, AdapterSessionCreateResponse,
    CloseSessionResponse, HeartbeatResponse, HealthResponse,
)
from dogido_server.haiku.hud import project_workshop

TOKEN = "connection-test-only"


@contextmanager
def server(binary: Path, log_path: Path, stop_signal=signal.SIGTERM):
    env = dict(os.environ, DOGIDO_AUTH_TOKEN=TOKEN)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(binary), "serve", "--listen", "127.0.0.1:0"],
            stdout=subprocess.PIPE, stderr=log, text=True, env=env,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=10), "server did not announce its address"
                ready_line = process.stdout.readline()
                assert ready_line, f"server exited before listening:\n{log_path.read_text()}"
                ready = json.loads(ready_line)
            assert ready["event"] == "server_listening" and ready["llm_enabled"] is False
            host, port = ready["address"].rsplit(":", 1)
            assert host == "127.0.0.1" and int(port) >= 1024
            # 既存サービスの固定ポートには接続しない。
            assert int(port) not in {5055, 5056, 8080}
            yield host, int(port)
        finally:
            if process.poll() is None:
                process.send_signal(stop_signal)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    raise AssertionError("server failed to stop gracefully")
            process.stdout.close()
        assert process.returncode == 0, log_path.read_text()
    log_text = log_path.read_text()
    assert "server_stopped" in log_text, "worker shutdown was not recorded"
    assert TOKEN not in log_text, "auth token leaked into logs"


def request(address, method, path, body=None, *, authorized=True, session_id=None):
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if session_id:
        headers["X-Dogido-Session-Id"] = session_id
    with closing(HTTPConnection(*address, timeout=3)) as connection:
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        assert response.getheader("Cache-Control") == "no-store"
        return response.status, raw if path == "/dogido" else json.loads(raw)


def register(address):
    payload = {
        "adapter_name": "dogido-fabric-client", "adapter_version": "migration-test",
        "schema_version": "2026-05-24", "player_name": "接続試験",
        "capabilities": ["player_state", "workshop_display.v1"],
        "execution_capabilities": ["client.hotbar.select.v1"], "adapter_build": "extra-field",
    }
    AdapterSessionCreateRequest.model_validate(payload)
    status, body = request(address, "POST", "/api/v1/adapter-sessions", payload)
    assert status == 201
    parsed = AdapterSessionCreateResponse.model_validate(body)
    assert parsed.heartbeat_interval_ms == 5000 and parsed.max_batch_size == 25
    assert parsed.accepted_schema_version == "2026-05-24"
    return parsed.session_id


def hud(address, session_id, sequence):
    status, body = request(address, "GET", f"/api/v1/haiku-workshop/snapshot?session_id={session_id}")
    assert status == 200
    expected = project_workshop(SimpleNamespace(
        session_id=session_id, last_sequence=sequence, haiku_workshop=None,
        machine=SimpleNamespace(haiku_thinking_depth=0, state=SimpleNamespace(mode="normal")),
    ))
    assert {key: value for key, value in body.items() if key != "revision"} == expected
    assert type(body["revision"]) is int
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/dogido-rust")
    args = parser.parse_args()
    binary = args.binary.resolve()
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-rust-server-") as directory:
        log1, log2 = (Path(directory) / name for name in ("server1.log", "server2.log"))
        with server(binary, log1) as address:
            status, body = request(address, "GET", "/healthz", authorized=False)
            assert status == 200 and HealthResponse.model_validate(body).ok
            assert body["dialogue_ready"] is False and body["llm_enabled"] is False
            passed.append("health_reports_connection_only")
            assert request(address, "GET", "/api/v1/display/snapshot", authorized=False)[0] == 401
            passed.append("bearer_auth")
            status, html = request(address, "GET", "/dogido", authorized=False)
            assert status == 200 and "会話・警告・音声はまだ使えません" in html
            passed.append("existing_display_page")
            old_id = register(address)
            passed.append("python_session_response_contract")
            before = hud(address, old_id, 0)
            assert hud(address, old_id, 0) == before
            passed.append("python_closed_hud_and_read_only_revision")
            status, heartbeat = request(address, "POST", f"/api/v1/adapter-sessions/{old_id}/heartbeat",
                {"last_sequence": 17, "sent_at": datetime.now(timezone.utc).isoformat()})
            assert status == 200 and HeartbeatResponse.model_validate(heartbeat).ok
            assert hud(address, old_id, 17)["revision"] > before["revision"]
            passed.append("heartbeat_updates_sequence")
            status, snapshot = request(address, "GET", "/api/v1/display/snapshot")
            assert status == 200 and snapshot["minecraft"]["connected"] is True
            assert snapshot["runtime"]["runtime_environment"] == "Rust"
            assert snapshot["diagnostics"] and snapshot["utterances"] == []
            passed.append("runtime_and_diagnostics_snapshot")
            status, body = request(address, "POST", "/api/v1/game-events", {"sequence": 18}, session_id=old_id)
            assert status == 501 and body["accepted"] is False
            assert hud(address, old_id, 17)["observed_sequence"] == 17
            status, body = request(address, "POST", "/api/v1/player-input", {"text": "こんにちは", "source": "voice"})
            assert status == 501 and body["accepted"] is False
            passed.append("unfinished_processing_not_acknowledged")
        passed.append("sigterm_drains_and_stops_worker")
        with server(binary, log2, signal.SIGINT) as address:
            assert request(address, "GET", f"/api/v1/haiku-workshop/snapshot?session_id={old_id}")[0] == 404
            status, body = request(address, "POST", "/api/v1/game-events", {}, session_id=old_id)
            assert status == 409 and body == {"detail": {"code": "unknown_session_id", "session_id": old_id}}
            passed.append("restart_invalidates_old_session")
            current_id = register(address)
            assert current_id != old_id
            hud(address, current_id, 0)
            passed.append("fabric_style_reregistration")
            status, body = request(address, "DELETE", f"/api/v1/adapter-sessions/{current_id}")
            assert status == 200 and CloseSessionResponse.model_validate(body).ok
            assert request(address, "GET", f"/api/v1/haiku-workshop/snapshot?session_id={current_id}")[0] == 404
            passed.append("close_removes_snapshot")
        assert "adapter_session_created" in log1.read_text()
        passed.append("ctrl_c_drains_and_stops_worker")
    report = {"schema_version": 1, "passed": passed, "scope": "connection_server_only",
              "model_calls": 0, "tts_calls": 0, "test_servers_stopped": True}
    output = ROOT / "reports/server-check.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"PASS: {len(passed)} real HTTP/lifecycle checks; all test servers stopped; no model or TTS calls")


if __name__ == "__main__":
    main()
