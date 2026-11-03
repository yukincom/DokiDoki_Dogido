"""LAN中継の固定契約を検査する。模擬HTTP・一時ファイルだけを使い、実モデル・実Wi-Fi・実音声は確認しない。"""
from __future__ import annotations
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unicodedata
import runpy
import signal
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/family-link'


def load(name):
    spec = importlib.util.spec_from_file_location('family_' + name, SOURCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = load('gateway')
client = load('client')
installer = load('apply_client')
log_relay = load('log_relay')
minecraft_log = load('minecraft_log_relay')

link_builder_spec = importlib.util.spec_from_file_location(
    'family_link_builder', SOURCE.parent / 'build_family_link.py')
link_builder = importlib.util.module_from_spec(link_builder_spec)
link_builder_spec.loader.exec_module(link_builder)

call_name_spec = importlib.util.spec_from_file_location(
    'family_call_name', SOURCE.parent / 'family-client/configure_call_name.py')
call_name = importlib.util.module_from_spec(call_name_spec)
call_name_spec.loader.exec_module(call_name)


def prepared_runtime(kit):
    runtime = runpy.run_path(str(SOURCE.parent / 'family-client/family_runtime.py'))
    for relative in runtime['runtime_files']():
        path = kit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('FIXTURE RUNTIME')
    (kit / runtime['RUST_BINARY']).chmod(0o755)
    normalized = kit / 'reference/language_education_and_poetry'
    (normalized / 'index.json').write_text('{"datasets":[{"path":"fixture.json"}]}')
    (normalized / 'fixture.json').write_text('{}\n')


def valid():
    return {'model': 'default_model', 'messages': [{'role': 'user', 'content': '大丈夫？'}],
            'temperature': 0.7, 'max_tokens': 512, 'stream': False,
            'chat_template_kwargs': {'enable_thinking': False}}


def stream_request():
    return valid() | {'stream': True, 'stream_options': {'include_usage': True}}


RUST_TOOL_PARAMETERS = {
    # Literal copies of the currently serialized schemas in
    # dogido-rust/src/workshop/{inspection_tool,tools}.rs. Do not derive these
    # from gateway.py: this test must fail if the relay drifts independently.
    'inspect_verse': {
        'type': 'object',
        'properties': {
            'checks': {'type': 'array', 'items': {'type': 'string', 'enum': ['reading', 'source']},
                       'minItems': 1, 'maxItems': 2, 'uniqueItems': True},
            'inspect_text': {'type': 'string', 'minLength': 1, 'maxLength': 160},
            'inspect_line_index': {'type': 'integer', 'minimum': 0, 'maximum': 2},
        },
        'required': ['checks'],
        'additionalProperties': False,
    },
    'stage_player_edit': {
        'type': 'object',
        'properties': {
            'replacement_text': {
                'type': 'string',
                'description': '変更後の一行全体。部分変更でも残す言葉を含め、行IDや他の二行は含めない。元の行と同じかどうかはコードが比較する。',
            },
            'evidence': {'type': 'string', 'description': '今回の変更依頼を解釈した根拠。記録用。'},
            'confidence': {'type': 'number', 'description': '任意の自己申告値。記録用。'},
        },
        'required': ['replacement_text'],
        'additionalProperties': False,
    },
}


def tool_definition(name):
    return {'type': 'function', 'function': {
        'name': name, 'description': '現行ドギドの固定tool',
        'parameters': RUST_TOOL_PARAMETERS[name]}}


def complete_update_package(package, overrides=None):
    overrides = overrides or {}
    for relative in ('apply_client.py', '息子Macで接続を設定.command'):
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('FIXTURE ' + relative)
    shutil.copyfile(SOURCE.parent / 'family-client/family_runtime.py', package / 'family_runtime.py')
    for relative in installer.ALLOWED_UPDATES:
        path = package / 'updates' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(overrides.get(relative, 'FIXTURE UPDATE ' + relative))
    link = package / 'link'
    link.mkdir(parents=True, exist_ok=True)
    for name in ('identity', 'known_hosts', 'connection.json'):
        (link / name).write_text('FIXTURE ' + name)
    manifest = {
        relative: hashlib.sha256((package / relative).read_bytes()).hexdigest()
        for relative in installer.MANIFEST_FILES
    }
    (package / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))


