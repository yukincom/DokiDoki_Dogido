"""同じMac・同じ利用者のユノから伝言を受け、登録PC全員への配送結果を返す。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socketserver
import stat
import threading
import unicodedata


def _request(line):
    if not line.endswith(b"\n") or len(line) > 65536:
        raise ValueError
    item = json.loads(line)
    if not isinstance(item, dict) or set(item) != {"source_id", "sender", "text"}:
        raise ValueError
    if not isinstance(item["source_id"], str) or not re.fullmatch(r"line-[0-9a-f]{32}", item["source_id"]):
        raise ValueError
    for key, limit in (("sender", 64), ("text", 5000)):
        value = item[key]
        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or len(value) > limit
                or any(unicodedata.category(ch) in ("Cc", "Cs", "Zl", "Zp") for ch in value)):
            raise ValueError
    return item


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(45)
        try:
            request = _request(self.rfile.readline(65537))
        except (ValueError, OSError, RecursionError):
            response = {"accepted": False, "reason": "invalid_message"}
        else:
            try:
                # 全登録への配送と実際の配送完了、停止・失敗の判定はホスト側が所有する。
                response = self.server.deliver(**request)
            except Exception:
                # 既に届いた可能性があるので未達と断定しない。例外詳細には本文を含み得る。
                response = {"accepted": False, "reason": "delivery_unknown"}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except OSError:
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


@contextmanager
def serve(state, deliver):
    """ホストのrole_lockを取得中に一度だけ開く。停止時はこの起動が所有するソケットを消す。"""
    state = Path(state)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("ユノの接続先には自分だけが使えるフォルダを指定してください。")
    path = state / "family-line.sock"
    try:
        old = path.lstat()
    except FileNotFoundError:
        old = None
    if old is not None:
        if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.getuid():
            raise ValueError("ユノの接続先に別のファイルがあります。")
        path.unlink()
    with _Server(str(path), _Handler) as server:
        inode = path.lstat().st_ino
        try:
            path.chmod(0o600)
            server.deliver = deliver
            worker = threading.Thread(target=server.serve_forever, args=(0.1,), daemon=True)
            worker.start()
            try:
                yield
            finally:
                server.shutdown()
                worker.join()
        finally:
            if path.exists() and path.lstat().st_ino == inode:
                path.unlink()
