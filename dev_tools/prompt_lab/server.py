"""Local comparison UI. Candidate/results files are also usable without this server."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .core import read_json
from .runner import Manager


def serve(lab, port=8791):
    manager = Manager(lab)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, kind="application/json; charset=utf-8"):
            if not isinstance(body, (str, bytes)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False)
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            host = self.headers.get("Host")
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.send(403, {"error": "ローカルHostのみ利用できます。"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host:
                self.send(403, {"error": "同じ画面から操作してください。"})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/app.js", "/style.css"):
                    filename, kind = {"/": ("index.html", "text/html; charset=utf-8"),
                                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                                      "/style.css": ("style.css", "text/css; charset=utf-8")}[url.path]
                    self.send(200, (lab.directory / filename).read_bytes(), kind)
                elif url.path == "/api/state":
                    self.send(200, {"candidates": lab.candidates(), "models": lab.models(), "suites": lab.suites(),
                                    "runs": lab.list_runs(), "notes": lab.notes(), "manager": manager.state()})
                elif url.path == "/api/run":
                    identifier = parse_qs(url.query)["id"][0]
                    result = lab.run(identifier)
                    result["requests"] = read_json(lab.runs / result["manifest"]["id"] / "requests.json")
                    self.send(200, result)
                elif url.path == "/report":
                    self.send(200, lab.export_html(parse_qs(url.query)["id"][0]), "text/html; charset=utf-8")
                else:
                    self.send(404, {"error": "Not found"})
            except (ValueError, KeyError, OSError) as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed():
                return
            try:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("application/json が必要です。")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    raise ValueError("本文サイズが不正です。")
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/candidate":
                    result = lab.save_candidate(body["candidate"], body.get("expected_etag"))
                elif self.path == "/api/prepare":
                    result = lab.prepare(body)
                elif self.path == "/api/start":
                    result = manager.start(body["batch_id"])
                elif self.path == "/api/cancel":
                    result = manager.cancel()
                elif self.path == "/api/note":
                    result = lab.add_note(body)
                else:
                    self.send(404, {"error": "Not found"}); return
                self.send(200, result)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                self.send(400, {"error": str(exc)})

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"Dogido Prompt Lab: http://127.0.0.1:{port}  (Ctrl+Cで終了)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            manager.cancel()
            if manager.thread:
                manager.thread.join()
