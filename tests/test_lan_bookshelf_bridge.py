"""一時の登録情報・実loopback TLSとUnix socketで本棚中継を検査する。実Mac間通信・実川柳の変更は行わない。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lan'))
import bookshelf_bridge as bridge
import transport
from test_lan_transport import World, serving


@pytest.fixture
def world(monkeypatch):
    # macOSのUnix socketパス上限を越えない一時領域。鍵・要求・模擬データは終了時に整理する。
    monkeypatch.setattr(transport, 'computer_name', lambda: '遊ぶMac')
    with tempfile.TemporaryDirectory(prefix='dgb-', dir='/tmp') as directory, ExitStack() as stack:
        yield World(Path(directory), stack)


def wait_request(broker, device):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        item = broker.poll(device)
        if item:
            return item
        time.sleep(.01)
    raise AssertionError('request was not offered')


# 見込み: 同じ登録情報と固定の本棚窓口を使えば、親に句を保存せず子のJSON・画像を取得できる。
# 合格: 実TLSとUnix socketを通じて元のbytesと権限ヘッダーが一致し、親状態に本文ファイルが増えない。子本棚は模擬HTTP。
@pytest.mark.parametrize('path,typ,body', [('/bootstrap.js', 'application/javascript', b'window.DATA={};'),
                                          ('/api/photos/photo-1/image', 'image/png', b'\x89PNG\r\n\x1a\nimage')])
def test_paired_bridge_roundtrip_without_payload_files(world, path, typ, body):
    credentials = world.pair()
    broker = world.host.bookshelf
    calls = []
    class Local(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            calls.append((self.path, self.headers.get('Origin'), self.headers.get('X-Dogido-Library'),
                          self.headers.get('X-Dogido-Bookshelf-Admin')))
            self.send_response(200)
            self.send_header('Content-Type', typ)
            self.end_headers()
            self.wfile.write(body)
    root = world.root / 'host'
    before = set(root.iterdir())
    with serving(ThreadingHTTPServer(('127.0.0.1', 0), Local)) as address, bridge.serve_host(root, broker), ThreadPoolExecutor() as executor:
        url = f'http://127.0.0.1:{address[1]}'
        bridge._exchange(world.address, credentials, bridge.NEXT, {})
        rows = bridge.list_remote(root)
        assert len(rows) == 1 and rows[0]['id'] == credentials['device'] and rows[0]['online']
        task = executor.submit(bridge.request_remote, root, None, 'GET', path, b'')
        request = None
        for _ in range(100):
            request = bridge._exchange(world.address, credentials, bridge.NEXT, {})['request']
            if request:
                break
            time.sleep(.01)
        assert request is not None
        reply = bridge.forward_local(url, 'local-admin', 'local-csrf', request)
        bridge._exchange(world.address, credentials, bridge.RESULT, reply)
        assert task.result(timeout=3) == (200, typ, body)
        assert calls == [(path, url, 'local-csrf', 'local-admin')]
        assert bridge._exchange(world.address, credentials, bridge.NEXT, {}) == {'request': None}
        assert not broker.pending
    assert set(root.iterdir()) == before


# 見込み: 結果の配送先を認証済み端末へ固定すれば、別端末から応答を注入できない。
# 合格: 他端末の同一ID応答では完了せず、正しい端末の応答と再送で一度だけ完了する。仲介は実コード、子保存は模擬。
def test_result_is_bound_to_authenticated_device_and_report_retry():
    device, other = 'a' * 16, 'b' * 16
    broker = bridge.Broker(lambda: [{'id': device, 'name': ''}, {'id': other, 'name': ''}])
    broker.poll(device)
    with ThreadPoolExecutor() as executor:
        task = executor.submit(broker.request, device, 'PUT', '/api/poems/current', b'{"operation":"same"}')
        item = wait_request(broker, device)
        reply = {'id': item['id'], 'status': 200, 'content_type': 'application/json', 'body': bridge._encoded(b'{"saved":true}')}
        broker.result(other, reply)
        assert not task.done()
        assert broker.poll(device) is None  # 保存操作を再配送しない。
        broker.result(device, reply)
        assert task.result(timeout=2)[0] == 200
        broker.result(device, reply)
        assert not broker.pending


# 見込み: 未配送要求を期限で終端すれば、復帰後に古い削除が突然実行されない。
# 合格: タイムアウト後のpollは空、保存呼出しなし、応答は失敗の504。時計を短縮した仲介単体試験。
def test_expired_request_never_offered_after_reconnect(monkeypatch):
    monkeypatch.setattr(bridge, 'REQUEST_SECONDS', .05)
    device = 'a' * 16
    broker = bridge.Broker(lambda: [{'id': device, 'name': '子'}])
    broker.poll(device)
    status, _, body = broker.request(device, 'PUT', '/api/trash', b'{"action":"delete"}')
    assert status == 504 and '応答を確認できません' in body.decode()
    assert broker.poll(device) is None and not broker.pending


# 見込み: 手元にない接続先や複数候補を勝手に選ばず、既存データの所在を変えずに案内できる。
# 合格: 未接続は503、二台からの自動選択は409、要求は一件も生成しない。模擬登録情報のみ。
def test_offline_and_ambiguous_clients_do_not_create_work():
    rows = [{'id': 'a' * 16, 'name': '子'}]
    broker = bridge.Broker(lambda: rows)
    assert broker.list() == [{'id': 'a' * 16, 'name': '子', 'online': False}]
    assert broker.request(None, 'GET', '/api/library', b'')[0] == 503
    rows.append({'id': 'b' * 16, 'name': '別PC'})
    assert broker.request(None, 'GET', '/api/library', b'')[0] == 409
    assert not broker.pending


# 見込み: 登録解除と共有側終了を待機中の呼出しへ通知すれば、古い宛先への保存が残らない。
# 合格: 解除時403、停止時503で待機終了し、その後のpollへ要求が現れない。仲介単体試験。
@pytest.mark.parametrize('finish,expected', [('revoke', 403), ('close', 503)])
def test_revocation_and_shutdown_cancel_waiting_requests(finish, expected):
    device = 'a' * 16
    broker = bridge.Broker(lambda: [{'id': device, 'name': '子'}])
    broker.poll(device)
    with ThreadPoolExecutor() as executor:
        task = executor.submit(broker.request, device, 'GET', '/api/library', b'')
        end = time.monotonic() + 2
        while not broker.pending and time.monotonic() < end:
            time.sleep(.01)
        if finish == 'revoke':
            broker.revoke(device)
        else:
            broker.close()
        assert task.result(timeout=2)[0] == expected
        assert broker.poll(device) is None


# 見込み: 期限切れ・任意宛先・辞書変更を送信前に弾けば、外部通信や別データへの書込みを起こさない。
# 合格: 各入力がValueError、HTTPConnectionは一度も作られない。固定操作の境界だけを検査。
@pytest.mark.parametrize('url,path,remaining', [('http://example.com', '/api/library', 1),
                                              ('http://127.0.0.1:1', '/api/readings', 1),
                                              ('http://127.0.0.1:1', '/api/trash', 0)])
def test_child_rejects_nonlocal_or_expired_work_before_http(monkeypatch, url, path, remaining):
    monkeypatch.setattr(bridge.http.client, 'HTTPConnection', lambda *_args, **_kwargs: pytest.fail('unexpected HTTP'))
    with pytest.raises(ValueError):
        bridge.forward_local(url, 'admin', 'csrf', {'id': 'a' * 32, 'method': 'PUT', 'path': path,
                                                  'body': '', 'remaining': remaining})
