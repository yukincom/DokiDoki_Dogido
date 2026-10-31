"""家庭用セットの同梱・起動境界を確認する。常駐Whisperも共有ライブラリを同梱先から読める見込み。
合格: worker・依存ファイル・相対rpathを保持。OSツールは模擬し、実バイナリ起動・音声・モデルは未検査。
"""
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
    normalized = root / 'reference/language_education_and_poetry'
    (normalized / 'index.json').write_text('{"datasets":[{"path":"fixture.json"}]}')
    (normalized / 'fixture.json').write_text('{}\n')


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


def test_missing_poetry_dataset_is_detected_before_update(tmp_path):
    prepared(tmp_path)
    data = tmp_path / 'reference/language_education_and_poetry/fixture.json'
    data.unlink()
    with pytest.raises(ValueError, match='fixture.json'):
        runtime.validate_runtime(tmp_path)



def test_missing_shared_runtime_defaults_is_detected_before_update(tmp_path):
    prepared(tmp_path)
    (tmp_path / 'dogido_server/runtime_defaults.json').unlink()
    with pytest.raises(ValueError, match='runtime_defaults.json'):
        runtime.validate_runtime(tmp_path)


def test_runtime_manifest_keeps_bert_and_removes_os_ai_helpers():
    required = set(runtime.runtime_files())
    assert "dogido-rust/scripts/request_classifier_worker.py" in required
    assert not {"dogido-rust/scripts/combat_input_helper.py",
                "dogido_server/platform_ai.py", "dogido_server/combat_input_contract.py"} & required


# 見込み: 常駐workerも既存の同梱目録へ入れば別Macへ渡し忘れない。
# 合格: release配置が必須ファイルに含まれる。目録のみで、実行権限・実ロードはこの試験の対象外。
def test_runtime_manifest_includes_resident_whisper_worker():
    assert "dogido-rust/target/release/dogido-whisper-worker" in runtime.runtime_files()


# 見込み: workerをCLIと同じ依存探索へ入れ、releaseからlibへの相対rpathで移設先を参照できる。
# 合格: worker固有の依存も再帰コピーし各rpathを設定、CLI設定は維持。otool・署名は模擬し実ロードは未検査。
def test_portable_whisper_includes_worker_dependencies_and_relative_rpath(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('build_family_client', ROOT / 'scripts/build_family_client.py')
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    source = tmp_path / 'whisper-source'
    root = tmp_path / 'family-kit'
    destination = root / '.dogido_tools/whisper'
    worker_source = tmp_path / 'source' / runtime.WHISPER_WORKER
    worker_target = root / runtime.WHISPER_WORKER
    originals = [source / 'build/bin/whisper-cli', source / 'build/bin/whisper-vad-speech-segments',
                 source / 'build/src/libwhisper.dylib', source / 'build/src/libworker-extra.dylib',
                 source / 'build/ggml/libggml.dylib', worker_source]
    for path in originals:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
        path.chmod(0o755)
    dependency_names = {
        'dogido-whisper-worker': ['libwhisper.dylib', 'libworker-extra.dylib'],
        'whisper-cli': ['libwhisper.dylib'],
        'whisper-vad-speech-segments': ['libwhisper.dylib'],
        'libworker-extra.dylib': ['libggml.dylib'],
    }
    monkeypatch.setattr(package, 'dependencies', lambda path: [
        '@rpath/' + name for name in dependency_names.get(path.name, [])])
    monkeypatch.setattr(package, 'output', lambda *args:
                        'cmd LC_RPATH\ncmdsize 48\npath /source/build/lib (offset 12)')
    commands = []
    monkeypatch.setattr(package.subprocess, 'run', lambda command, **kwargs: commands.append(command))

    package.portable_whisper(source, destination, worker_source, worker_target)

    expected = {destination / 'bin/whisper-cli', destination / 'bin/whisper-vad-speech-segments',
                destination / 'lib/libwhisper.dylib', destination / 'lib/libworker-extra.dylib',
                destination / 'lib/libggml.dylib', worker_target}
    assert set(path for path in root.rglob('*') if path.is_file()) == expected
    assert worker_target.read_bytes() == worker_source.read_bytes()
    assert worker_target.stat().st_mode & 0o777 == 0o755
    rewrites = {Path(command[-1]): command for command in commands if command[0] == 'install_name_tool'}
    assert set(rewrites) == expected
    for path, command in rewrites.items():
        assert command[1:3] == ['-delete_rpath', '/source/build/lib']
        rpath = command[command.index('-add_rpath') + 1]
        if path == worker_target:
            assert rpath == '@executable_path/../../../.dogido_tools/whisper/lib'
            assert (path.parent / rpath.removeprefix('@executable_path/')).resolve() == destination / 'lib'
        elif path.parent == destination / 'bin':
            assert rpath == '@executable_path/../lib'
        else:
            assert rpath == '@loader_path'
    assert {Path(command[-1]) for command in commands if command[0] == 'codesign'} == expected
