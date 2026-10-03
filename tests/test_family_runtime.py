"""Pure family launch/package boundary checks; no services, SSH, audio or models."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('family_runtime', ROOT / 'scripts/family-client/family_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def prepared(root):
    for relative in runtime.runtime_files():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('FIXTURE')
    (root / runtime.RUST_BINARY).chmod(0o755)
    normalized = root / 'reference/language_education_and_poetry/data/normalized'
    (normalized / 'index.json').write_text('{"datasets":[{"path":"fixture.jsonl","index_path":"fixture.index.jsonl"}]}')
    (normalized / 'fixture.jsonl').write_text('{}\n')
    (normalized / 'fixture.index.jsonl').write_text('{}\n')


def test_ready_requires_rust_dialogue_not_legacy_or_connection_only():
    healthy = {'ok': True, 'service': 'dogido-server', 'runtime': 'rust', 'dialogue_ready': True}
    assert runtime.is_rust_ready(healthy)
    for value in (None, {'ok': True, 'service': 'dogido-server'},
                  healthy | {'dialogue_ready': False}, healthy | {'runtime': 'python'}):
        assert not runtime.is_rust_ready(value)


def test_configured_port_reaches_both_launches_without_new_memory_root(tmp_path):
    settings = SimpleNamespace(bind_host='::1', bind_port=5111)
    assert runtime.server_url(settings) == 'http://[::1]:5111'
    server = runtime.launch_command(tmp_path)
    voice = runtime.launch_command(tmp_path, voice=True, check=True)
    assert server == voice[:-2]
    assert voice[-2:] == ['--voice', '--check']
    assert server[-2:] == ['--settings-dir', str(tmp_path)]
    assert all('rust-migration' not in argument for argument in voice)


def test_runtime_check_never_writes_or_accepts_legacy_controller(tmp_path):
    prepared(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    runtime.validate_runtime(tmp_path)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    legacy = tmp_path / 'dogido_server/app.py'
    legacy.write_text('OLD')
    with pytest.raises(ValueError, match='旧Python本体'):
        runtime.validate_runtime(tmp_path)


def test_runtime_check_refuses_external_symlink_before_launch(tmp_path):
    root = tmp_path / 'kit'
    prepared(root)
    helper = root / 'dogido-rust/scripts/haiku_tokens.py'
    other = tmp_path / 'external.py'
    other.write_text('EXTERNAL')
    helper.unlink()
    helper.symlink_to(other)
    with pytest.raises(ValueError, match='リンク'):
        runtime.validate_runtime(root)


def test_missing_normalized_dataset_is_detected_before_update(tmp_path):
    prepared(tmp_path)
    data = tmp_path / 'reference/language_education_and_poetry/data/normalized/fixture.jsonl'
    data.unlink()
    with pytest.raises(ValueError, match='fixture.jsonl'):
        runtime.validate_runtime(tmp_path)



def test_missing_shared_runtime_defaults_is_detected_before_update(tmp_path):
    prepared(tmp_path)
    (tmp_path / 'dogido_server/runtime_defaults.json').unlink()
    with pytest.raises(ValueError, match='runtime_defaults.json'):
        runtime.validate_runtime(tmp_path)
