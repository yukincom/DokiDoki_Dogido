from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/family-link/start_client.py'
spec = importlib.util.spec_from_file_location('family_start', SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_one_click_waits_for_readiness_and_stops_only_owned_children(tmp_path, monkeypatch):
    state = tmp_path / '.dogido_tools/family-link'
    state.mkdir(parents=True)
    (state / 'connection.json').write_text('{}')
    events, children = [], []
    monkeypatch.setattr(launcher.os, 'environ', dict(launcher.os.environ))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(launcher.os, 'chdir', lambda path: None)
    monkeypatch.setattr(launcher, 'port_open', lambda port, host: False)
    runtime = importlib.util.spec_from_file_location('family_runtime', ROOT / 'scripts/family-client/family_runtime.py')
    runtime_module = importlib.util.module_from_spec(runtime)
    runtime.loader.exec_module(runtime_module)
    monkeypatch.setattr(runtime_module, 'validate_runtime', lambda root: None)
    monkeypatch.setitem(sys.modules, 'family_runtime', runtime_module)
    monkeypatch.setattr(launcher.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(launcher.time, 'sleep', lambda _: None)
    class Child:
        def __init__(self, argv):
            self.name = 'ssh' if argv[0] == 'fake-ssh' else argv[-1]
            self.returncode = None
            children.append(self)
            events.append('spawn:' + self.name)
        def poll(self):
            if self.name == 'voice':
                self.returncode = 1
            return self.returncode
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda argv, **kw: Child(argv))
    class Relay:
        def __init__(self, source): pass
        def put(self, line): pass
        def close(self): events.append('relay-close')
    monkeypatch.setitem(sys.modules, 'family_link', SimpleNamespace(command=lambda root: ['fake-ssh']))
    monkeypatch.setitem(sys.modules, 'log_relay', SimpleNamespace(Relay=Relay,
        stop_child=lambda child: events.append('stop:' + child.name)))
    monkeypatch.setitem(sys.modules, 'dogido_server.config', SimpleNamespace(get_settings=lambda: SimpleNamespace(
        bind_host='127.0.0.1', bind_port=5111, voicevox_url='http://127.0.0.1:50021')))
    def response(url):
        if url.endswith('/models'):
            events.append('qwen-ready'); return {'data': []}
        if url.endswith('/version'):
            events.append('voicevox-ready'); return '1.0'
        assert url == 'http://127.0.0.1:5111/healthz'
        events.append('server-ready'); return {'ok': True, 'service': 'dogido-server', 'runtime': 'rust', 'dialogue_ready': True}
    monkeypatch.setattr(launcher, 'json_response', response)
    with pytest.raises(RuntimeError, match='音声入力が終了'):
        launcher.run(tmp_path)
    assert events.index('qwen-ready') < events.index('spawn:server')
    assert events.index('server-ready') < events.index('spawn:voice')
    assert [e for e in events if e.startswith('stop:')] == ['stop:voice', 'stop:server', 'stop:ssh']


def test_single_click_runs_the_shared_launcher_source():
    command = (ROOT / 'scripts/family-client/ドギドを起動.command').read_text()
    assert 'client_tools/start_client.py "$PWD"' in command
    assert 'DOGIDO_LAUNCH_PYTHON' not in command
    assert 'install_on_demand' not in SOURCE.read_text()
