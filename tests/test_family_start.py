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


# 見込み: 音声入力だけを任意のworkerにすれば、マイクの停止・異常終了でも本体・接続・対話中継を維持できる。
# 合格: voiceの起動失敗／終了後も監視を3巡継続し、案内は一度。必須workerの終了時と明示停止時だけ残りを停止。通信・プロセスは模擬。
@pytest.mark.parametrize('voicevox_running', [True, False])
@pytest.mark.parametrize('installed_app', [True, False])
@pytest.mark.parametrize('failure', ['voice-0', 'voice-1', 'voice-start', 'server', 'client', 'minecraft_log_relay.py'])
def test_one_click_waits_for_readiness_and_stops_only_owned_children(tmp_path, monkeypatch, capsys, voicevox_running, installed_app, failure):
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
    cycles = []
    def idle(_):
        cycles.append(True)
        assert failure.startswith('voice')
        assert not any(event in events for event in ('stop:server', 'stop:client', 'relay-close'))
        if len(cycles) == 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(launcher.time, 'sleep', idle)
    class Child:
        def __init__(self, argv):
            self.name = Path(argv[-1]).name
            if self.name == 'voice' and failure == 'voice-start':
                raise OSError('mock microphone worker start failed')
            if self.name == 'client':
                assert Path(argv[-2]) == tmp_path / 'lan_tools/connection.py'
            self.returncode = None
            children.append(self)
            events.append('spawn:' + self.name)
        def poll(self):
            events.append('poll:' + self.name)
            if self.name == 'voice' and failure in ('voice-0', 'voice-1'):
                self.returncode = int(failure[-1])
            elif self.name == failure and any(child.name == 'voice' for child in children):
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
    helpers = SimpleNamespace(Relay=Relay,
        relay_dialogue=lambda stop, url, auth, emit: stop.wait(),
        stop_child=lambda child: events.append('stop:' + child.name))
    monkeypatch.setitem(sys.modules, 'log_relay', helpers)
    if installed_app:
        # 旧kitが残る実導入配置でも、app内の起動スクリプトと同じ版のhelperが選ばれることを検査する。
        app = tmp_path / 'app/client_tools'
        app.mkdir(parents=True)
        (tmp_path / 'client_tools').mkdir()
        (tmp_path / 'client_tools/log_relay.py').write_text("raise AssertionError('old kit helper selected')")
        (app / 'log_relay.py').write_text('from test_family_helper_hooks import Relay, stop_child, relay_dialogue')
        monkeypatch.setattr(launcher, '__file__', str(app / 'start_client.py'))
        monkeypatch.setitem(sys.modules, 'test_family_helper_hooks', helpers)
        del sys.modules['log_relay']
    monkeypatch.setitem(sys.modules, 'dogido_server.config', SimpleNamespace(get_settings=lambda: SimpleNamespace(
        bind_host='127.0.0.1', bind_port=5111, auth_token=None, default_call_name="テスト", memory_dir=tmp_path / 'memory', voicevox_url='http://127.0.0.1:50021')))
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
    if failure.startswith('voice'):
        launcher.run(tmp_path)
        assert len(cycles) == 3
        assert events.count('poll:server') >= 3 and events.count('poll:client') >= 3
        assert capsys.readouterr().out.count('文字入力・読み上げ・伝言は続けます。') == 1
    else:
        with pytest.raises(RuntimeError, match='残りも停止します'):
            launcher.run(tmp_path)
    assert launcher.os.environ['DOGIDO_CHAT_IMAGE_ENABLED'] == 'true'
    assert launcher.os.environ['DOGIDO_HAIKU_INTERNAL_CAPTURE_ENABLED'] == 'false'
    assert events.index('model-ready') < events.index('spawn:server')
    assert events.count('open-voicevox') == int(not voicevox_running)
    assert events.index('voicevox-ready') < events.index('spawn:server')
    if failure != 'voice-start':
        assert events.index('server-ready') < events.index('spawn:voice')
    assert [e for e in events if e.startswith('stop:')] == (
        ([] if failure == 'voice-start' else ['stop:voice']) +
        ['stop:server', 'stop:minecraft_log_relay.py', 'stop:client'])
    assert events.count('relay-close') == 2


def test_single_click_runs_the_shared_launcher_source():
    command = (ROOT / 'scripts/family-client/ドギドを起動.command').read_text()
    assert 'client_tools/start_client.py "$PWD"' in command
    assert 'DOGIDO_LAUNCH_PYTHON' not in command
    assert 'install_on_demand' not in SOURCE.read_text()
