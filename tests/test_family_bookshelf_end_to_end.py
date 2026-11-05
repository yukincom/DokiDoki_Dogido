"""見込み: 既存Rust保存をTLS中継の先で使えば、原句を残して両画面の現在句が一致する。
合格: 元句バイト不変、編集再送は一履歴、子の削除403、親からの削除と復元が往復する。
範囲: 隔離した実Rust・実TLS・実HTTP。Bonjourと実Mac画面は別確認。実モデルは起動しない。
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts/lan"), str(ROOT / "dogido-rust/tests/python")]
from storage_fixture import isolated_storage
from start_dogido_library import parse_bootstrap
import bookshelf_remote
import bookshelf_bridge as bridge
from test_lan_transport import serving
from test_lan_bookshelf_bridge import world


def request_http(url, path, method="GET", value=None, token=None):
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=20)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Dogido-Library"] = token
    connection.request(method, path, None if value is None else json.dumps(value), headers)
    response = connection.getresponse(); body = response.read()
    result = response.status, body
    connection.close()
    return result


@contextmanager
def rust_shelf(root, binary):
    settings, environment = isolated_storage(root, {}, [])
    memory = Path(settings["memory_dir"])
    records = memory / "long_term/haiku_entries.jsonl"
    records.parent.mkdir(parents=True)
    original = {"id": "fixture", "kind": "player_haiku", "author": "player",
                "text": "あさのやま / とりのこえする / もりのみち", "created_at": "2026-10-13T00:00:00Z",
                "world": {"biome": "forest", "time_phase": "day"}}
    records.write_text(json.dumps(original, ensure_ascii=False) + "\n")
    config = {"memory": str(memory), "photos": str(root / "photos"),
              "screenshots": settings["screenshot_dir"], "capture_dir": settings["capture_dir"],
              "ui": str(ROOT / "tools/dogido-library"), "child_mode": True, "admin_token": "fixture-admin"}
    process = subprocess.Popen([str(binary), "serve-bookshelf", "--config", json.dumps(config), "--port", "0"],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # URLは改行済みの一行として読む。起動できない場合に無期限待ちしない。
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not selector.select(1):
                    continue
                line = process.stdout.readline().decode()
                if line.startswith("DOGIDO_LIBRARY_URL="):
                    url = line.strip().split("=", 1)[1]
                    break
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode())
            else:
                raise AssertionError("isolated shelf startup timeout")
        yield url, memory, records
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()


# 見込み: 中継を通しても同一の元句・版・UUID照合を使う。合格: 同じ編集を二回送っても一履歴で原句不変。
# 子の拒否・親削除・復元も実Rustで確認し、親側には本文や画像を永続保存しない。Bonjourだけ模擬する。
def test_parent_child_roundtrip_preserves_original_and_single_revision(world, monkeypatch, tmp_path):
    binary = os.environ.get("DOGIDO_TEST_BOOKSHELF_BINARY")
    if not binary:
        pytest.skip("隔離試験用にビルド済みRust実行ファイルを明示してください")
    credentials = world.pair()
    broker = world.host.bookshelf
    host_state = world.root / "host"
    initial_files = set(host_state.iterdir())
    with rust_shelf(tmp_path / "storage", Path(binary)) as (local_url, memory, records):
        original = records.read_bytes()
        status, body = request_http(local_url, "/bootstrap.js")
        assert status == 200
        child = parse_bootstrap(body)
        assert not child["can_delete"] and not child["show_readings"]
        child_token = child["csrf"]
        stop = threading.Event()
        errors = []
        def pump():
            while not stop.is_set():
                try:
                    request = bridge._exchange(world.address, credentials, bridge.NEXT, {})["request"]
                    if request:
                        result = bridge.forward_local(local_url, "fixture-admin", child_token, request)
                        bridge._exchange(world.address, credentials, bridge.RESULT, result)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                stop.wait(.01)
        worker = threading.Thread(target=pump, daemon=True); worker.start()
        try:
            broker.poll(credentials["device"])
            with bridge.serve_host(host_state, broker), \
                    serving(bookshelf_remote.make_server(ROOT, device=credentials["device"], state_dir=host_state)) as address:
                parent_url = f"http://127.0.0.1:{address[1]}"
                status, body = request_http(parent_url, "/bootstrap.js")
                assert status == 200
                parent = parse_bootstrap(body)
                assert parent["can_delete"] and not parent["show_readings"] and parent["parent_view"]
                assert parent["csrf"] != child_token
                poem = parent["poems"][0]
                edit = {"id": poem["id"], "requestId": str(uuid.uuid4()), "expectedRevisionId": None,
                        "expectedLines": poem["lines"], "lines": ["ひるのやま", "とりのこえする", "もりのみち"]}
                for _ in range(2):
                    status, body = request_http(parent_url, "/api/poems/current", "PUT", edit, parent["csrf"])
                    assert status == 200, body
                revision = memory / "long_term/haiku_revisions.jsonl"
                assert len(revision.read_text().splitlines()) == 1
                assert records.read_bytes() == original
                status, body = request_http(local_url, "/api/library")
                assert status == 200 and json.loads(body)["poems"][0]["lines"] == edit["lines"]
                trash = {"kind": "poem", "id": poem["id"], "restore": False}
                assert request_http(local_url, "/api/trash", "PUT", trash, child_token)[0] == 403
                assert request_http(parent_url, "/api/trash", "PUT", trash, parent["csrf"])[0] == 200
                assert json.loads(request_http(local_url, "/api/library")[1])["poems"] == []
                trash["restore"] = True
                assert request_http(parent_url, "/api/trash", "PUT", trash, parent["csrf"])[0] == 200
                assert len(json.loads(request_http(local_url, "/api/library")[1])["poems"]) == 1
                assert records.read_bytes() == original
                assert len(revision.read_text().splitlines()) == 1
        finally:
            stop.set(); worker.join(timeout=8)
        assert not errors
    assert set(host_state.iterdir()) == initial_files