# 見込み: build時にclient-package自身の完全目録を入れれば、LANサーバーPCが作る最終ZIPにも検証材料が残る。
# 合格: builder出力のmanifestがapply側の固定集合と一致し全hashを再計算できる。実AirDrop・Archive Utility展開は未検査。
def test_link_builder_embeds_complete_client_manifest(tmp_path):
    output = tmp_path / 'link bundle'
    link_builder.build(output)
    client = output / 'client-package'
    manifest = json.loads((client / 'manifest.json').read_text())
    assert set(manifest) == installer.STATIC_PACKAGE_FILES
    assert all(
        hashlib.sha256((client / relative).read_bytes()).hexdigest() == digest
        for relative, digest in manifest.items()
    )
    entries = {path.relative_to(client).as_posix(): path.read_bytes()
               for path in client.rglob('*') if path.is_file()}
    for name in installer.LINK_FILES:
        entries[name] = ('DYNAMIC ' + name).encode()
    final = json.loads(gateway.finalized_client_manifest(entries))
    assert set(final) == installer.MANIFEST_FILES


def test_preserves_dogido_request_and_fixes_model_loader():
    payload = gateway.checked_payload(valid())
    assert payload['messages'] == valid()['messages']
    assert payload['max_tokens'] == 512
    assert payload['draft_model'] is None and payload['adapters'] is None
    assert payload['model'] == 'default_model'


# 見込み: 現行Rustと同じstream・PNG・既知toolだけを再構成すれば、画像会話と相談を汎用API化せず中継できる。
# 合格: 192px以下のPNG二枚、usage付きstream、二つの既知toolと結果を保持し、固定model・thinking無効のままになる。実モデル解釈は対象外。
def test_accepts_current_stream_images_and_known_workshop_tools():
    png = base64.b64encode(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )).decode()
    tools = [tool_definition('inspect_verse'), tool_definition('stage_player_edit')]
    request = stream_request() | {
        'messages': [
            {'role': 'system', 'content': 'ドギドの指示'},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': '古い順やで'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + png}},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + png}},
            ]},
            {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'call-1', 'type': 'function',
                'function': {'name': 'inspect_verse', 'arguments': '{"checks":["reading"]}'},
            }]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'name': 'inspect_verse',
             'content': '{"reading":"しずく"}'},
        ],
        'tools': tools,
        'tool_choice': 'none',
    }
    checked = gateway.checked_payload(request)
    assert checked['stream'] is True
    assert checked['stream_options'] == {'include_usage': True}
    assert checked['tools'] == tools
    assert checked['messages'] == request['messages']
    assert checked['model'] == 'default_model'
    assert checked['chat_template_kwargs'] == {'enable_thinking': False}


# 見込み: URL・任意tool・過大画像を閉じれば、専用鍵を持つクライアントからも任意ファイルや汎用モデル操作へ広がらない。
# 合格: HTTP/file画像、三枚目、未知tool、変更されたschema、usageなしstream、内部表現flagを入口で拒否し、上流へは送らない。SSH実接続は別試験。
@pytest.mark.parametrize('override', [
    {'stream': True},
    {'stream': True, 'stream_options': {'include_usage': False}},
    {'dogido_capture': True},
    {'tools': [tool_definition('inspect_verse') | {'type': 'other'}], 'tool_choice': 'auto'},
    {'tools': [{'type': 'function', 'function': {'name': 'unknown', 'description': 'x',
                                                'parameters': {'type': 'object'}}}], 'tool_choice': 'auto'},
])
def test_rejects_stream_or_tool_shapes_outside_current_contract(override):
    with pytest.raises(ValueError):
        gateway.checked_payload(valid() | override)


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://example.invalid/a.png',
                                 'data:image/jpeg;base64,AAAA', 'data:image/png;base64,%%%%'])
