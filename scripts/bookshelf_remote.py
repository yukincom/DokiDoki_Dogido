"""親機の本棚窓。表示中の応答だけを中継し、子機の川柳をディスクへ保存しない。"""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import signal
import sys
import threading
from urllib.parse import urlsplit


def make_server(root, port=0, device=None, state_dir=None):
    sys.path.insert(0, str(root / "scripts/lan"))
    from bookshelf_bridge import list_remote, request_remote
    from start_dogido_library import parse_bootstrap
    state = state_dir or Path.home() / "Library/Application Support/Dogido LAN/host"
    ui = root / "tools/dogido-library"
    csrf = secrets.token_urlsafe(32)
    # 宛先は一度確定したら変えない。接続中の別端末への自動切替を防ぐ。
    selected = device
    selection_lock = threading.Lock()

    def remote(method, path, body=b""):
        nonlocal selected
        with selection_lock:
            if selected is None:
                records = list_remote(state)
                if len(records) != 1:
                    raise ValueError("子機を一台指定してください。登録済み端末の設定を確認してください。")
                selected = records[0]["id"]
        return request_remote(state, selected, method, path, body)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def detail(self, code, message):
            self.reply(code, "application/json; charset=utf-8", json.dumps({"detail": message}, ensure_ascii=False).encode())

        def disconnected(self):
            message = "子機の本棚に接続できません。子機で「ドギドの本棚」、こちらで「ドギド共有」を開いてください。"
            page = '<!doctype html><html lang="ja"><meta charset="utf-8"><title>子機用の本棚</title><body style="background:#f8f0df;font:20px sans-serif;padding:48px"><h1>子機用の本棚</h1><p>' + html.escape(message) + '</p><p>保存場所は変えずに待っています。</p><a href="/">もう一度つなぐ</a></body></html>'
            self.reply(200, "text/html; charset=utf-8", page.encode())

        def missing_bootstrap(self):
            # 初期HTMLの後で接続が切れた場合も、空の棚として起動しない。
            self.reply(200, "text/javascript; charset=utf-8", b"window.location.replace('/connection-unavailable');")

        def do_GET(self):
            self.handle_shelf()

        def do_PUT(self):
            self.handle_shelf()

        def do_POST(self):
            self.handle_shelf()

        def do_DELETE(self):
            self.handle_shelf()

        def handle_shelf(self):
            authority = f"127.0.0.1:{self.server.server_port}"
            if (self.headers.get_all("Host") != [authority]
                    or self.headers.get("Origin") not in (None, f"http://{authority}")
                    or self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin")
                    or (self.command != "GET" and self.headers.get("X-Dogido-Library") != csrf)):
                self.detail(403, "この本棚の画面から操作してください。")
                return
            path = urlsplit(self.path)
            if path.query or path.fragment or path.path != self.path:
                self.detail(404, "本棚の項目が見つかりません。")
                return
            try:
                if self.command == "GET" and self.path == "/connection-unavailable":
                    self.disconnected()
                elif self.command == "GET" and self.path == "/":
                    # 接続できない状態で空の句集を表示しない。再試行は読み取りだけ。
                    try:
                        status, _, _ = remote("GET", "/bootstrap.js")
                        if status != 200:
                            raise OSError
                    except (OSError, ValueError, RuntimeError):
                        self.disconnected()
                        return
                    self.reply(200, "text/html; charset=utf-8", (ui / "index.html").read_bytes())
                elif self.command == "GET" and self.path == "/bootstrap.js":
                    status, content_type, body = remote("GET", self.path)
                    if status != 200:
                        self.missing_bootstrap()
                        return
                    if status == 200:
                        boot = parse_bootstrap(body)
                        boot["csrf"] = csrf
                        code = json.dumps(boot, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
                        body = ("window.DOGIDO_BOOTSTRAP=" + code + ";window.DOGIDO_CATALOG_SAMPLE=window.DOGIDO_BOOTSTRAP.catalog;").encode()
                    self.reply(status, content_type, body)
                elif self.path.startswith("/api/"):
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 128 * 1024 or self.headers.get("Transfer-Encoding"):
                        self.detail(413, "一度に保存する内容が大きすぎます。")
                        return
                    body = self.rfile.read(length)
                    self.reply(*remote(self.command, self.path, body))
                elif self.command == "GET":
                    relative = Path(self.path[1:])
                    candidate = ui / relative
                    if (not relative.parts or any(p.startswith(".") for p in relative.parts)
                            or candidate.suffix not in (".js", ".css", ".png")
                            or candidate.is_symlink() or not candidate.is_file()
                            or not candidate.resolve().is_relative_to(ui.resolve())):
                        self.detail(404, "本棚の項目が見つかりません。")
                        return
                    self.reply(200, mimetypes.guess_type(candidate)[0] or "application/octet-stream", candidate.read_bytes())
                else:
                    self.detail(405, "この操作は使えません。")
            except (OSError, ValueError, RuntimeError):
                # 応答喪失時は保存失敗と断定しない。編集欄と操作IDは既存UIが保持する。
                if self.path == "/bootstrap.js":
                    self.missing_bootstrap()
                else:
                    self.detail(503, "子機の本棚から応答を確認できません。接続を確認して、同じ操作をもう一度試してください。")

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(25)
            return connection, address

    return Server(("127.0.0.1", port), Handler)


def run(root, port=0, device=None):
    with make_server(root, port, device) as server:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        print(f"DOGIDO_LIBRARY_URL=http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
