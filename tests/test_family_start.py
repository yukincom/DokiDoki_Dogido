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


# 見込み: 新しいkitの一括起動はペアリング窓口を開き、VOICEVOXを必要な場合だけ起動してから音声を開始する。
# 合格: 旧接続情報なしで新clientを起動し、未起動時だけVOICEVOXを開き、準備順・停止対象が一致する。プロセスと通信は模擬。
@pytest.mark.parametrize('voicevox_running', [True, False])
def test_one_click_waits_for_readiness_and_stops_only_owned_children(tmp_path, monkeypatch, voicevox_running):
    (tmp_path / 'lan_tools').mkdir()
    (tmp_path / 'lan_tools/connection.py').touch()
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
            self.name = Path(argv[-1]).name
            if self.name == 'client':
                assert Path(argv[-2]) == tmp_path / 'lan_tools/connection.py'
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
    def open_voicevox(argv, **kwargs):
        assert argv == ['/usr/bin/open', '-a', 'VOICEVOX']
        events.append('open-voicevox')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher.subprocess, 'run', open_voicevox)
    monkeypatch.setitem(sys.modules, 'log_relay', SimpleNamespace(Relay=Relay,
        stop_child=lambda child: events.append('stop:' + child.name)))
    monkeypatch.setitem(sys.modules, 'dogido_server.config', SimpleNamespace(get_settings=lambda: SimpleNamespace(
        bind_host='127.0.0.1', bind_port=5111, voicevox_url='http://127.0.0.1:50021')))
    def response(url):
        if url.endswith('/models'):
            events.append('model-ready'); return {'data': []}
        if url.endswith('/version'):
            if not voicevox_running and 'open-voicevox' not in events:
                raise OSError('not started')
            events.append('voicevox-ready'); return '1.0'
        assert url == 'http://127.0.0.1:5111/healthz'
        events.append('server-ready'); return {'ok': True, 'service': 'dogido-server', 'runtime': 'rust', 'dialogue_ready': True}
    monkeypatch.setattr(launcher, 'json_response', response)
    with pytest.raises(RuntimeError, match='音声入力が終了'):
        launcher.run(tmp_path)
    assert launcher.os.environ['DOGIDO_CHAT_IMAGE_ENABLED'] == 'true'
    assert launcher.os.environ['DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED'] == 'false'
    assert events.index('model-ready') < events.index('spawn:server')
    assert events.count('open-voicevox') == int(not voicevox_running)
    assert events.index('voicevox-ready') < events.index('spawn:server')
    assert events.index('server-ready') < events.index('spawn:voice')
    assert [e for e in events if e.startswith('stop:')] == [
        'stop:voice', 'stop:server', 'stop:minecraft_log_relay.py', 'stop:client']


def test_single_click_runs_the_shared_launcher_source():
    command = (ROOT / 'scripts/family-client/ドギドを起動.command').read_text()
    assert 'client_tools/start_client.py "$PWD"' in command
    assert 'DOGIDO_LAUNCH_PYTHON' not in command
    assert 'install_on_demand' not in SOURCE.read_text()