def test_rejects_non_inline_png_image_urls(url):
    request = valid() | {'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': '画像'}, {'type': 'image_url', 'image_url': {'url': url}}]}]}
    with pytest.raises(ValueError):
        gateway.checked_payload(request)


# 見込み: SSEを逐次書き換えれば、文単位再生を止めず、LANサーバー側の実モデル名だけを隠せる。
# 合格: 一つ目のdata行を上流完了前に受信し、modelはdefault_model、最終usageはログへ渡る。内容の意味・音声再生は未検査。
def test_streaming_proxy_flushes_sse_and_redacts_model_name():
    first_sent, release = threading.Event(), threading.Event()
    received = []
    class Upstream(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            chunks = [
                {'id': 'a', 'model': '/private/model.gguf', 'choices': [{'delta': {'content': '一文目。'}}]},
                {'id': 'a', 'model': '/private/model.gguf', 'choices': [],
                 'usage': {'prompt_tokens': 7, 'completion_tokens': 3}},
            ]
            payloads = [(b'data: ' + json.dumps(chunk, ensure_ascii=False).encode() + b'\n\n')
                        for chunk in chunks] + [b'data: [DONE]\n\n']
            total = sum(map(len, payloads))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(total))
            self.end_headers()
            self.wfile.write(payloads[0])
            self.wfile.flush()
            first_sent.set()
            release.wait(3)
            for payload in payloads[1:]:
                self.wfile.write(payload)
                self.wfile.flush()
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    proxy_events = []
    proxy = gateway.make_proxy(port=0, upstream_port=upstream.server_port,
                               event=lambda *args, **kwargs: proxy_events.append((args, kwargs)))
    workers = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (upstream, proxy)]
    for worker in workers:
        worker.start()
    connection = http.client.HTTPConnection('127.0.0.1', proxy.server_port, timeout=4)
    try:
        request = stream_request()
        connection.request('POST', '/v1/chat/completions', json.dumps(request),
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 200 and first_sent.wait(1)
        first = response.readline()
        assert b'default_model' in first and b'private' not in first
        release.set()
        rest = response.read()
        assert b'data: [DONE]' in rest
        assert received == [gateway.checked_payload(request)]
        completed = [details for (args, details) in proxy_events if args[:2] == ('モデル', '生成完了')]
        assert completed[0]['usage'] == {'prompt_tokens': 7, 'completion_tokens': 3}
    finally:
        release.set()
        connection.close()
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join(timeout=2)


# 見込み: `[DONE]`を一度だけ必須にすれば、上流切断を完了として記録せずRustにも不完全streamとして返せる。
# 合格: 正常終端だけ完了し、欠落・重複・終端後dataを拒否する。HTTP切断後のRust側表示・音声確定は別の実接続確認。
def test_sse_filter_requires_exactly_one_terminal_marker():
    complete = gateway.SseFilter()
    assert b'[DONE]' not in complete.feed(b'data: {"choices":[]}\n\ndata: [DONE]\n\n')
    assert complete.finish() == b'data: [DONE]\n\n'

    missing = gateway.SseFilter()
    missing.feed(b'data: {"choices":[]}\n\n')
    with pytest.raises(ValueError, match='terminator'):
        missing.finish()

    duplicate = gateway.SseFilter()
    with pytest.raises(ValueError, match='Duplicate'):
        duplicate.feed(b'data: [DONE]\n\ndata: [DONE]\n\n')

    after = gateway.SseFilter()
    with pytest.raises(ValueError, match='after terminator'):
        after.feed(b'data: [DONE]\n\ndata: {"choices":[]}\n\n')


# 見込み: active二件と有界waitingを分ければ、複数クライアントの通常重なりを許しつつ共有モデルを無制限に占有しない。
# 合格: active一件中は一件だけwaitingへ入り、三件目は即時拒否、解放後waitingが開始する。実Gemma待ち時間は実機測定。
def test_generation_gate_bounds_active_and_waiting_calls():
    gate = gateway.GenerationGate(active=1, waiting=1, wait_seconds=1)
    entered = threading.Event()
    finished = threading.Event()

    def wait_for_turn():
        with gate.claim():
            entered.set()
        finished.set()

    with gate.claim():
        worker = threading.Thread(target=wait_for_turn)
        worker.start()
        deadline = time.monotonic() + 1
        while gate.capacity._value != 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert gate.capacity._value == 0
        with pytest.raises(gateway.GenerationBusy):
            with gate.claim():
                pass
        assert not entered.is_set()
    assert entered.wait(1) and finished.wait(1)
    worker.join(timeout=1)


# 見込み: 管理対象の拡張子と24時間だけで整理すれば、直近・書込中・他用途のファイルを消さず容量を限定できる。
# 合格: 期限超過jsonlだけ一件削除し、当日jsonl・除外中jsonl・txt・リンク先は保持。時刻は固定し、実日跨ぎは未検査。
def test_gateway_log_retention_deletes_only_expired_managed_files(tmp_path):
    now = 2_000_000_000
    old = tmp_path / 'old.jsonl'
    recent = tmp_path / 'recent.jsonl'
    active = tmp_path / 'active.jsonl'
    other = tmp_path / 'other.txt'
    for path in (old, recent, active, other):
        path.write_text(path.name)
    os.utime(old, (now - gateway.LOG_RETENTION_SECONDS - 1,) * 2)
    os.utime(active, (now - gateway.LOG_RETENTION_SECONDS - 1,) * 2)
    os.utime(recent, (now - 60,) * 2)
    assert gateway.prune_logs(tmp_path, '.jsonl', now=now, exclude=(active,)) == 1
    assert not old.exists()
    assert recent.exists() and active.exists() and other.exists()


# 見込み: 両PCで同じ24時間境界と閉じた拡張子を使えば、実行ログだけを日々整理し、記憶や研究データへ触れない。
# 合格: クライアント側でも期限超過logだけを削除し、現在書込み中・当日・JSONL以外を保持する。実日跨ぎとSSD容量は未検査。
def test_client_log_retention_deletes_only_expired_managed_files(tmp_path):
    now = 2_000_000_000
    old, server, voice, minecraft, other = (
        tmp_path / name for name in ('old.log', 'server.log', 'voice.log', 'minecraft.log', 'review.jsonl'))
    for path in (old, other):
        path.write_text(path.name)
    os.utime(old, (now - log_relay.LOG_RETENTION_SECONDS - 1,) * 2)
    with log_relay.managed_log(server), log_relay.managed_log(voice), log_relay.managed_log(minecraft):
        for path in (server, voice, minecraft):
            os.utime(path, (now - log_relay.LOG_RETENTION_SECONDS - 1,) * 2)
        assert log_relay.prune_logs(tmp_path, '.log', now=now) == 1
        assert all(path.exists() for path in (server, voice, minecraft))
    assert not old.exists() and other.exists()


# 見込み: 実Fabricのmessage先頭と旧logger表記を閉じて選べば、Dogido診断と直後のstackだけを転送できる。
# 合格: 実latest.log形式のDogido行・既知body診断・継続stackを採用し、player chat・他mod・別recordを除外。tail実行は実機確認。
def test_minecraft_log_filter_keeps_only_dogido_logger_records_and_stack():
    filter_ = minecraft_log.DogidoLogFilter()
    lines = [
        '[14:05:59] [Render thread/INFO]: Dogido client adapter ready: target=http://127.0.0.1:5055',
        '    at dogido.fabric.Client.run(Client.java:1)',
        '[14:06:07] [ForkJoinPool.commonPool-worker-1/INFO]: Dogido session created: ses_fixture',
        '[14:06:09] [dogido-body-view-io/INFO]: Body view capture saved: view_fixture annotation="available"',
        '[14:06:10] [Render thread/INFO]: <player> Dogido is fun',
        '    at unrelated.Trace.run(Trace.java:2)',
        '[12:00:02] [Render thread/WARN] (dogido-character-hud) placement failed',
        'Caused by: java.io.IOException: fixture',
        '[12:00:03] [Render thread/INFO]: unrelated done',
    ]
    assert [line for line in lines if filter_.accepts(line)] == [
        lines[0], lines[1], lines[2], lines[3], lines[6], lines[7]]


# 見込み: 呼び名をkit内.envの一項目だけへ保存すれば、発話名を変えてもMinecraft identity・記憶先・秘密設定は変わらない。
# 合格: 日本語・記号をquoted値で保存し他行はbyte相当で保持、空・制御文字・重複キーを拒否する。実VOICEVOXの読みは未検査。
def test_call_name_updates_only_local_env_key(tmp_path):
    env = tmp_path / '.env'
    env.write_text('DOGIDO_MEMORY_DIR=.dogido_memory\nDOGIDO_DEFAULT_CALL_NAME=プレイヤー\nPRIVATE=value#keep\n')
    call_name.update_env(env, '  まい#ちゃん  ')
    assert env.read_text() == ('DOGIDO_MEMORY_DIR=.dogido_memory\n'
                               'DOGIDO_DEFAULT_CALL_NAME="まい#ちゃん"\n'
                               'PRIVATE=value#keep\n')
    assert env.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        call_name.update_env(env, 'bad\nname')
    env.write_text('DOGIDO_DEFAULT_CALL_NAME=a\nDOGIDO_DEFAULT_CALL_NAME=b\n')
    with pytest.raises(ValueError, match='重複'):
        call_name.update_env(env, 'name')


def test_default_state_reuses_existing_legacy_keys_without_moving_them(tmp_path):
    legacy = tmp_path / 'Library/Application Support' / gateway.LEGACY_STATE_NAME
    legacy.mkdir(parents=True)
    assert gateway.default_state(tmp_path) == legacy
    current = legacy.parent / gateway.NEW_STATE_NAME
    current.mkdir()
    assert gateway.default_state(tmp_path) == current


# 見込み: listener終了後も記録済み子接続を個別確認すれば、別process groupへ移った認証済み転送を残さない。
# 合格: TERMを無視する別sessionの子までstop_sshdが終了させ、PID再利用時はbirth照合で別processを触らない。実OpenSSHはopt-in試験。
@pytest.mark.parametrize('listener_exited_first', [False, True])
def test_stop_sshd_kills_captured_child_after_listener_exit(listener_exited_first):
    child_program = (
        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "print('ready',flush=True); time.sleep(60)"
    )
    parent_program = (
        "import subprocess,sys,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"p=subprocess.Popen([sys.executable,'-u','-c',{child_program!r}],"
        "start_new_session=True,stdout=subprocess.PIPE,text=True); "
        "p.stdout.readline(); print(p.pid,flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, '-u', '-c', parent_program],
        start_new_session=True, stdout=subprocess.PIPE, text=True)
    child = None
    try:
        child = int(process.stdout.readline())
        owned = {}
        gateway.track_process_tree(process.pid, owned)
        assert child in owned
        if listener_exited_first:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        gateway.stop_sshd(process, owned, grace=0.1)
        assert process.poll() is not None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = subprocess.run(
                ['/bin/ps', '-p', str(child), '-o', 'stat='], text=True, capture_output=True)
            if not result.stdout.strip() or result.stdout.strip().startswith('Z'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('captured SSH child remained alive')
    finally:
        for pid in (child, process.pid):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait(timeout=3)


# 見込み: 保存済みPIDだけでなくbirthも親子edgeごとに照合すれば、長時間稼働中のPID再利用で無関係なprocessを所有扱いしない。
# 合格: 既知親PIDのbirthが変わったsnapshotでは、その子も同じsessionのprocessも追跡へ追加しない。実SSH停止は別のopt-in確認。
def test_process_tree_tracker_rejects_reused_parent_pid(monkeypatch):
    owned = {100: 'original-birth'}
    monkeypatch.setattr(gateway, 'process_snapshot', lambda: {
        100: (1, 'reused-birth', 'S', 100),
        101: (100, 'unrelated-child', 'S', 100),
    })
    gateway.track_process_tree(100, owned)
    assert owned == {100: 'original-birth'}


def test_process_tree_tracker_refreshes_reused_child_inside_proven_session(monkeypatch):
    owned = {100: 'root-birth', 101: 'old-child-birth'}
    monkeypatch.setattr(gateway, 'process_snapshot', lambda: {
        100: (1, 'root-birth', 'S', 100),
        101: (100, 'new-child-birth', 'S', 100),
    })
    gateway.track_process_tree(100, owned)
    assert owned == {100: 'root-birth', 101: 'new-child-birth'}


@pytest.mark.parametrize('override', [
    {'model': '/tmp/custom-model'}, {'model': 'someone/custom-code'},
    {'adapters': '/tmp/adapters'}, {'draft_model': 'another/model'},
    {'tools': []}, {'chat_template': 'custom'},
    {'chat_template_kwargs': {'enable_thinking': False, 'tools': []}},
    {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'file:///etc/passwd'}]}]},
    {'messages': [{'role': [], 'content': 'invalid'}]},
    {'messages': [{'role': 'user', 'content': 'hello', 'tool_calls': []}]},
    {'stream': True}, {'stream': 0}, {'temperature': float('nan')},
    {'max_tokens': True}, {'max_tokens': -1},
])
def test_rejects_non_conversation_requests(override):
    with pytest.raises(ValueError):
        gateway.checked_payload(valid() | override)


