"""見込み: 既存保存先とRust writerを共有すれば正本を増やさず本棚を分けられる。
合格条件: 子は既存memoryを使い、親の中継は固定宛先・自分のCSRFだけを受ける。
範囲: 起動設定と実loopback HTTP。Rust保存と実MacのUIは別の確認で扱う。
"""
import http.client
import json
from pathlib import Path
import sys
import signal
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import start_dogido_library as launch
import bookshelf_remote


class LauncherTests(unittest.TestCase):
    # 見込み: 子でも共有プロファイルの設定先を直接使う。合格: 同じmemory、権限token、複製設定なし。
    # 範囲: 設定の組立だけ。既存の本体や本番保存先は起動・更新しない。
    def test_child_uses_existing_memory_and_shared_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            memory = root / "existing-memory"; memory.mkdir()
            binary = root / "dogido-rust/target/release/dogido-rust"
            binary.parent.mkdir(parents=True); binary.touch()
            settings = SimpleNamespace(memory_dir=memory, memory_enabled=True, bind_port=5055,
                                       capture_dir=None, minecraft_screenshots_dir=None, primary_storage_env=lambda: {})
            with patch.object(launch, "ROOT", root), patch.object(launch.os, "chdir"), \
                    patch.object(launch, "get_settings", return_value=settings), \
                    patch.object(launch, "child_session", return_value=0) as child, \
                    patch.dict(launch.os.environ, {}, clear=True), \
                    patch.object(sys, "argv", ["launch", "--profile", "child"]):
                with self.assertRaises(SystemExit) as result:
                    launch.main()
                self.assertEqual(result.exception.code, 0)
                command, environment, token = child.call_args.args
                config = json.loads(command[command.index("--config") + 1])
                self.assertEqual(config["memory"], str(memory.resolve()))
                self.assertTrue(config["child_mode"])
                self.assertEqual(config["admin_token"], token)
                self.assertGreaterEqual(len(token), 32)
                self.assertEqual(environment["DOGIDO_ENV_PROFILE"], "shared")
                self.assertNotIn("data_repository", config)
                self.assertEqual(list(memory.iterdir()), [])

    # 見込み: 保存先不在を空の本棚として作らない。合格: 起動前に停止し、ディレクトリ未作成。
    # 範囲: launcher。SSD切断の実機動作は別確認。
    def test_missing_memory_never_creates_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Path(folder) / "missing"
            with patch.object(launch.os, "chdir"), \
                    patch.object(launch, "get_settings", return_value=SimpleNamespace(memory_dir=memory)), \
                    patch.object(sys, "argv", ["launch"]):
                with self.assertRaisesRegex(SystemExit, "保存先"):
                    launch.main()
                self.assertFalse(memory.exists())


    # 見込み: アプリ終了でも実行中のRust writerを強制停止しない。合格: SIGTERMを受けても子へSIGINTを送り完了待ちする。
    # 範囲: 子プロセスの信号契約を模擬する。実保存往復は別のRust統合試験。
    def test_child_close_uses_graceful_interrupt_and_waits(self):
        handlers = {}; sent = []; waits = []
        process = SimpleNamespace(status=None)
        def lines():
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            yield from ()
        def wait():
            waits.append(True); process.status = 0; return 0
        process.stdout = lines(); process.poll = lambda: process.status
        process.send_signal = sent.append; process.wait = wait
        bridge = SimpleNamespace(serve_child=lambda *_: None)
        with patch.dict(sys.modules, {"bookshelf_bridge": bridge}), \
                patch("subprocess.Popen", return_value=process), \
                patch("signal.signal", side_effect=lambda sig, handler: handlers.__setitem__(sig, handler)):
            self.assertEqual(launch.child_session(["test-only"], {}, "test-token"), 0)
        self.assertEqual(sent, [signal.SIGINT])
        self.assertGreaterEqual(len(waits), 1)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ui = self.root / "tools/dogido-library"; ui.mkdir(parents=True)
        (ui / "index.html").write_text("<html>isolated shelf</html>")
        (ui / "app.js").write_text("// isolated")
        self.calls = []
        self.offline = False
        self.devices = [{"id": "a" * 16, "name": "child", "online": True}]
        def request(state, device, method, path, body):
            self.calls.append((device, method, path, body))
            if self.offline:
                return 503, "application/json", b'{"detail":"offline"}'
            if path == "/bootstrap.js":
                boot = {"csrf": "remote-secret", "catalog": [], "poems": [], "child_mode": True,
                        "can_delete": True, "show_readings": False}
                return 200, "text/javascript", ("window.DOGIDO_BOOTSTRAP=" + json.dumps(boot) + ";").encode()
            return 200, "application/json", b'{"ok":true}'
        bridge = SimpleNamespace(list_remote=lambda _: self.devices, request_remote=request)
        with patch.dict(sys.modules, {"bookshelf_bridge": bridge}):
            self.server = bookshelf_remote.make_server(self.root, state_dir=self.root / "host")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse(); data = response.read(); status = response.status
        conn.close()
        return status, data

    # 見込み: 親窓専用のCSRFなら他のlocalhostページから変更されない。合格: 子tokenは出ず不正要求403。
    # 範囲: 実loopback HTTP・模擬家庭内仲介。ファイル複製がないことも確認する。
    def test_proxy_replaces_csrf_and_protects_writes_without_persistence(self):
        before = sorted(str(p) for p in self.root.rglob("*"))
        status, body = self.request("/bootstrap.js")
        self.assertEqual(status, 200)
        self.assertNotIn(b"remote-secret", body)
        token = launch.parse_bootstrap(body)["csrf"]
        self.assertEqual(self.request("/api/trash", "PUT", body="{}")[0], 403)
        self.assertEqual(self.request("/api/trash", "PUT", {"X-Dogido-Library": token}, "{}")[0], 200)
        self.assertEqual(self.request("/api/library", headers={"Origin": "http://other.test"})[0], 403)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))

    # 見込み: 一度選んだ息子の宛先は接続状況で変えない。合格: 登録一覧が変化しても同じIDへ送る。
    # 範囲: 実HTTPと模擬登録一覧。兄弟の追加で別の句を変更しないための検査。
    def test_destination_stays_fixed_after_first_selection(self):
        self.request("/bootstrap.js")
        self.devices = [{"id": "b" * 16, "name": "other", "online": True}]
        self.request("/api/library")
        self.assertTrue(all(call[0] == "a" * 16 for call in self.calls))

    # 見込み: 複数候補では他の家族へ勝手に接続しない。合格: 503、仲介要求なし、説明ページで再試行可。
    # 範囲: 模擬候補と実HTTP。新規ペアリングはしない。
    def test_ambiguous_destination_stays_unselected(self):
        self.devices.append({"id": "b" * 16, "name": "other", "online": True})
        self.assertEqual(self.request("/api/library")[0], 503)
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("もう一度つなぐ", page.decode())
        self.assertEqual(self.calls, [])


    # 見込み: 初期HTML後の切断でも空の本棚へ進まない。合格: bootstrapは再接続画面へ遷移し、本文は保存しない。
    # 範囲: 実HTTPと模擬切断。ブラウザーの画面遷移は実UIでも確認する。
    def test_disconnect_between_document_and_bootstrap_has_reconnect_page(self):
        self.assertEqual(self.request("/")[0], 200)
        self.offline = True
        status, script = self.request("/bootstrap.js")
        self.assertEqual(status, 200)
        self.assertIn(b"location.replace('/connection-unavailable')", script)
        status, page = self.request("/connection-unavailable")
        self.assertEqual(status, 200)
        self.assertIn("もう一度つなぐ", page.decode())

    # 見込み: UIだけを配るため、相対パスから他ファイルへ出られない。合格: traversal・不正Hostを拒否。
    # 範囲: HTTP静的配信。本番ファイルを使わない。
    def test_static_scope_and_host(self):
        self.assertEqual(self.request("/app.js")[0], 200)
        self.assertEqual(self.request("/../private.js")[0], 404)
        self.assertEqual(self.request("/app.js", headers={"Host": "evil.test"})[0], 403)


if __name__ == "__main__":
    unittest.main()
