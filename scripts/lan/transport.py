"""Paired Dogido HTTPS transport. Discovery and user dialogs belong to the launcher."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from functools import lru_cache
import fcntl
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.parse import quote, unquote

from family_messages import GAME_REASONS, MessageBox, check_activity, check_text


PAIR_SECONDS = 120
HEX64 = re.compile(r'[a-f0-9]{64}')
DEVICE_ID = re.compile(r'[a-f0-9]{16}')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
CHAT = '/v1/chat/completions'
LOGS = '/dogido/client-logs'
MODELS = '/v1/models'
READINGS = '/dogido/readings'
AUTH = '/dogido/auth'
NEXT_MESSAGE = '/dogido/family/next'
MESSAGE_RESULT = '/dogido/family/result'
PAIR = '/dogido/pair/'
MAX_BODY = {CHAT: 2 << 20, LOGS: 1 << 20}
IO_TIMEOUT = 200


class IdentityError(ValueError):
    """The known endpoint is not the paired peer, or the credential was revoked."""


class RevokedError(IdentityError):
    """The pinned host itself refused the stored credential."""


class Refusal(Exception):
    def __init__(self, status):
        self.status = status


def _directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError('端末登録の保存先を確認してください')
    path.chmod(0o700)
    return path


def _read(path):
    if path.is_symlink():
        raise ValueError('認証ファイルがリンクになっています')
    try:
        with path.open() as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def _save(path, value):
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def _locked(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _identity(root):
    # One atomic identity file avoids accepting a new key after a partial launch.
    identity = root / 'host.pem'
    with _locked(root / 'identity.lock'):
        if identity.is_symlink():
            raise ValueError('サーバー鍵がリンクになっています')
        if not identity.exists():
            with tempfile.TemporaryDirectory(dir=root) as tmp:
                key, cert = Path(tmp) / 'key', Path(tmp) / 'cert'
                subprocess.run(['/usr/bin/openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-x509',
                                '-days', '3650', '-subj', '/CN=Dogido LAN',
                                '-keyout', str(key), '-out', str(cert)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                cert.chmod(0o600)
                with cert.open('ab') as handle:
                    handle.write(key.read_bytes())
                os.replace(cert, identity)
    pem = identity.read_text().partition('-----END CERTIFICATE-----')
    der = ssl.PEM_cert_to_DER_cert(pem[0] + pem[1])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(identity)
    return hashlib.sha256(der).hexdigest(), context


def _digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def _code(nonce, challenge, pin):
    # Commit to the client's nonce before the server generates its challenge.
    # Neither side of a relayed exchange can choose the matching display code
    # after learning the other side's secrets, even with a substituted TLS key.
    digest = _digest('dogido-pair-v1|' + nonce + '|' + challenge + '|' + pin)
    return str(int(digest, 16) % 1_000_000).zfill(6)


def _credentials(value):
    if (not isinstance(value, dict) or set(value) != {'pin', 'device', 'token'}
            or not all(isinstance(val, str) for val in value.values())
            or not HEX64.fullmatch(value['pin']) or not DEVICE_ID.fullmatch(value['device'])
            or not TOKEN.fullmatch(value['token'])):
        raise IdentityError('登録情報を確認してください')
    return value


def load_pair(state_dir):
    value = _read(_directory(state_dir) / 'client.json')
    return None if value is None else _credentials(value)


def forget_pair(state_dir):
    (_directory(state_dir) / 'client.json').unlink(missing_ok=True)


class Pairings:
    """One opt-in, expiring enrollment. Host stores token hashes only."""
    def __init__(self, root, pin, confirm):
        self.root, self.pin, self.confirm = root, pin, confirm
        self.lock = threading.Lock()
        self.open_until = 0
        self.pending = None
        self.attempts = deque(maxlen=5)
        self.prompt_busy = False

    def open(self, seconds):
        with self.lock:
            self.open_until = time.monotonic() + min(seconds, PAIR_SECONDS)

    def begin(self, commit):
        now = time.monotonic()
        with self.lock:
            if now >= self.open_until:
                raise Refusal(403)
            if self.prompt_busy or (self.pending and self.pending['deadline'] > now):
                raise Refusal(409)
            if len(self.attempts) == 5 and now - self.attempts[0] < PAIR_SECONDS:
                raise Refusal(429)
            self.attempts.append(now)
            challenge = secrets.token_hex(32)
            self.pending = {'commit': commit, 'challenge': challenge,
                            'deadline': min(self.open_until, now + PAIR_SECONDS),
                            'decision': None, 'shown': False}
            return {'challenge': challenge}

    def _live(self, nonce):
        item = self.pending
        if (not item or item['deadline'] <= time.monotonic()
                or not hmac.compare_digest(item['commit'], _digest(nonce))):
            raise Refusal(403)
        return item

    def prove(self, nonce):
        with self.lock:
            item = self._live(nonce)
            if item['shown']:
                raise Refusal(409)
            item['shown'] = True
            self.prompt_busy = True
        def prompt():
            decision = False
            try:
                decision = self.confirm(_code(nonce, item['challenge'], self.pin)) is True
            finally:
                with self.lock:
                    item['decision'] = decision
                    self.prompt_busy = False
        threading.Thread(target=prompt, daemon=True).start()

    def cancel(self, nonce):
        with self.lock:
            self._live(nonce)
            self.pending = None

    def finish(self, nonce):
        with self.lock:
            item = self._live(nonce)
            if not item['shown']:
                raise Refusal(409)
            if item['decision'] is None:
                return 202, {}
            self.pending = None
            if not item['decision']:
                raise Refusal(403)
            token, device = secrets.token_urlsafe(32), secrets.token_hex(8)
            with _locked(self.root / 'devices.lock'):
                devices = self._devices()
                devices[device] = _digest(token)
                _save(self.root / 'devices.json', devices)
            self.open_until = 0
            return 200, {'device': device, 'token': token}

    def _devices(self):
        return _read(self.root / 'devices.json') or {}

    def devices(self):
        return sorted(self._devices())

    def device_records(self):
        """表示名は登録情報に添える。宛先と認証は従来の端末ID・tokenで照合する。"""
        return [{'id': device, 'name': value.get('name', '') if isinstance(value, dict) else ''}
                for device, value in sorted(self._devices().items())]

    def device_name(self, device):
        value = self._devices().get(device)
        return value.get('name') or '登録PC' if isinstance(value, dict) else '登録PC'

    def name_device(self, device, name):
        name = check_text(name, 80)
        with _locked(self.root / 'devices.lock'):
            devices = self._devices()
            if device not in devices:
                return
            old = devices[device]
            value = dict(old) if isinstance(old, dict) else {'token_hash': old}
            if value.get('name') == name:
                return
            value['name'] = name
            devices[device] = value
            _save(self.root / 'devices.json', devices)

    def revoke(self, device):
        with _locked(self.root / 'devices.lock'):
            devices = self._devices()
            if device not in devices:
                return False
            del devices[device]
            _save(self.root / 'devices.json', devices)
            return True

    def authorized_device(self, headers):
        if len(headers) != 1:
            return None
        prefix, _, credential = headers[0].partition(' ')
        device, _, token = credential.partition('.')
        stored = self._devices().get(device, '')
        # 名前のない既存登録もそのまま読める。表示名の追加でtokenを作り直さない。
        digest = stored.get('token_hash', '') if isinstance(stored, dict) else stored
        if (prefix == 'Bearer' and DEVICE_ID.fullmatch(device) and TOKEN.fullmatch(token)
                and isinstance(digest, str) and hmac.compare_digest(digest, _digest(token))):
            return device
        return None


def _tls(address, pin=None, *, timeout=8):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Verify the paired certificate before writing any HTTP bytes.
    connection = http.client.HTTPSConnection(address[0], address[1], timeout=timeout, context=ctx)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if pin is not None and not hmac.compare_digest(pin, actual):
            raise IdentityError('共有Macの登録情報と証明書が一致しません')
        return connection, actual
    except BaseException:
        connection.close()
        raise


def pairing_address(peers):
    """登録を開始する前に、選んだ共有Macの広告とTLS鍵が一致する到達可能な経路を探す。"""
    for peer in peers:
        try:
            conn, _ = _tls(peer.address, peer.pin, timeout=2)
        except (IdentityError, OSError):
            continue
        conn.close()
        return peer.address
    return None


def _enroll(address, pin, route, value):
    conn, actual = _tls(address, pin)
    try:
        conn.request('POST', PAIR + route, json.dumps(value), {'Content-Type': 'application/json'})
        reply = conn.getresponse()
        if reply.status not in (200, 202):
            raise IdentityError(f'共有Macでペアリングの受付を確認してください（{reply.status}）')
        data = reply.read(4097)
        if len(data) > 4096:
            raise IdentityError('ペアリング応答が大きすぎます')
        return actual, reply.status, json.loads(data)
    finally:
        conn.close()


def pair(state_dir, address, confirm):
    root = _directory(state_dir)
    with _locked(root / 'pair.lock'):
        if load_pair(root) is not None:
            raise IdentityError('先に既存の端末登録を解除してください')
        nonce = secrets.token_hex(32)
        pin, _, hello = _enroll(address, None, 'begin', {'commit': _digest(nonce)})
        challenge = hello.get('challenge') if isinstance(hello, dict) else None
        if not isinstance(challenge, str) or not HEX64.fullmatch(challenge):
            raise IdentityError('ペアリング応答の形式を確認してください')
        value = {'nonce': nonce}
        deadline = time.monotonic() + PAIR_SECONDS
        _enroll(address, pin, 'prove', value)
        if confirm(_code(nonce, challenge, pin)) is not True:
            _enroll(address, pin, 'cancel', value)
            raise IdentityError('ペアリングを取り消しました')
        while time.monotonic() < deadline:
            _, status, result = _enroll(address, pin, 'finish', value)
            if status == 200:
                credentials = _credentials(dict(result, pin=pin))
                _save(root / 'client.json', credentials)
                return credentials
            time.sleep(0.25)
        raise IdentityError('ペアリングの制限時間を過ぎました')


def _auth(value):
    credentials = _credentials(value)
    return 'Bearer ' + credentials['device'] + '.' + credentials['token']


@lru_cache(maxsize=1)
def computer_name():
    """macOSの登録名を起動中に一度読む。HTTPヘッダーへはUTF-8をpercent encodeして渡す。"""
    try:
        result = subprocess.run(['/usr/sbin/scutil', '--get', 'ComputerName'],
                                capture_output=True, text=True, check=True, timeout=2)
        return check_text(result.stdout.strip(), 80)
    except (OSError, ValueError, subprocess.SubprocessError):
        return check_text(socket.gethostname().removesuffix('.local')[:80], 80)


def probe(address, credentials):
    # 切断済みの経路が、同じ登録先の別経路への切り替えを長く塞がないようにする。
    conn, _ = _tls(address, credentials['pin'], timeout=2)
    try:
        conn.request('GET', AUTH, headers={'Authorization': _auth(credentials),
                                          'X-Dogido-Device-Name': quote(computer_name(), safe='')})
        response = conn.getresponse()
        if response.status in (401, 403):
            raise RevokedError('端末登録が解除されています')
        if response.status != 200:
            raise OSError('ドギドの窓口が応答していません')
    except http.client.HTTPException as error:
        raise OSError('HTTPS応答が途切れました') from error
    finally:
        conn.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bind, handler, context=None):
        self.context = context
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(bind, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            request.settimeout(8)
            if self.context:
                try:
                    request = self.context.wrap_socket(request, server_side=True)
                except OSError:
                    request.close()
                    return
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 delimits streaming with EOF. No redirects, cookies, upstream
    # headers, authorization values, payloads or dynamic paths enter our logs.
    def log_message(self, *_):
        pass

    def reply(self, status, value=None):
        body = json.dumps({} if value is None else value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit):
        lengths = self.headers.get_all('Content-Length', [])
        if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                or self.headers.get_content_type() != 'application/json'
                or not lengths[0].isdigit() or len(lengths[0]) > 9):
            raise Refusal(400)
        size = int(lengths[0])
        if not 0 < size <= limit:
            raise Refusal(413)
        data = self.rfile.read(size)
        if len(data) != size:
            raise Refusal(400)
        return data

    def forward(self, conn, auth=None):
        started = False
        try:
            if self.command == 'GET' and self.path in (MODELS, READINGS):
                data = None
            elif self.command == 'POST' and self.path in MAX_BODY:
                data = self.body(MAX_BODY[self.path])
            else:
                raise Refusal(404)
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['Authorization'] = auth
            conn.request(self.command, self.path, data, headers)
            response = conn.getresponse()
            typ = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if typ not in ('application/json', 'text/event-stream'):
                raise Refusal(502)
            limit = (8 if typ == 'text/event-stream' else 2) << 20
            if typ == 'application/json':
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise Refusal(502)
            self.send_response(response.status)
            self.send_header('Content-Type', typ + '; charset=utf-8')
            if typ == 'application/json':
                self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            started = True
            if typ == 'application/json':
                self.wfile.write(raw)
            else:
                total = 0
                while chunk := response.read1(65536):
                    total += len(chunk)
                    if total > limit:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (OSError, http.client.HTTPException):
            if not started:
                raise Refusal(502)
        finally:
            conn.close()

    def dispatch(self):
        try:
            self.handle_route()
        except Refusal as error:
            self.reply(error.status)
        except (OSError, http.client.HTTPException):
            self.close_connection = True

    do_GET = do_POST = dispatch


def host_listener(state_dir, upstream_port, confirm, messages=None, readings=None):
    """同じ登録受付・TLS鍵・伝言を、LANアドレス別の待受けで共有する。"""
    root = _directory(state_dir)
    pin, ctx = _identity(root)
    pairings = Pairings(root, pin, confirm)
    if messages is None:
        messages = MessageBox(root, pairings.devices)
    messages.device_name = pairings.device_name

    class HostHandler(Handler):
        def handle_route(self):
            if self.command == 'POST' and self.path.startswith(PAIR):
                route = self.path[len(PAIR):]
                if route not in ('begin', 'prove', 'finish', 'cancel'):
                    raise Refusal(404)
                try:
                    obj = json.loads(self.body(1024))
                except (ValueError, RecursionError):
                    raise Refusal(400)
                field = 'commit' if route == 'begin' else 'nonce'
                if (not isinstance(obj, dict) or set(obj) != {field}
                        or not isinstance(obj[field], str) or not HEX64.fullmatch(obj[field])):
                    raise Refusal(400)
                val = obj[field]
                if route == 'begin':
                    self.reply(200, pairings.begin(val))
                elif route == 'prove':
                    pairings.prove(val)
                    self.reply(202)
                elif route == 'cancel':
                    pairings.cancel(val)
                    self.reply(200)
                else:
                    self.reply(*pairings.finish(val))
                return
            device = pairings.authorized_device(self.headers.get_all('Authorization', []))
            if not device:
                raise Refusal(401)
            if self.path == AUTH and self.command == 'GET':
                names = self.headers.get_all('X-Dogido-Device-Name', [])
                if names:
                    try:
                        if len(names) != 1 or len(names[0]) > 960:
                            raise ValueError
                        pairings.name_device(device, unquote(names[0], errors='strict'))
                    except ValueError:
                        raise Refusal(400)
                self.reply(200)
            elif self.path == READINGS:
                if self.command != 'GET':
                    raise Refusal(405)
                if readings is None:
                    raise Refusal(503)
                try:
                    value = readings()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    raise Refusal(503)
                self.reply(200, value)
            elif self.command == 'POST' and self.path in (NEXT_MESSAGE, MESSAGE_RESULT):
                try:
                    data = json.loads(self.body(2048))
                    if not isinstance(data, dict):
                        raise ValueError
                    if self.path == NEXT_MESSAGE:
                        reason = data.get('reason')
                        if set(data) != {'reason', 'player'} or reason not in GAME_REASONS | {None}:
                            raise ValueError
                        player = check_text(data['player'], 80) if data['player'] != '' else ''
                        self.reply(200, {'message': messages.poll(device, reason, player)})
                    else:
                        if (set(data) not in ({'id', 'status'}, {'id', 'status', 'activity'})
                                or not re.fullmatch('[a-f0-9]{32}', data['id'])
                                or data['status'] not in GAME_REASONS | {'delivered', 'delivery_failed', 'delivery_unknown'}):
                            raise ValueError
                        activity = check_activity(data.get('activity', []))
                        if activity and data['status'] != 'delivered':
                            raise ValueError
                        messages.report(device, data['id'], data['status'], activity)
                        self.reply(200)
                except (TypeError, ValueError, RecursionError):
                    raise Refusal(400)
            else:
                self.forward(http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=IO_TIMEOUT))

    def revoke(device):
        removed = pairings.revoke(device)
        messages.revoked(device)
        return removed

    def listen(bind):
        server = Server(bind, HostHandler, ctx)
        server.pin = pin
        for name in ('open_pairing', 'devices', 'device_records', 'revoke', 'messages'):
            setattr(server, name, getattr(listen, name))
        return server
    # 設定はLANの抜き差しと独立して同じ登録正本へ届く。
    listen.open_pairing = lambda seconds=PAIR_SECONDS: pairings.open(seconds)
    listen.devices = pairings.devices
    listen.device_records = pairings.device_records
    listen.revoke = revoke
    listen.messages = messages
    return listen


def make_host(state_dir, bind, upstream_port, confirm, messages=None, readings=None):
    return host_listener(state_dir, upstream_port, confirm, messages, readings)(bind)


def make_client_relay(resolve, credentials, port=8080):
    auth = _auth(credentials)
    pin = credentials['pin']

    class RelayHandler(Handler):
        def handle_route(self):
            # A browser opening an unrelated site must not be able to use our paired credential.
            port = self.server.server_port
            if self.headers.get_all('Host', []) not in ([f'127.0.0.1:{port}'], [f'localhost:{port}']) or 'Origin' in self.headers:
                raise Refusal(403)
            if (self.command, self.path) not in {('GET', MODELS), ('GET', READINGS), ('POST', CHAT), ('POST', LOGS)}:
                raise Refusal(404)
            try:
                conn, _ = _tls(resolve(), pin)
            except IdentityError:
                raise Refusal(403)
            except (OSError, http.client.HTTPException):
                raise Refusal(503)
            conn.sock.settimeout(IO_TIMEOUT)
            self.forward(conn, auth)

    return Server(('127.0.0.1', port), RelayHandler)


def message_request(address, credentials, path, data):
    conn, _ = _tls(address, credentials['pin'])
    try:
        conn.request('POST', path, json.dumps(data), {'Authorization': _auth(credentials), 'Content-Type': 'application/json'})
        response = conn.getresponse()
        if response.status != 200:
            raise OSError(f'伝言窓口の応答 {response.status}')
        return json.loads(response.read(65537))
    finally:
        conn.close()
