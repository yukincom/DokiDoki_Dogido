from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unicodedata
import runpy

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


def prepared_runtime(kit):
    runtime = runpy.run_path(str(SOURCE.parent / 'family-client/family_runtime.py'))
    for relative in runtime['runtime_files']():
        path = kit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('FIXTURE RUNTIME')
    (kit / runtime['RUST_BINARY']).chmod(0o755)
    normalized = kit / 'reference/language_education_and_poetry/data/normalized'
    (normalized / 'index.json').write_text('{"datasets":[{"path":"fixture.jsonl","index_path":"fixture.index.jsonl"}]}')
    (normalized / 'fixture.jsonl').write_text('{}\n')
    (normalized / 'fixture.index.jsonl').write_text('{}\n')


def valid():
    return {'model': 'default_model', 'messages': [{'role': 'user', 'content': '大丈夫？'}],
            'temperature': 0.7, 'max_tokens': 512, 'stream': False,
            'chat_template_kwargs': {'enable_thinking': False}}


def test_preserves_dogido_request_and_fixes_model_loader():
    payload = gateway.checked_payload(valid())
    assert payload['messages'] == valid()['messages']
    assert payload['max_tokens'] == 512
    assert payload['draft_model'] is None and payload['adapters'] is None
    assert payload['model'] == 'default_model'


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
    (package / 'updates').mkdir(parents=True)
    (package / 'updates/01_connect.command').write_text('NEW CONNECTOR')
    (package / 'link').mkdir()
    for name in ('identity', 'known_hosts', 'connection.json'):
        (package / 'link' / name).write_text('TEST ' + name)
    installer.apply(package, kit)
    installer.apply(package, kit)
    for relative, data in before.items():
        if str(relative) not in ('connection.txt', '01_connect.command'):
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
    (package / 'updates').mkdir(parents=True)
    (package / 'updates/01_connect.command').write_text('NEW')
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
    (package / 'updates').mkdir(parents=True)
    name = unicodedata.normalize(normalization, 'はじめに.md')
    (package / 'updates' / name).write_text('UPDATED INSTRUCTIONS')
    (package / 'updates/01_connect.command').write_text('NEW CONNECTOR')
    metadata = ['.DS_Store', '._はじめに.md', '__MACOSX/._はじめに.md', 'client_tools/.DS_Store']
    for relative in metadata:
        source = package / 'updates' / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'FINDER METADATA')
    (package / 'link').mkdir()
    for name in ('identity', 'known_hosts', 'connection.json'):
        (package / 'link' / name).write_text('FIXTURE ' + name)
    installer.apply(package, kit)
    installer.apply(package, kit)
    assert (kit / 'はじめに.md').read_text() == 'UPDATED INSTRUCTIONS'
    assert (kit / '01_connect.command').read_text() == 'NEW CONNECTOR'
    assert (kit / '.env').read_text() == 'USER SETTINGS'
    assert (kit / '.env.shared').read_text() == 'SHARED SETTINGS'
    assert (kit / 'minecraft/saves/world.dat').read_bytes() == b'KEEP WORLD'
    assert (kit / '.dogido_memory/entries.jsonl').read_text() == 'KEEP MEMORY'
    assert all(not (kit / relative).exists() for relative in metadata)


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
    log.emit('息子/server', '\x1b[31m句の確認\r\n')
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
    before = {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='tts_tokens_worker.py'):
        installer.apply(tmp_path / 'package', kit)
    assert before == {p.relative_to(kit): p.read_bytes() for p in kit.rglob('*') if p.is_file()}
