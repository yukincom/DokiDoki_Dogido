"""User-owned, foreground Qwen-only SSH gateway. Never edits macOS settings."""
from __future__ import annotations

import argparse
from datetime import datetime
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys
import threading
import time
import zipfile
import uuid

SSH_PORT = 22080
PROXY_PORT = 22081
MAX_BODY = 1024 * 1024
STATE = Path.home() / 'Library/Application Support/Dogido-Qwen-Link'


def log_text(value):
    # Child log text is data, never terminal control sequences or paths to write.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
    return ''.join(char if char >= ' ' and not '\x7f' <= char <= '\x9f' else ' '
                   for char in value)


class DebugLog:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6] + '.jsonl')
        self.file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        self.lock = threading.Lock()

    def emit(self, source, message, **details):
        record = {'time': datetime.now().astimezone().isoformat(timespec='milliseconds'),
                  'source': source, 'message': log_text(message), **details}
        with self.lock:
            suffix = ' '.join(f'{key}={log_text(value)}' for key, value in details.items())
            try:
                print(f"[{record['time'][11:23]}][{source}] {record['message']} {suffix}".rstrip(), flush=True)
            except OSError:
                pass  # A closing Terminal must not prevent shutdown.
            if self.file.closed:
                return
            try:
                self.file.write(json.dumps(record, ensure_ascii=False) + '\n')
                self.file.flush()
            except OSError:
                try:
                    self.file.close()
                except OSError:
                    pass
                try:
                    print('ログ保存に失敗しました。ターミナル表示のみ継続します。', flush=True)
                except OSError:
                    pass

    def close(self):
        with self.lock:
            try:
                self.file.close()
            except OSError:
                pass


def checked_logs(body):
    if not isinstance(body, dict) or set(body) != {'source', 'lines'}:
        raise ValueError('Invalid log payload')
    if body['source'] not in ('server', 'voice'):
        raise ValueError('Invalid log source')
    lines = body['lines']
    if (not isinstance(lines, list) or not 1 <= len(lines) <= 32
            or any(not isinstance(line, str) or len(line) > 8192 for line in lines)):
        raise ValueError('Invalid log lines')
    return body['source'], lines


def checked_payload(body):
    if not isinstance(body, dict):
        raise ValueError('JSON object required')
    allowed = {'model', 'messages', 'temperature', 'max_tokens', 'stream', 'chat_template_kwargs'}
    if set(body) - allowed:
        raise ValueError('Unsupported request fields')
    if body.get('model', 'default_model') != 'default_model':
        raise ValueError('Model switching is not permitted')
    if body.get('stream', False) is not False:
        raise ValueError('Only non-streaming requests are supported')
    if body.get('chat_template_kwargs', {}) not in ({}, {'enable_thinking': False}):
        raise ValueError('Custom templates are not permitted')
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages required')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or not isinstance(message['role'], str)
                or message['role'] not in {'system', 'user', 'assistant', 'developer'}
                or not isinstance(message['content'], str)):
            raise ValueError('Only text messages are permitted')
    tokens = body.get('max_tokens')
    if type(tokens) is not int or tokens <= 0:
        raise ValueError('Positive max_tokens required')
    temperature = body.get('temperature', 0.7)
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0:
        raise ValueError('Invalid temperature')
    # Reconstruct instead of forwarding arbitrary model-loader or template options.
    return {'model': 'default_model', 'draft_model': None, 'adapters': None,
            'messages': messages, 'max_tokens': tokens, 'temperature': temperature,
            'stream': False, 'chat_template_kwargs': {'enable_thinking': False}}


