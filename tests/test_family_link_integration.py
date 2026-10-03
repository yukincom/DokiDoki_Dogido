"""Opt-in real OpenSSH tests; every temporary listener/process is stopped."""
from __future__ import annotations
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('DOGIDO_TEST_FAMILY_SSH') != '1',
                                reason='opt-in macOS loopback SSH integration')
ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
            return True
    except OSError:
        return False


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for expected process/listener state')


@pytest.mark.parametrize('stop_signal', [signal.SIGHUP, signal.SIGINT])
def test_real_gateway_restricts_ssh_http_and_stops_active_connections(stop_signal):
    assert not listening(22080) and not listening(22081), 'Dedicated gateway already running; leave it untouched'
    received = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"/private/model/path"}]}')
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'model': '/private/model/path',
                'choices': [{'message': {'role': 'assistant', 'content': '接続できました'}}]}).encode())
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    (ROOT / 'build').mkdir(exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.family ssh test ', dir=ROOT / 'build'))
    daemon = tunnel = None
    try:
        bundle = temporary / 'Parent folder with spaces'
        bundle.mkdir()
        (bundle / 'client-package').mkdir()
        shutil.copyfile(ROOT / 'scripts/family-link/gateway.py', bundle / 'gateway.py')
        state = temporary / 'State with spaces'
        wrapper = ("import gateway as g, sys; "
                   "g.lan_address=lambda:'127.0.0.1'; original=g.make_proxy; "
                   f"g.make_proxy=lambda **kw:original(**kw, upstream_port={upstream.server_port}); "
                   f"sys.argv=['gateway','--state',{str(state)!r}]; sys.exit(g.main())")
        with (temporary / 'process.log').open('w') as log:
            daemon = subprocess.Popen([sys.executable, '-u', '-c', wrapper], cwd=bundle,
                                      stdout=log, stderr=log, start_new_session=True)
        until(lambda: listening(22080) or daemon.poll() is not None)
        assert daemon.poll() is None, (temporary / 'process.log').read_text()
        public = (state / 'host_ed25519.pub').read_text().strip()
        known = temporary / 'known hosts'
        known.write_text('[127.0.0.1]:22080 ' + public + '\n')
        base = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-p', '22080', '-i', str(state / 'client_ed25519'),
                '-o', 'IdentityAgent=none', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile="{known}"',
                '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=3',
                '-o', 'ExitOnForwardFailure=yes', '-o', 'ControlPath=none']
        target = pwd.getpwuid(os.getuid()).pw_name + '@127.0.0.1'
        port = free_port()
        with (temporary / 'tunnel.log').open('w') as log:
            tunnel = subprocess.Popen(base + ['-N', '-L', f'127.0.0.1:{port}:127.0.0.1:22081', target],
                                      stdout=log, stderr=log, start_new_session=True)
        until(lambda: listening(port) or tunnel.poll() is not None)
        assert tunnel.poll() is None, (temporary / 'tunnel.log').read_text()
        def request(method, path, body=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                conn.request(method, path, body=None if body is None else json.dumps(body),
                             headers={'Content-Type': 'application/json'})
                result = conn.getresponse()
                return result.status, json.loads(result.read())
            finally:
                conn.close()
        code, models = request('GET', '/v1/models')
        assert code == 200 and [m['id'] for m in models['data']] == ['default_model']
        payload = {'model': 'default_model', 'messages': [{'role': 'user', 'content': '大丈夫？'}],
                   'max_tokens': 512, 'temperature': 0.7, 'stream': False,
                   'chat_template_kwargs': {'enable_thinking': False}}
        code, result = request('POST', '/v1/chat/completions', payload)
        assert code == 200 and result['model'] == 'default_model'
        assert received == [dict(payload, draft_model=None, adapters=None)]
        for extra in ({'model': 'untrusted/custom-code'}, {'draft_model': 'untrusted/custom-code'},
                      {'adapters': '/private/files'}, {'tools': []}):
            assert request('POST', '/v1/chat/completions', dict(payload, **extra))[0] == 400
        assert request('GET', '/etc/passwd')[0] == 404
        assert request('POST', '/v1/models', payload)[0] == 404
        assert len(received) == 1, 'Rejected requests must never reach Qwen'
        assert request('POST', '/dogido/client-logs',
                       {'source': 'server', 'lines': ['INFO: 動作確認', '\u001b[31mWARNING: 音声確認']})[0] == 202
        assert request('POST', '/dogido/client-logs',
                       {'source': '../arbitrary-file', 'lines': ['invalid']})[0] == 400
        # Run the shipped log wrapper with a tiny stand-in process: its actual
        # stdout must survive locally and arrive through SSH in the parent log.
        child = temporary / 'Child kit'
        (child / 'client_tools').mkdir(parents=True)
        (child / 'client_tools/check.py').write_text("print('OK: fixture endpoint check')\n")
        # Runtime is a stand-in here; this opt-in test owns only the SSH/log path.
        (child / 'client_tools/family_runtime.py').write_text(
            "import sys\ndef validate_runtime(root): pass\n"
            "def launch_command(root, **kwargs):\n"
            "    return [sys.executable, '-c', \"print('INFO: relay_process_sample')\"]\n")
        shutil.copyfile(ROOT / 'scripts/family-link/log_relay.py', child / 'client_tools/log_relay.py')
        relay_wrapper = ("import log_relay as r; from pathlib import Path; original=r.Relay; "
                         f"r.Relay=lambda src:original(src,port={port}); "
                         f"raise SystemExit(r.run('server',Path({str(child)!r})))")
        relay_result = subprocess.run([sys.executable, '-u', '-c', relay_wrapper],
                                      cwd=child / 'client_tools', text=True, capture_output=True, timeout=8)
        assert relay_result.returncode == 0, relay_result.stderr
        assert 'INFO: relay_process_sample' in relay_result.stdout
        assert any('INFO: relay_process_sample' in p.read_text() for p in (child / 'logs/family-client').glob('*.log'))
        records = [json.loads(line) for path in (state / 'logs').glob('*.jsonl')
                   for line in path.read_text().splitlines()]
        assert any(r['source'] == '息子/server' and r['message'] == 'INFO: 動作確認' for r in records)
        assert any(r['source'] == 'Qwen' and 'elapsed_ms' in r for r in records)
        assert any(r['source'] == '息子/server' and r['message'] == 'INFO: relay_process_sample' for r in records)
        assert all('\u001b' not in r['message'] for r in records)
        assert len(received) == 1, 'Logs must never reach Qwen'
        for arguments in ([target, 'printf UNEXPECTED_SHELL'], ['-s', target, 'sftp'],
                          ['-W', '127.0.0.1:8080', target], ['-W', '127.0.0.1:5055', target],
                          ['-N', '-R', '0:127.0.0.1:8080', target]):
            result = subprocess.run(base + arguments, input='', text=True, capture_output=True, timeout=5)
            assert result.returncode != 0 and 'UNEXPECTED_SHELL' not in result.stdout
        daemon.send_signal(stop_signal)
        daemon.wait(timeout=8)
        tunnel.wait(timeout=8)
        until(lambda: not any(listening(p) for p in (22080, 22081, port)))
        # Include sshd's command-line state path, not other user SSH processes.
        processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,command='], text=True)
        assert not any(str(state / 'sshd_config') in line for line in processes.splitlines())
        assert '専用接続を停止しました' in (temporary / 'process.log').read_text()
    finally:
        for process in (tunnel, daemon):
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
        upstream.shutdown()
        upstream.server_close()
        shutil.rmtree(temporary)


@pytest.mark.skipif(os.environ.get('DOGIDO_TEST_FAMILY_QWEN') != '1', reason='one opt-in real Qwen completion')
def test_fixed_proxy_real_qwen_completion():
    import importlib.util
    spec = importlib.util.spec_from_file_location('real_gateway', ROOT / 'scripts/family-link/gateway.py')
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    server = gateway.make_proxy(port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=45)
    try:
        payload = {'model': 'default_model', 'messages': [{'role': 'user', 'content': '接続確認です。「はい」とだけ返答してください。'}],
                   'temperature': 0, 'max_tokens': 16, 'stream': False}
        connection.request('POST', '/v1/chat/completions', body=json.dumps(payload),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200, body
        assert body['model'] == 'default_model'
        assert body['choices'][0]['message']['content'].strip()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_forced_voice_shutdown_stops_separate_session_grandchild():
    import importlib.util
    spec = importlib.util.spec_from_file_location('relay_shutdown', ROOT / 'scripts/family-link/log_relay.py')
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    grandchild_program = "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
    parent_program = ("import subprocess,sys,signal,time; "
                      "signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      f"p=subprocess.Popen([sys.executable,'-u','-c',{grandchild_program!r}],start_new_session=True,stdout=subprocess.PIPE,text=True); "
                      "p.stdout.readline(); print(p.pid,flush=True); time.sleep(60)")
    process = subprocess.Popen([sys.executable, '-u', '-c', parent_program], start_new_session=True,
                               stdout=subprocess.PIPE, text=True)
    grandchild = None
    try:
        grandchild = int(process.stdout.readline())
        relay.stop_child(process, grace=0.1)
        assert process.poll() is not None
        def stopped():
            result = subprocess.run(['/bin/ps', '-p', str(grandchild), '-o', 'stat='], text=True, capture_output=True)
            return not result.stdout.strip() or result.stdout.strip().startswith('Z')
        until(stopped, timeout=3)
    finally:
        for pid in (grandchild, process.pid):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait(timeout=3)


def test_large_japanese_and_control_logs_do_not_stall():
    import importlib.util
    from unittest.mock import patch
    spec = importlib.util.spec_from_file_location('relay_batch', ROOT / 'scripts/family-link/log_relay.py')
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    received = []
    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            size = int(self.headers['Content-Length'])
            data = self.rfile.read(size)
            self.send_response(400 if size > 1024 * 1024 else 202)
            self.end_headers()
            if size <= 1024 * 1024:
                received.extend(json.loads(data)['lines'])
    receiver = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()
    sender = None
    try:
        # Fill the queue before the sender starts to exercise the maximum batch.
        with patch.object(threading.Thread, 'start'):
            sender = relay.Relay('voice', port=receiver.server_port)
        original = ['あ' * 8192] * 31 + ['\x01' * 8192] * 31 + ['最後まで転送']
        for line in original:
            sender.put(line)
        sender.worker.start()
        until(lambda: len(received) == len(original), timeout=6)
        assert received == original
    finally:
        if sender is not None:
            sender.close()
        receiver.shutdown()
        receiver.server_close()
