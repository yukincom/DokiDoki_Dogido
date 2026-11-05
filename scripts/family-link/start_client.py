"""Single visible launcher for the client PC's model link, Dogido and microphone."""
from __future__ import annotations
import fcntl
import argparse
import json
import os
import secrets
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.request import build_opener, ProxyHandler
from urllib.error import HTTPError


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
            raise RuntimeError(f'{label}が応答しません。LANサーバーPCの接続画面と共有モデルを確認してください。')
        if elapsed - announced >= 10:
            say(f'[準備中] {label} / {int(elapsed)}秒（終了はCtrl+C）')
            announced = elapsed
        time.sleep(0.3)


def run(root, lan_tools=None):
    os.chdir(root)
    os.environ.update(DOGIDO_ENV_PROFILE='shared', DOGIDO_VOICE_ECHO_CANCELLATION='webrtc',
                      DOGIDO_CHAT_IMAGE_ENABLED='true', DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED='false',
                      PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    sys.path.insert(0, str(root / 'client_tools'))
    sys.path.insert(0, str(root))
    from family_runtime import validate_runtime, server_url, is_rust_ready
    validate_runtime(root)
    from dogido_server.config import get_settings
    settings = get_settings()
    base_url = server_url(settings)
    if lan_tools is None and (root / 'lan_tools').is_dir():
        lan_tools = root / 'lan_tools'
    if lan_tools is not None:
        connector = lan_tools / 'connection.py'
        if not connector.is_file():
            raise RuntimeError('ドギドのLAN接続部品がありません。アプリを再配置してください。')
        # 同じ一括起動で作る本体・接続係だけが、選んだ遊ぶMacの伝言窓口を使用する。
        os.environ['DOGIDO_FAMILY_SESSION_KEY'] = secrets.token_hex(32)
        os.environ['DOGIDO_FAMILY_LOCAL_URL'] = base_url
        os.environ['DOGIDO_FAMILY_PLAYER'] = settings.default_call_name
        if settings.auth_token:
            os.environ['DOGIDO_AUTH_TOKEN'] = settings.auth_token
        connect = [sys.executable, '-u', str(connector), 'client']
    elif (root / '.dogido_tools/family-link/connection.json').is_file():
        # Only pre-existing, separately shipped SSH kits use this entry point.
        from family_link import command
        connect = command(root)
    else:
        raise RuntimeError('最初にLANサーバーPCから受け取った接続更新を適用してください。')
    # Do not adopt or kill independently started processes. Old 01/02/03 must
    # be stopped once when switching from the old manual launch procedure.
    for host, port, label in (('127.0.0.1', 8080, '01の接続'),
                              (settings.bind_host, settings.bind_port, '02の本体')):
        if port_open(port, host):
            raise RuntimeError(f'{label}が既に動いています。以前の03→02→01をCtrl+Cで閉じてから開いてください。')
    # 導入済みappでは起動スクリプトと同梱版を揃え、旧キットの同名モジュールを先に読まない。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from log_relay import Relay, stop_child, relay_dialogue
    children, relay = [], None
    dialogue_relay = None
    dialogue_stop = threading.Event()
    dialogue_worker = None
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
        say('[1/3] 共有モデルのMacを探しています。初回は両方の画面で番号を確認してください。'
            if lan_tools is not None else '[1/3] 旧セットの専用接続を開始します。')
        spawn('専用接続', connect)
        last_model_status = None
        def model_ready():
            nonlocal last_model_status
            try:
                status = json_response('http://127.0.0.1:8080/v1/models')
            except HTTPError as error:
                error.close()
                if error.code == 502 and last_model_status != 502:
                    say('[準備中] 共有窓口から有効なモデル応答が届きません。共有Macのモデル起動状態を確認してください。')
                last_model_status = error.code
                return False
            return isinstance(status, dict) and isinstance(status.get('data'), list)
        wait_ready('LANサーバーPCの共有モデル', model_ready,
                   children, say, timeout=None if lan_tools is not None else 30)
        relay = Relay('server')
        helpers = Path(__file__).resolve().parent
        spawn('Minecraftログ', [sys.executable, '-u', str(helpers / 'minecraft_log_relay.py')])
        say('[ログ] MinecraftはDogidoが付けた行だけを確認します。全ログは転送しません。')
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
        spawn('ドギド本体', [sys.executable, '-u', str(helpers / 'log_relay.py'), 'server'])
        def server_ready():
            return is_rust_ready(json_response(base_url + '/healthz'))
        wait_ready('ドギド本体', server_ready, children, say)
        dialogue_relay = Relay('dialogue')
        dialogue_worker = threading.Thread(target=relay_dialogue,
            args=(dialogue_stop, base_url, settings.auth_token, dialogue_relay.put), daemon=True)
        dialogue_worker.start()
        say('[3/3] 本体の準備ができたので、音声入力を開始します。')
        spawn('音声入力', [sys.executable, '-u', str(helpers / 'log_relay.py'), 'voice'])
        say('[待機] 音声入力の「待機中」が出たら、Minecraftで遊べます。この画面だけ開いたままにしてください。')
        say('[終了方法] Ctrl+C一回で音声入力・本体・専用接続を順に終了します。')
        while True:
            for name, child in children:
                if child.poll() is not None:
                    raise RuntimeError(f'{name}が終了しました（exit={child.returncode}）。残りも停止します。')
            time.sleep(0.3)
    except KeyboardInterrupt:
        say('[停止] 音声入力・本体・専用接続を終了します。')
    except (OSError, ValueError, RuntimeError) as error:
        say(f'[エラー] {error}')
        raise
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        dialogue_stop.set()
        if dialogue_worker is not None:
            dialogue_worker.join()
        if dialogue_relay is not None:
            dialogue_relay.close()
        for name, child in reversed(children):
            stop_child(child)
        if relay is not None:
            relay.close()
        print('[停止完了] ドギドの接続と処理を終了しました。', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kit', type=Path)
    parser.add_argument('--lan-tools', type=Path)
    args = parser.parse_args()
    root = args.kit.resolve()
    os.environ['DOGIDO_CLIENT_KIT'] = str(root)
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
        run(root, args.lan_tools.resolve() if args.lan_tools else None)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f'[起動停止] {error}', file=sys.stderr, flush=True)
        raise SystemExit(1)