def make_proxy(port=PROXY_PORT, upstream_port=8080, event=None):
    event = event or (lambda *args, **kwargs: None)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def log_message(self, *args):
            pass  # No conversation text, headers, or credentials in logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def respond(self, code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def upstream(self, method, path, body=None):
            # Fixed loopback upstream; no redirect following or caller-selected URLs.
            connection = http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=180)
            try:
                data = None if body is None else json.dumps(body).encode()
                connection.request(method, path, body=data,
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                raw = response.read(MAX_BODY + 1)
                if response.status != 200 or len(raw) > MAX_BODY:
                    raise ValueError('Upstream response rejected')
                return json.loads(raw)
            finally:
                connection.close()

        def do_GET(self):
            if self.path != '/v1/models':
                self.respond(404, {'error': 'Endpoint not available'})
                return
            try:
                result = self.upstream('GET', '/v1/models')
                if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                    raise ValueError('Unexpected upstream')
                self.respond(200, {'object': 'list', 'data': [
                    {'id': 'default_model', 'object': 'model', 'owned_by': 'dogido'}]})
            except (OSError, ValueError, http.client.HTTPException):
                self.respond(502, {'error': '親MacのQwenが応答していません'})

        def do_POST(self):
            if self.path not in ('/v1/chat/completions', '/dogido/client-logs'):
                self.respond(404, {'error': 'Endpoint not available'})
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
                    raise ValueError('Content-Length required')
                size = int(lengths[0])
                if not 0 < size <= MAX_BODY:
                    raise ValueError('Request too large or empty')
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('Incomplete request')
                decoded = json.loads(raw)
                if self.path == '/dogido/client-logs':
                    source, lines = checked_logs(decoded)
                    for line in lines:
                        event('息子/' + source, line)
                    self.respond(202, {'accepted': len(lines)})
                    return
                body = checked_payload(decoded)
            except (ValueError, OSError, RecursionError):
                event('接続', '許可されていないリクエストを拒否', endpoint=self.path)
                self.respond(400, {'error': '許可されていないリクエストです'})
                return
            request_id = uuid.uuid4().hex[:8]
            started = time.monotonic()
            event('Qwen', '生成開始', request_id=request_id, max_tokens=body['max_tokens'])
            try:
                result = self.upstream('POST', '/v1/chat/completions', body)
                if not isinstance(result, dict) or not isinstance(result.get('choices'), list):
                    raise ValueError('Unexpected completion')
                result['model'] = 'default_model'
                event('Qwen', '生成完了', request_id=request_id,
                      elapsed_ms=round((time.monotonic() - started) * 1000), usage=result.get('usage', {}))
                self.respond(200, result)
            except (OSError, ValueError, http.client.HTTPException):
                event('Qwen', '生成失敗', request_id=request_id,
                      elapsed_ms=round((time.monotonic() - started) * 1000))
                self.respond(502, {'error': '親MacのQwen生成に失敗しました'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def ssh_config(state, address, user, port=SSH_PORT, proxy_port=PROXY_PORT):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user):
        raise ValueError('Unexpected local account name')
    ipaddress.IPv4Address(address)
    if any(c in str(state) for c in '\n\r"'):
        raise ValueError('Unsupported state path')
    return f'''# Dogido dedicated endpoint. Not /etc/ssh/sshd_config.
ListenAddress {address}
AddressFamily inet
Port {port}
HostKey "{state / 'host_ed25519'}"
AuthorizedKeysFile "{state / 'authorized_keys'}"
PidFile "{state / 'sshd.pid'}"
UsePAM no
StrictModes yes
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
GSSAPIAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
AllowUsers {user}
MaxSessions 0
AllowTcpForwarding local
PermitOpen 127.0.0.1:{proxy_port}
PermitListen none
AllowStreamLocalForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTTY no
PermitTunnel no
PermitUserRC no
GatewayPorts no
LoginGraceTime 30
ClientAliveInterval 15
ClientAliveCountMax 3
LogLevel VERBOSE
'''


def lan_address():
    for interface in ('en1', 'en0', 'en2', 'en3'):
        result = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', interface],
                                text=True, capture_output=True)
        try:
            address = ipaddress.IPv4Address(result.stdout.strip())
        except ValueError:
            continue
        if address.is_private and not address.is_loopback and not address.is_link_local:
            return str(address)
    raise RuntimeError('家庭内LANのIPv4アドレスが見つかりません。Wi-Fi接続を確認してください。')


def key_pair(path):
    if not path.exists():
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                        '-C', 'dogido-qwen-only', '-f', str(path)], check=True)
    path.chmod(0o600)
    public = subprocess.check_output(['/usr/bin/ssh-keygen', '-y', '-f', str(path)], text=True).strip()
    return public