def test_client_pins_host_key_and_cannot_request_password(tmp_path):
    root = tmp_path / 'Folder with spaces'
    state = root / '.dogido_tools/family-link'
    state.mkdir(parents=True)
    (state / 'identity').write_text('test-placeholder')
    (state / 'known_hosts').write_text('')
    (state / 'connection.json').write_text(json.dumps(
        {'host': '192.168.1.48', 'port': 22080, 'remote_port': 22081,
         'local_port': 8080, 'user': 'example'}))
    argv = client.command(root)
    result = subprocess.run(argv[:1] + ['-G'] + argv[1:], text=True, capture_output=True, check=True)
    config = result.stdout
    for setting in ('batchmode yes', 'passwordauthentication no', 'kbdinteractiveauthentication no',
                    'stricthostkeychecking true', 'identitiesonly yes', 'identityagent none'):
        assert setting in config.splitlines()
    assert 'userknownhostsfile ' + str(state / 'known_hosts') in config.splitlines()
    assert argv[-1] == 'example@192.168.1.48'


def test_client_update_preserves_worlds_memories_environment_and_backup(tmp_path):
    kit = tmp_path / 'existing kit'
    package = tmp_path / 'update'
    for path, text in {
        '00_setup.command': 'OLD SETUP', '.env.shared': 'OLD SHARED SETTINGS',
        '.env': 'PRIVATE USER SETTINGS', '.dogido_memory/entries.jsonl': 'KEEP MEMORY',
        'minecraft/saves/MyWorld/level.dat': 'KEEP WORLD',
        'dogido_server/__init__.py': 'KEEP RUNTIME', 'connection.txt': 'old-target',
        '01_connect.command': 'OLD CONNECTOR'
    }.items():
        file = kit / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    prepared_runtime(kit)
    before = {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
    complete_update_package(package, {'01_connect.command': 'NEW CONNECTOR'})
    installer.apply(package, kit)
    installer.apply(package, kit)
    for relative, data in before.items():
        if str(relative) not in {'connection.txt', *installer.ALLOWED_UPDATES}:
            assert (kit / relative).read_bytes() == data
    assert (kit / '01_connect.command').read_text() == 'NEW CONNECTOR'
    backup = kit / '.dogido_tools/setup-backups/qwen-link-v1'
    assert (backup / '01_connect.command').read_text() == 'OLD CONNECTOR'
    assert (backup / 'connection.txt').read_text() == 'old-target'
    assert (kit / '.dogido_tools/family-link/identity').stat().st_mode & 0o777 == 0o600
    assert (kit / '01_connect.command').stat().st_mode & 0o777 == 0o755


def test_installer_does_not_accept_arbitrary_folder(tmp_path):
    with pytest.raises(ValueError):
        installer.apply(tmp_path, tmp_path)


def test_installer_rejects_extra_files_before_any_update(tmp_path):
    kit, package = tmp_path / 'kit', tmp_path / 'package'
    kit.mkdir()
    for name in ('00_setup.command', '.env.shared', 'dogido_server', 'minecraft'):
        if name == 'dogido_server':
            (kit / name).mkdir()
        else:
            (kit / name).touch()
    prepared_runtime(kit)
    (kit / '01_connect.command').write_text('OLD')
    (kit / '.env').write_text('USER SETTINGS')
    complete_update_package(package, {'01_connect.command': 'NEW'})
    (package / 'updates/.env').write_text('ACCIDENTAL OVERWRITE')
    with pytest.raises(ValueError, match='更新対象外'):
        installer.apply(package, kit)
    assert (kit / '01_connect.command').read_text() == 'OLD'
    assert (kit / '.env').read_text() == 'USER SETTINGS'
    assert not (kit / '.dogido_tools').exists()


@pytest.mark.parametrize('normalization', ['NFC', 'NFD'])
def test_installer_accepts_mac_extracted_names_and_ignores_finder_metadata(tmp_path, normalization):
    kit, package = tmp_path / 'existing kit', tmp_path / 'Mac extracted update'
    kit.mkdir()
    (kit / '00_setup.command').write_text('OLD SETUP')
    (kit / '.env.shared').write_text('SHARED SETTINGS')
    (kit / '.env').write_text('USER SETTINGS')
    (kit / 'dogido_server').mkdir()
    prepared_runtime(kit)
    (kit / 'minecraft/saves').mkdir(parents=True)
    (kit / 'minecraft/saves/world.dat').write_bytes(b'KEEP WORLD')
    (kit / '.dogido_memory').mkdir()
    (kit / '.dogido_memory/entries.jsonl').write_text('KEEP MEMORY')
    complete_update_package(package, {
        'はじめに.md': 'UPDATED INSTRUCTIONS',
        '01_connect.command': 'NEW CONNECTOR',
    })
    canonical = package / 'updates/はじめに.md'
    name = unicodedata.normalize(normalization, 'はじめに.md')
    if canonical.name != name:
        canonical.rename(canonical.with_name(name))
    metadata = ['.DS_Store', '._はじめに.md', '__MACOSX/._はじめに.md', 'client_tools/.DS_Store']
    for relative in metadata:
        source = package / 'updates' / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'FINDER METADATA')
    installer.apply(package, kit)
    installer.apply(package, kit)
    assert (kit / 'はじめに.md').read_text() == 'UPDATED INSTRUCTIONS'
    assert (kit / '01_connect.command').read_text() == 'NEW CONNECTOR'
    assert (kit / '.env').read_text() == 'USER SETTINGS'
    assert (kit / '.env.shared').read_text() == 'SHARED SETTINGS'
    assert (kit / 'minecraft/saves/world.dat').read_bytes() == b'KEEP WORLD'
    assert (kit / '.dogido_memory/entries.jsonl').read_text() == 'KEEP MEMORY'
    assert all(not (kit / relative).exists() for relative in metadata)


