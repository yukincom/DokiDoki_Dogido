"""Show child process output locally and relay debug lines without blocking it."""
from __future__ import annotations
import argparse
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid


def process_snapshot():
    return {int(pid): (int(parent), birth) for line in subprocess.check_output(
        ['/bin/ps', '-axo', 'pid=,ppid=,lstart='], text=True).splitlines()
        for pid, parent, birth in [line.split(maxsplit=2)]}


def freeze_children(root):
    frozen = {}
    pending = {root}
    while pending:
        snapshot = process_snapshot()
        for pid in pending:
            if pid not in snapshot:
                continue
            try:
                os.kill(pid, signal.SIGSTOP)
                frozen[pid] = snapshot[pid][1]
            except ProcessLookupError:
                pass
        pending = {pid for pid, (parent, _) in process_snapshot().items()
                   if parent in frozen and pid not in frozen}
    return frozen


def signal_owned(owned, sig):
    current = process_snapshot()
    for pid, birth in owned.items():
        if pid not in current or current[pid][1] != birth:
            continue  # Never signal a reused PID belonging to another process.
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def stop_child(process, grace=8):
    if process.poll() is not None:
        return
    owned = freeze_children(process.pid)
    signal_owned(owned, signal.SIGCONT)
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        owned.update(freeze_children(process.pid))
    # AEC may be in its own session. Retain the initial identities even after
    # the voice process exits and its descendants are reparented.
    signal_owned(owned, signal.SIGTERM)
    signal_owned(owned, signal.SIGCONT)
    time.sleep(0.1)
    signal_owned(owned, signal.SIGKILL)
    process.wait(timeout=3)


def encoded_logs(source, lines):
    # UTF-8 avoids the six-byte JSON escape expansion for every Japanese char.
    # Batches are also byte-bounded, including control-character expansion.
    return json.dumps({'source': source, 'lines': lines}, ensure_ascii=False).encode('utf-8')


class Relay:
    def __init__(self, source, port=8080):
        self.source, self.port = source, port
        self.lines = queue.Queue(maxsize=1024)
        self.done = threading.Event()
        self.dropped = 0
        self.worker = threading.Thread(target=self.send, daemon=True)
        self.worker.start()

    def put(self, line):
        for start in range(0, max(1, len(line)), 8192):
            try:
                self.lines.put_nowait(line[start:start + 8192])
            except queue.Full:
                self.dropped += 1

    def send(self):
        pending = []
        offline = False
        while not self.done.is_set() or pending or not self.lines.empty():
            if not pending:
                try:
                    pending.append(self.lines.get(timeout=0.1))
                except queue.Empty:
                    continue
            while len(pending) < 31:
                try:
                    pending.append(self.lines.get_nowait())
                except queue.Empty:
                    break
            dropped = self.dropped
            batch = ([f'[ログ転送] 混雑により {dropped} 行省略。息子Macのログには保存済み。']
                     if dropped else []) + pending
            payload = encoded_logs(self.source, batch)
            count = len(pending)
            while len(payload) > 900_000:
                batch.pop()
                count -= 1
                payload = encoded_logs(self.source, batch)
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=1)
            try:
                connection.request('POST', '/dogido/client-logs',
                                   body=payload,
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                response.read(4096)
                if response.status != 202:
                    raise OSError('Log receiver unavailable')
                self.dropped -= dropped
                del pending[:count]
                if offline:
                    print('[ログ転送] 親Macへの転送が復旧しました。', flush=True)
                offline = False
            except (OSError, http.client.HTTPException):
                if not offline:
                    print('[ログ転送] 親Macへの転送を待っています。息子Macへの保存は継続します。', flush=True)
                offline = True
                if self.done.wait(0.5):
                    break
            finally:
                connection.close()

    def close(self):
        self.done.set()
        self.worker.join(timeout=2)


def run(source, root):
    sys.path.insert(0, str(root / 'client_tools'))
    from family_runtime import launch_command, validate_runtime
    validate_runtime(root)
    log_dir = root / 'logs/family-client'
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / (datetime.now().strftime('%Y%m%d-%H%M%S-') + source + '-' + uuid.uuid4().hex[:6] + '.log')
    relay = Relay(source)
    process = None
    def interrupt(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupt)
    env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', DOGIDO_ENV_PROFILE='shared')
    commands = [[sys.executable, '-u', 'client_tools/check.py', '--network' if source == 'server' else '--server'],
                launch_command(root, voice=source == 'voice')]
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as log:
        def output(line):
            print(line, flush=True)
            if not log.closed:
                try:
                    log.write(line + '\n')
                    log.flush()
                except OSError:
                    try:
                        log.close()
                    except OSError:
                        pass
                    print('[ログ保存] 保存に失敗しました。画面表示と親への転送は継続します。', flush=True)
            relay.put(line)
        try:
            output(f'[起動] {source} / 息子Macの保存先: {path}')
            for stage, argv in enumerate(commands):
                if stage == 0:
                    output(f'[事前確認] {source} / 接続を確認します')
                elif source == 'server':
                    output('[本体起動] Rust本体を起動します。「server_listening」が出てから03_voice.commandを開いてください。')
                else:
                    output('[音声起動] マイク入力を開始します')
                process = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, errors='replace',
                                           start_new_session=True)
                while line := process.stdout.readline(8192):
                    output(line.rstrip('\r\n'))
                code = process.wait()
                if stage == 0:
                    if code == 0:
                        output(f'[事前確認OK] {source} / 続けて本体を起動します')
                    elif source == 'voice':
                        output(f'[音声起動前に停止] exit={code} / 02の起動完了後に03をもう一度開いてください。マイクはまだ開始していません。')
                    else:
                        output(f'[事前確認NG] {source} / exit={code} / 上のエラーを確認してください')
                else:
                    output(f'[本体終了] {source} / exit={code}')
                if code:
                    return code
            return 0
        except KeyboardInterrupt:
            output(f'[停止] {source} / 終了操作を受け付けました')
            return 0
        finally:
            # The foreground command owns this child only; never terminate other
            # Minecraft, Qwen, VOICEVOX or Dogido instances by process name.
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
            if process is not None:
                stop_child(process)
            relay.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', choices=['server', 'voice'])
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.source, Path(__file__).resolve().parents[1]))
    except (OSError, ValueError) as error:
        raise SystemExit(f'起動できません: {error}') from error