def prepare(state, bundle_dir, address):
    if state.is_symlink():
        raise RuntimeError('状態保存先がリンクになっています。停止しました。')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    user = pwd.getpwuid(os.getuid()).pw_name
    host_public = key_pair(state / 'host_ed25519')
    client_public = key_pair(state / 'client_ed25519')
    authorized = state / 'authorized_keys'
    # Server settings are authoritative; the key additionally rejects sessions if reused here.
    authorized.write_text('restrict,port-forwarding,command="/usr/bin/false",'
                          f'permitopen="127.0.0.1:{PROXY_PORT}" {client_public}\n')
    authorized.chmod(0o600)
    config = state / 'sshd_config'
    config.write_text(ssh_config(state, address, user))
    config.chmod(0o600)
    subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(config)], check=True)
    effective = subprocess.check_output(['/usr/sbin/sshd', '-G', '-f', str(config)], text=True)
    for required in ('maxsessions 0', 'passwordauthentication no', 'usepam no',
                     'allowtcpforwarding local', f'permitopen 127.0.0.1:{PROXY_PORT}',
                     'allowstreamlocalforwarding no', 'permitlisten none'):
        if required not in effective.splitlines():
            raise RuntimeError('専用SSHの制限検査に失敗: ' + required)
    package = bundle_dir / '息子Macへ渡す.zip'
    temporary = package.with_suffix('.zip.tmp')
    config_data = {'host': address, 'port': SSH_PORT, 'user': user,
                   'remote_port': PROXY_PORT, 'local_port': 8080}
    entries = {
        'link/connection.json': json.dumps(config_data, indent=2).encode(),
        'link/identity': (state / 'client_ed25519').read_bytes(),
        'link/known_hosts': f'[{address}]:{SSH_PORT} {host_public}\n'.encode(),
    }
    for source in (bundle_dir / 'client-package').rglob('*'):
        if source.is_file():
            entries[source.relative_to(bundle_dir / 'client-package').as_posix()] = source.read_bytes()
    old_mask = os.umask(0o077)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo('Dogido-接続更新/' + name)
                info.create_system = 3
                info.external_attr = (0o100700 if name.endswith('.command') else 0o100600) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(temporary, package)
        package.chmod(0o600)
    finally:
        os.umask(old_mask)
    return config, package


def stop_sshd(process):
    if process is None:
        return
    # OpenSSH forwarding children can create their own process groups. Freeze
    # this dedicated listener and its descendants before terminating the tree;
    # killing only the listener's group leaves authenticated connections alive.
    frozen = set()
    def send(pid, sig):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        send(process.pid, signal.SIGSTOP)
        frozen.add(process.pid)
        while True:
            listing = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid='], text=True)
            children = {int(pid) for line in listing.splitlines()
                        for pid, ppid in [line.split()] if int(ppid) in frozen} - frozen
            if not children:
                break
            for pid in children:
                send(pid, signal.SIGSTOP)
            frozen.update(children)
        for pid in frozen:
            send(pid, signal.SIGTERM)
            send(pid, signal.SIGCONT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        send(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run(state, bundle_dir):
    if os.geteuid() == 0:
        raise RuntimeError('sudoは不要です。通常のユーザーで開いてください。')
    address = lan_address()
    proxy = None
    daemon = None
    worker = None
    debug = None
    ssh_reader = None
    if state.is_symlink():
        raise RuntimeError('状態保存先がリンクになっています。停止しました。')
    lock = state / 'running.lock'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    import fcntl
    with lock.open('a+') as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('ドギド専用接続は既に起動中です。先に開いた画面を使ってください。')
        config, package = prepare(state, bundle_dir, address)
        try:
            debug = DebugLog(state / 'logs')
            proxy = make_proxy(event=debug.emit)
            worker = threading.Thread(target=proxy.serve_forever, daemon=True)
            worker.start()
            daemon = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(config)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, errors='replace', start_new_session=True)
            def read_ssh():
                for line in daemon.stdout:
                    debug.emit('SSH', line.rstrip())
            ssh_reader = threading.Thread(target=read_ssh, daemon=True)
            ssh_reader.start()
            time.sleep(0.5)
            if daemon.poll() is not None:
                raise RuntimeError('専用接続口を開始できません。上のSSHログを確認してください。')
            print('\nドギド専用接続を開始しました。', flush=True)
            print(f'接続口: {address}:{SSH_PORT} / 会話とデバッグログ専用。ファイル操作・モデル変更は禁止。')
            print(f'親Macのログ保存先: {debug.path}')
            print(f'息子MacへAirDropするファイル: {package}')
            print('息子MacでZIPを展開し「息子Macで接続を設定.command」を開いてください。')
            print('この画面は開いたままにします。終了は Ctrl+C。Qwen本体は止めません。', flush=True)
            while daemon.poll() is None:
                time.sleep(0.25)
            raise RuntimeError('専用接続が終了しました。上のSSHログを確認してください。')
        finally:
            stop_sshd(daemon)
            if proxy is not None:
                if worker is not None and worker.is_alive():
                    proxy.shutdown()
                proxy.server_close()
            if ssh_reader is not None:
                ssh_reader.join(timeout=1)
            if debug is not None:
                debug.emit('接続', '専用接続を停止しました')
                debug.close()
            print('\nドギド専用接続を停止しました。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=STATE)
    args = parser.parse_args()
    print('親Mac用: 専用鍵を作り、Qwenの会話だけを息子Macへ渡します。')
    print('sudo・OSアカウント追加・既存SSH/ファイル共有設定の変更はありません。')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        run(args.state.absolute(), Path(__file__).resolve().parent)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'開始できませんでした: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