# 見込み: updater一式とhashを適用前に固定すれば、転送欠損や部分展開で必須launcherだけが更新され起動不能にならない。
# 合格: Minecraft relayと実行helperの欠損・manifest後改変を、接続鍵・kitファイルへ書く前に拒否する。AirDrop自体は実機確認。
@pytest.mark.parametrize('damage', [
    'missing_relay', 'tampered_relay', 'missing_helper', 'tampered_helper',
])
def test_installer_rejects_incomplete_or_tampered_package_before_writes(tmp_path, damage):
    kit, package = tmp_path / 'kit', tmp_path / 'package'
    for path, text in {
        '00_setup.command': 'OLD SETUP', '.env.shared': 'SHARED', '.env': 'PRIVATE',
        'dogido_server/__init__.py': 'RUNTIME', 'minecraft/saves/world.dat': 'WORLD',
    }.items():
        target = kit / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    prepared_runtime(kit)
    complete_update_package(package)
    target = (package / 'family_runtime.py' if damage.endswith('helper')
              else package / 'updates/client_tools/minecraft_log_relay.py')
    if damage.startswith('missing'):
        target.unlink()
    else:
        target.write_text('CHANGED AFTER MANIFEST')
    before = {path.relative_to(kit): path.read_bytes() for path in kit.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='不足|hash'):
        installer.apply(package, kit)
    assert before == {path.relative_to(kit): path.read_bytes() for path in kit.rglob('*') if path.is_file()}
    assert not (kit / '.dogido_tools').exists()


