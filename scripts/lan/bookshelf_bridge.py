"""登録済み家庭内PCの本棚を中継する。要求・応答はメモリーだけに保持し、句を複製保存しない。"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import stat
import threading
import time
from urllib.parse import urlsplit

MAX_BYTES = 16 << 20
WIRE_LIMIT = 24 << 20
REQUEST_SECONDS = 15
ONLINE_SECONDS = 12
NEXT = '/dogido/bookshelf/next'
RESULT = '/dogido/bookshelf/result'
STATE = Path.home() / 'Library/Application Support/Dogido LAN'
ID = re.compile('[a-f0-9]{32}')
DEVICE = re.compile('[a-f0-9]{16}')
TYPES = {'application/json', 'application/javascript', 'text/javascript', 'image/png', 'image/jpeg', 'application/octet-stream', 'application/zip'}


def allowed(method, path):
    """家庭内の本棚だけを対象とする。URL・ファイルパス・読み辞書編集は中継しない。"""
    if not isinstance(path, str) or len(path) > 4096 or '?' in path or '#' in path:
        return False
    fixed = {'GET': {'/bootstrap.js', '/api/library', '/api/trash'},
             'PUT': {'/api/library', '/api/poems/current', '/api/photos', '/api/trash'},
             'POST': {'/api/poems/export'}}
    if path in fixed.get(method, set()):
        return True
    return method == 'GET' and bool(re.fullmatch(r'/api/photos/[A-Za-z0-9_%:-]+/image', path))


def _encoded(body):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise ValueError('本棚の応答が大きすぎます。')
    return base64.b64encode(body).decode('ascii')


def _decoded(value):
    if not isinstance(value, str) or len(value) > ((MAX_BYTES + 2) // 3) * 4:
        raise ValueError('本棚の応答を確認できません。')
    data = base64.b64decode(value, validate=True)
    if len(data) > MAX_BYTES:
        raise ValueError('本棚の応答が大きすぎます。')
    return data


def _error(status, message):
    return status, 'application/json', json.dumps({'detail': message}, ensure_ascii=False).encode()


@dataclass
class Pending:
    device: str
    method: str
    path: str
    body: str
    deadline: float
    offered: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    response: tuple | None = None


class Broker:
    """親本棚の要求を一度だけ子へ渡す。期限切れの未配送要求を後から実行しない。"""
    def __init__(self, records):
        self.records = records
        self.lock = threading.Lock()
        self.pending = {}
        self.seen = {}
        self.closed = False

    def list(self):
        now = time.monotonic()
        with self.lock:
            return [dict(row, name=row.get('name') or '息子用',
                         online=now - self.seen.get(row['id'], float('-inf')) < ONLINE_SECONDS)
                    for row in self.records()]

    def request(self, device, method, path, body):
        if not allowed(method, path) or len(body) > 128 * 1024:
            return _error(400, 'この本棚の操作を確認できません。')
        rows = self.list()
        if device is None:
            if len(rows) != 1:
                return _error(409, '開く息子さんのMacを設定してください。')
            device = rows[0]['id']
        if not DEVICE.fullmatch(device) or not any(r['id'] == device for r in rows):
            return _error(404, '登録済みの息子さんのMacが見つかりません。')
        if not any(r['id'] == device and r['online'] for r in rows):
            return _error(503, '息子さんのMacで本棚を開き、同じLANに接続してください。')
        item = Pending(device, method, path, _encoded(body), time.monotonic() + REQUEST_SECONDS)
        key = secrets.token_hex(16)
        with self.lock:
            if self.closed or len(self.pending) >= 32:
                return _error(503, '本棚の接続を待っています。少し待って試してください。')
            self.pending[key] = item
        item.done.wait(REQUEST_SECONDS)
        with self.lock:
            self.pending.pop(key, None)
            response = item.response
        return response or _error(504, '応答を確認できませんでした。編集した内容を残して、接続後にもう一度保存してください。')

    def poll(self, device):
        now = time.monotonic()
        with self.lock:
            self.seen[device] = now
            for key, item in self.pending.items():
                if item.device == device and not item.offered and not item.done.is_set() and item.deadline > now:
                    item.offered = True
                    return {'id': key, 'method': item.method, 'path': item.path, 'body': item.body,
                            'remaining': item.deadline - now}
        return None

    def result(self, device, value):
        if (not isinstance(value, dict) or set(value) != {'id', 'status', 'content_type', 'body'}
                or not isinstance(value['id'], str) or not ID.fullmatch(value['id'])
                or type(value['status']) is not int or not 100 <= value['status'] <= 599
                or value['content_type'] not in TYPES):
            raise ValueError('本棚の応答を確認できません。')
        response = value['status'], value['content_type'], _decoded(value['body'])
        with self.lock:
            item = self.pending.get(value['id'])
            if item and item.device == device and item.offered and not item.done.is_set():
                item.response = response
                item.done.set()
        # ACK消失後の再報告は成功として終了し、保存操作を再実行させない。

    def revoke(self, device):
        with self.lock:
            self.seen.pop(device, None)
            for item in self.pending.values():
                if item.device == device:
                    item.response = _error(403, 'このMacの登録が解除されました。')
                    item.done.set()

    def close(self):
        with self.lock:
            self.closed = True
            for item in self.pending.values():
                item.response = _error(503, '共有側の本棚接続が終了しました。')
                item.done.set()


def _line(handle):
    raw = handle.readline(WIRE_LIMIT + 1)
    if len(raw) > WIRE_LIMIT or not raw.endswith(b'\n'):
        raise ValueError('本棚の通信を確認できません。')
    return json.loads(raw)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(REQUEST_SECONDS + 5)
        try:
            value = _line(self.rfile)
            if value == {'action': 'list'}:
                response = {'devices': self.server.broker.list()}
            elif isinstance(value, dict) and set(value) == {'action', 'device', 'method', 'path', 'body'} and value['action'] == 'request':
                status, typ, body = self.server.broker.request(value['device'], value['method'], value['path'], _decoded(value['body']))
                response = {'status': status, 'content_type': typ, 'body': _encoded(body)}
            else:
                raise ValueError
        except (OSError, ValueError, TypeError, RecursionError):
            status, typ, body = _error(400, '本棚の通信を確認できません。')
            response = {'status': status, 'content_type': typ, 'body': _encoded(body)}
        try:
            self.wfile.write(json.dumps(response).encode() + b'\n')
        except OSError:
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


@contextmanager
def serve_host(state_dir, broker):
    """hostのrole_lock下で作る所有者限定窓口。句や応答のファイルは作らない。"""
    root = Path(state_dir)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('本棚の接続フォルダを確認してください。')
    path = root / 'bookshelf.sock'
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('本棚の接続先が使われています。')
        path.unlink()
    with _Server(str(path), _Handler) as server:
        path.chmod(0o600)
        inode = path.lstat().st_ino
        server.broker = broker
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        worker.start()
        try:
            yield
        finally:
            broker.close()
            server.shutdown()
            worker.join()
            if path.exists() and path.lstat().st_ino == inode:
                path.unlink()


def _local(state_dir, value):
    path = Path(state_dir) / 'bookshelf.sock'
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('共有側の本棚接続を確認してください。')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(REQUEST_SECONDS + 5)
        client.connect(str(path))
        client.sendall(json.dumps(value).encode() + b'\n')
        with client.makefile('rb') as handle:
            return _line(handle)


def list_remote(state_dir=None):
    return _local(state_dir or STATE / 'host', {'action': 'list'})['devices']


def request_remote(state_dir, device_id, method, path, body=b''):
    value = _local(state_dir or STATE / 'host', {'action': 'request', 'device': device_id,
                   'method': method, 'path': path, 'body': _encoded(body)})
    return value['status'], value['content_type'], _decoded(value['body'])


def _exchange(address, credentials, path, value):
    import transport
    conn, _ = transport._tls(address, credentials['pin'], timeout=5)
    try:
        conn.request('POST', path, json.dumps(value), {'Authorization': transport._auth(credentials), 'Content-Type': 'application/json'})
        response = conn.getresponse()
        raw = response.read(WIRE_LIMIT + 1)
        if response.status != 200 or len(raw) > WIRE_LIMIT:
            raise OSError('共有側の本棚を待っとるで。')
        return json.loads(raw)
    finally:
        conn.close()


def forward_local(local_url, admin_token, csrf, request):
    """宛先は起動時に固定したloopbackのみ。親の権限をブラウザーの指定で切り替えない。"""
    url = urlsplit(local_url)
    if (url.scheme != 'http' or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or not ipaddress.ip_address(url.hostname).is_loopback):
        raise ValueError('本棚の起動先を確認できへんかった。もう一回開いてな。')
    if (not isinstance(request, dict) or set(request) != {'id', 'method', 'path', 'body', 'remaining'}
            or not isinstance(request['id'], str) or not ID.fullmatch(request['id'])
            or not allowed(request['method'], request['path']) or not isinstance(request['remaining'], (int, float))
            or not 0 < request['remaining'] <= REQUEST_SECONDS):
        raise ValueError('本棚の操作を確認できへんかった。もう一回試してな。')
    body = _decoded(request['body'])
    if len(body) > 128 * 1024:
        raise ValueError('保存する内容が大きすぎるみたいや。')
    origin = f'http://{url.netloc}'
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=min(8, request['remaining']))
    try:
        conn.request(request['method'], request['path'], body if body else None,
                     {'Origin': origin, 'X-Dogido-Library': csrf, 'X-Dogido-Bookshelf-Admin': admin_token,
                      'Content-Type': 'application/json'})
        response = conn.getresponse()
        typ = response.getheader('Content-Type', 'application/json').split(';')[0].strip()
        raw = response.read(MAX_BYTES + 1)
        if typ not in TYPES or len(raw) > MAX_BYTES:
            raise ValueError('本棚の応答を確認できへんかった。')
        return {'id': request['id'], 'status': response.status, 'content_type': typ, 'body': _encoded(raw)}
    finally:
        conn.close()


def serve_child(stop, local_url, admin_token, csrf, state_dir=None):
    """子本棚が開いている間だけ接続する。ゲーム・音声・生成モデルは起動しない。"""
    import transport
    from connection import PeerRoute, discover_peers
    from discovery import Bonjour, BonjourError
    root = Path(state_dir or STATE / 'client')
    try:
        credentials = transport.load_pair(root)
    except (OSError, ValueError):
        print('共有Macとの登録を確認できへんかった。このMacの本棚はそのまま使えるで。', flush=True)
        return
    if credentials is None:
        print('共有Macとの登録がまだみたいや。ドギドの接続を一度開いてな。', flush=True)
        return
    # 本棚を二重起動しても同じ端末の要求を別workerで処理しない。後から開いた棚はローカル利用を続ける。
    try:
        lock_file = (root / 'bookshelf-worker.lock').open('a+b')
    except OSError:
        print('共有の準備ができへんかった。このMacの本棚はそのまま使えるで。', flush=True)
        return
    with lock_file as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        discovery = Bonjour()
        wording = {
            '共有Macを待っています。': '共有Macを待っとるで。このMacの句はそのまま見られるからな。',
            '共有Macでこの端末の登録が解除されています。': '共有Macとの登録が外れてるみたいや。おうちの人に確認してな。',
            '登録した相手を確認できません。共有側の登録を確認してください。': 'いつもの共有Macか確認できへんかった。おうちの人に確認してな。',
            '登録済みの共有Macに接続しました。': '共有Macとつながったで。',
        }
        route = PeerRoute(lambda message: print(wording.get(message, message), flush=True))
        next_probe = 0
        pending = None
        while not stop.is_set():
            try:
                if time.monotonic() >= next_probe:
                    if route.probe(discover_peers(discovery), credentials, transport.probe):
                        print('共有Macとの登録が外れてるみたいや。おうちの人に確認してな。', flush=True)
                        return
                    next_probe = time.monotonic() + 3
                address = route.resolve()
                if pending is not None:
                    _exchange(address, credentials, RESULT, pending)
                    pending = None
                started = time.monotonic()
                request = _exchange(address, credentials, NEXT, {})['request']
                if request is not None:
                    # 往復に費やした時間も差し引き、期限後に届いた書込みを開始しない。
                    request['remaining'] -= time.monotonic() - started
                    try:
                        pending = forward_local(local_url, admin_token, csrf, request)
                    except (OSError, ValueError, TypeError, http.client.HTTPException):
                        status, typ, body = _error(504, '保存の結果をまだ確認できへんねん。書いた内容を残して、接続を確かめてから試してな。')
                        pending = {'id': request['id'], 'status': status, 'content_type': typ, 'body': _encoded(body)}
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException, BonjourError):
                # 結果の再報告だけを再試行する。保存済みか不明な操作を再実行しない。
                next_probe = 0
            stop.wait(.3)
