"""Single visible launcher for the son's Qwen connection, Dogido and microphone."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler


def json_response(url):
    with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
        return json.load(response)


def port_open(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def wait_ready(label, check, children, say, timeout=None):
    started, announced = time.monotonic(), -10
    while True:
        for name, child in children:
            if child.poll() is not None:
                raise RuntimeError(f'{name}が終了しました（exit={child.returncode}）。上のログを確認してください。')
        try:
            if check():
                say(f'[準備OK] {label}')
                return
        except (OSError, ValueError):
            pass
        elapsed = time.monotonic() - started
        if timeout is not None and elapsed >= timeout:
            raise RuntimeError(f'{label}が応答しません。親Macの接続画面とQwenを確認してください。')
        if elapsed - announced >= 10:
            say(f'[準備中] {label} / {int(elapsed)}秒（終了はCtrl+C）')
            announced = elapsed
        time.sleep(0.3)


def run(root):
    os.chdir(root)
    os.environ.update(DOGIDO_ENV_PROFILE='shared', DOGIDO_VOICE_ECHO_CANCELLATION='webrtc',
                      PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    sys.path.insert(0, str(root / 'client_tools'))
    sys.path.insert(0, str(root))
    from family_runtime import validate_runtime, server_url, is_rust_ready
    validate_runtime(root)
    from dogido_server.config import get_settings
    settings = get_settings()
    base_url = server_url(settings)
    if not (root / '.dogido_tools/family-link/connection.json').is_file():
        raise RuntimeError('最初に親Macから受け取った接続更新を適用してください。')
    # Do not adopt or kill independently started processes. Old 01/02/03 must
    # be stopped once when switching from the old manual launch procedure.
    for host, port, label in (('127.0.0.1', 8080, '01の接続'),
                              (settings.bind_host, settings.bind_port, '02の本体')):
        if port_open(port, host):
            raise RuntimeError(f'{label}が既に動いています。以前の03→02→01をCtrl+Cで閉じてから開いてください。')
    from family_link import command
    from log_relay import Relay, stop_child
    children, relay = [], None
    def say(message):
        print(message, flush=True)
        if relay is not None:
            relay.put(message)
    def interrupt(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupt)
    def spawn(name, argv):
        child = subprocess.Popen(argv, cwd=root, start_new_session=True)
        children.append((name, child))
        return child
    try:
        say('[1/3] 親Macへ専用鍵で接続します。パスワードは入力しません。')
        spawn('専用接続', command(root))
        def qwen_ready():
            status = json_response('http://127.0.0.1:8080/v1/models')
            return isinstance(status, dict) and isinstance(status.get('data'), list)
        wait_ready('親MacのQwen', qwen_ready,
                   children, say, timeout=30)
        relay = Relay('server')
        version_url = settings.voicevox_url.rstrip('/') + '/version'
        def voicevox_ready():
            value = json_response(version_url)
            return isinstance(value, str) and bool(value.strip())
        try:
            ready = voicevox_ready()
        except (OSError, ValueError):
            ready = False
        if not ready:
            say('[準備] このMacのVOICEVOXを開きます。')
            result = subprocess.run(['/usr/bin/open', '-a', 'VOICEVOX'], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError('VOICEVOXを自動で開けませんでした。VOICEVOXを開いてから再実行してください。')
            wait_ready('VOICEVOX', voicevox_ready, children, say)
        say('[2/3] Rustのドギド本体を起動します。')
        spawn('ドギド本体', [sys.executable, '-u', 'client_tools/log_relay.py', 'server'])
        def server_ready():
            return is_rust_ready(json_response(base_url + '/healthz'))
        wait_ready('ドギド本体', server_ready, children, say)
        say('[3/3] 本体の準備ができたので、音声入力を開始します。')
        spawn('音声入力', [sys.executable, '-u', 'client_tools/log_relay.py', 'voice'])
        say('[待機] 音声入力の「待機中」が出たら、Minecraftで遊べます。この画面だけ開いたままにしてください。')
        say('[終了方法] Ctrl+C一回で音声入力・本体・専用接続を順に終了します。')
        while True:
            for name, child in children:
                if child.poll() is not None:
                    raise RuntimeError(f'{name}が終了しました（exit={child.returncode}）。残りも停止します。')
            time.sleep(0.3)
    except KeyboardInterrupt:
        say('[停止] 音声入力・本体・専用接続を終了します。')
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        for name, child in reversed(children):
            stop_child(child)
        if relay is not None:
            relay.close()
        print('[停止完了] ドギドの接続と処理を終了しました。', flush=True)


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / 'client_tools'))
    from family_runtime import validate_runtime
    validate_runtime(root)
    lock_path = root / '.dogido_tools/family-launch.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('ドギドの起動画面は既に開いています。その画面を使ってください。')
        run(root)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f'[起動停止] {error}', file=sys.stderr, flush=True)
        raise SystemExit(1)