@pytest.mark.parametrize('payload', [
    {'source': '../file', 'lines': ['x']}, {'source': 'server', 'lines': []},
    {'source': 'server', 'lines': ['x' * 8193]},
    {'source': 'server', 'lines': ['x'], 'path': '/tmp/arbitrary'},
])
def test_parent_rejects_unbounded_or_file_directed_logs(payload):
    with pytest.raises(ValueError):
        gateway.checked_logs(payload)


def test_logs_escape_terminal_controls_and_keep_japanese(tmp_path, capsys):
    log = gateway.DebugLog(tmp_path)
    log.emit('クライアント/server', '\x1b[31m句の確認\r\n')
    log.close()
    record = json.loads(log.path.read_text())
    assert record['message'] == '句の確認  '
    assert '\x1b' not in capsys.readouterr().out
    assert log.path.stat().st_mode & 0o777 == 0o600


def test_old_kit_rejected_before_keys_or_existing_files_change(tmp_path):
    kit, package = tmp_path / 'kit', tmp_path / 'package'
    (kit / 'dogido_server').mkdir(parents=True)
    (kit / 'minecraft/saves').mkdir(parents=True)
    for name, data in {'00_setup.command': 'OLD', '.env.shared': 'SHARED',
                       '.env': 'PRIVATE', 'dogido_server/app.py': 'OLD PYTHON'}.items():
        (kit / name).write_text(data)
    complete_update_package(package)
    before = {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='Rust本体セットが不足'):
        installer.apply(package, kit)
    assert before == {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
    assert not (kit / '.dogido_tools').exists()


def test_missing_runtime_rejects_before_replacing_existing_connection_keys(tmp_path):
    kit = tmp_path / 'kit'
    (kit / 'minecraft').mkdir(parents=True)
    (kit / '00_setup.command').write_text('OLD')
    (kit / '.env.shared').write_text('SHARED')
    prepared_runtime(kit)
    missing = kit / 'dogido-rust/scripts/tts_tokens_worker.py'
    missing.unlink()
    key = kit / '.dogido_tools/family-link/identity'
    key.parent.mkdir(parents=True)
    key.write_text('EXISTING TEST KEY')
    package = tmp_path / 'package'
    complete_update_package(package)
    before = {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='tts_tokens_worker.py'):
        installer.apply(package, kit)
    assert before == {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
