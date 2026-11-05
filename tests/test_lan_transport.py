"""隔離した鍵・登録情報と実loopback TLSを使う。Bonjour探索、2台間Wi-Fi、実モデルは別確認。"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lan'))
import transport


@contextmanager
def serving(server):
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    worker.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


class World:
    def __init__(self, root, stack):
        self.root, self.stack = root, stack
        self.upstream_calls, self.host_codes, self.client_codes = [], [], []
        self.finish_stream = threading.Event()
        self.should_confirm = True
        calls, release = self.upstream_calls, self.finish_stream

        class Mock(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                calls.append((self.path, body, self.headers.get('Authorization')))
                if self.path == transport.CHAT and json.loads(body).get('stream') is True:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    self.wfile.write(b'data: first\n\n')
                    self.wfile.flush()
                    release.wait(3)
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    self.send_response(202)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"accepted":1}')

            def do_GET(self):
                calls.append((self.path, None, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

        self.upstream = stack.enter_context(serving(ThreadingHTTPServer(('127.0.0.1', 0), Mock)))
        self.host, self.address = self.new_host(root / 'host')

    def new_host(self, root):
        def confirm(code):
            self.host_codes.append(code)
            return self.should_confirm
        server = transport.make_host(root, ('127.0.0.1', 0), self.upstream[1], confirm)
        return server, self.stack.enter_context(serving(server))

    def pair(self, approve=True):
        self.host.open_pairing()
        def confirm(code):
            self.client_codes.append(code)
            return approve
        return transport.pair(self.root / 'client', self.address, confirm)


@pytest.fixture
def world(tmp_path):
    with ExitStack() as stack:
        yield World(tmp_path, stack)


# 見込み: 初回に両側の番号を照合し、次回は端末の鍵で再接続できる。
# 合格: 同じ6桁を観測し、新しいportの同一証明書へ再承認なしで認証成功。実TLS、模擬共有窓口のみ。
def test_first_pair_and_new_address_without_new_approval(world):
    credentials = world.pair()
    assert world.host_codes == world.client_codes and len(world.host_codes[0]) == 6
    assert credentials == transport.load_pair(world.root / 'client')
    assert world.host.devices() == [credentials['device']]
    assert credentials['token'] not in (world.root / 'host/devices.json').read_text()
    assert (world.root / 'client/client.json').stat().st_mode & 0o777 == 0o600
    assert (world.root / 'host/host.pem').stat().st_mode & 0o777 == 0o600
    later, moved = world.new_host(world.root / 'host')
    assert later.pin == credentials['pin']
    transport.probe(moved, credentials)
    assert len(world.host_codes) == 1
    assert not world.upstream_calls


# 見込み: 登録受付と双方の承認を独立して要求すれば、拒否された端末には資格を保存しない。
# 合格: 閉じた窓口・期限切れ・片側拒否の全てで両端末に資格が残らない。時間境界は制御し実TLSで確認。
@pytest.mark.parametrize('mode', ['closed', 'expired', 'host_declines', 'client_declines'])
def test_no_enrollment_without_both_approvals(world, mode):
    with pytest.raises(transport.IdentityError):
        if mode == 'closed':
            transport.pair(world.root / 'client', world.address, lambda _: True)
        elif mode == 'expired':
            world.host.open_pairing(seconds=-1)
            transport.pair(world.root / 'client', world.address, lambda _: True)
        else:
            world.should_confirm = mode != 'host_declines'
            world.pair(approve=mode != 'client_declines')
    assert world.host.devices() == []
    assert transport.load_pair(world.root / 'client') is None
    assert not world.upstream_calls


# 見込み: 発見した同名の別端末でもTLSの相手を照合してからHTTPを送れば、tokenや会話を渡さない。
# 合格: 別鍵を持つ模擬サーバーでHTTP処理はゼロ、解除した登録は401扱いで上流呼出しゼロ。実TLSで確認。
def test_reject_replaced_peer_before_http_and_revoked_credential(world):
    credentials = world.pair()
    impostor, address = world.new_host(world.root / 'other')
    requests = []
    original = impostor.RequestHandlerClass
    class Spy(original):
        def handle_route(self):
            requests.append(self.path)
            super().handle_route()
    impostor.RequestHandlerClass = Spy
    with pytest.raises(transport.IdentityError, match='証明書'):
        transport.probe(address, credentials)
    assert requests == []
    assert world.host.revoke(credentials['device']) and world.host.devices() == []
    with pytest.raises(transport.IdentityError, match='解除'):
        transport.probe(world.address, credentials)
    assert not world.upstream_calls


# 見込み: 遊ぶ側loopbackから発信し続ければ、逆向き着信なしで文の先頭と保存応答を受け取れる。
# 合格: 模擬上流終了前に一文目が届き、ログ応答202、Bearerは上流に渡らない。実Wi-Fi・実モデルは別確認。
def test_relay_streams_early_and_preserves_status(world):
    credentials = world.pair()
    relay = transport.make_client_relay(lambda: world.address, credentials, port=0)
    client = world.stack.enter_context(serving(relay))
    conn = http.client.HTTPConnection(*client, timeout=2)
    try:
        conn.request('POST', transport.CHAT, b'{"stream":true}', {'Content-Type': 'application/json'})
        response = conn.getresponse()
        assert response.status == 200 and response.readline() == b'data: first\n'
        world.finish_stream.set()
        assert b'[DONE]' in response.read()
        conn.request('POST', transport.LOGS, b'{}', {'Content-Type': 'application/json'})
        response = conn.getresponse()
        assert response.status == 202 and json.load(response) == {'accepted': 1}
    finally:
        world.finish_stream.set()
        conn.close()
    assert world.upstream_calls == [
        (transport.CHAT, b'{"stream":true}', None), (transport.LOGS, b'{}', None)]


# 見込み: 認証・用途・サイズを先に検査すれば、上流へ不正な操作が届かない。
# 合格: 認証なし401、未知経路404、巨大本文413の全てで上流0回。小さな隔離済みHTTPSで確認。
def test_host_rejects_untrusted_requests_before_forwarding(world):
    credentials = world.pair()
    for path, headers, expected in [
        (transport.MODELS, {}, 401),
        ('/outside', {'Authorization': transport._auth(credentials)}, 404),
        (transport.CHAT, {'Authorization': transport._auth(credentials), 'Content-Length': '99999999',
                          'Content-Type': 'application/json'}, 413),
    ]:
        conn, _ = transport._tls(world.address, world.host.pin)
        try:
            conn.request('POST' if path == transport.CHAT else 'GET', path, headers=headers)
            assert conn.getresponse().status == expected
        finally:
            conn.close()
    assert not world.upstream_calls


# 見込み: nonceを先に確約させ、期限内の一回だけ消費すれば割込み・再利用で別端末を登録できない。
# 合格: 並列開始・別nonce・同一finishの再送が拒否され、登録は1台。時刻・上流モデルは不要の状態検査。
def test_commitment_is_single_use(tmp_path):
    token = '1' * 64
    registry = transport.Pairings(tmp_path, 'f' * 64, lambda _: True)
    registry.open(120)
    registry.begin(hashlib.sha256(token.encode()).hexdigest())
    with pytest.raises(transport.Refusal):
        registry.begin('0' * 64)
    with pytest.raises(transport.Refusal):
        registry.prove('2' * 64)
    registry.prove(token)
    import time
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if registry.finish(token)[0] == 200:
            break
        time.sleep(0.01)
    with pytest.raises(transport.Refusal):
        registry.finish(token)
    assert len(registry.devices()) == 1


# 見込み: ブラウザから別サイト名でlocalhostへ到達しても端末資格情報を使えない。
# 合格: 偽HostとOrigin付き要求はどちらも403、上流への要求は0件。実loopbackと一時ペアリングで確認。
@pytest.mark.parametrize('headers', [{'Host': 'unrelated.invalid'}, {'Origin': 'https://unrelated.invalid'}])
def test_loopback_blocks_browser_origins(world, headers):
    pair = world.pair()
    relay = transport.make_client_relay(lambda: world.address, pair, port=0)
    with serving(relay) as address:
        client = http.client.HTTPConnection(*address)
        try:
            client.request('GET', transport.MODELS, headers=headers)
            assert client.getresponse().status == 403
        finally:
            client.close()
    assert not world.upstream_calls


# 見込み: 認証した宛先でだけ配送し、表示ACKの転送に失敗しても同じ伝言を二重に再生しない。
# 合格: 別端末の偽ACKは無効、選択先の模擬本体だけが原文を受け、再接続後も表示済み1回。実TLS・一時DB。
def test_family_message_bound_to_peer_and_receipt_survives_lost_ack(world, monkeypatch):
    import message_client
    first = world.pair()
    world.host.open_pairing()
    other = transport.pair(world.root / 'other-client', world.address, lambda _: True)
    box = world.host.messages
    box.select(first['device'])
    state = world.root / 'client'
    received = []

    def local(message=None):
        if message is None:
            return {'ready': True}
        received.append(message)
        return {'accepted': True, 'status': 'delivered', 'activity': ['combat']}

    def poll():
        message_client.poll_once(state, world.address, first, '遊ぶ人', 'run1', local)
    poll()
    mid = box.enqueue('18時にごはんですよー！', sender='父', source_id='line-1')
    box.prepare_next(lambda sender, player, text: f'{player}、{sender}から「{text}」やで。')
    request = transport.message_request
    assert request(world.address, other, transport.NEXT_MESSAGE, {'reason': None, 'player': '他'})['message'] is None
    request(world.address, other, transport.MESSAGE_RESULT, {'id': mid, 'status': 'delivered'})
    def lost_ack(address, credentials, path, data):
        if path == transport.MESSAGE_RESULT:
            raise OSError
        return request(address, credentials, path, data)
    monkeypatch.setattr(transport, 'message_request', lost_ack)
    with pytest.raises(OSError):
        poll()
    monkeypatch.setattr(transport, 'message_request', request)
    poll()
    assert received == [{'id': mid, 'text': '遊ぶ人、父から「18時にごはんですよー！」やで。'}]
    assert box.deliver_line('line-1', '父', '同じ通知') == {'accepted': True, 'activity': ['combat']}
    assert not (state / 'family-receipt.json').exists()
    assert not world.upstream_calls


# 見込み: 再起動時は複数伝言の未確定IDすべてを認識し、先に受け付けたゲームへ二度話さない。
# 合格: 状態照会のtimeout後も同じ起動回に二件を提示し、片方のPOST応答が途切れても本体再起動後は両方とも不明と報告。LANとRustは模擬。
def test_multiple_uncertain_receipts_survive_game_restart(tmp_path, monkeypatch):
    import message_client
    messages = [{'id': c * 32, 'text': '模擬伝言'} for c in 'ab']
    offers = iter(messages * 2)
    results, posted = [], []
    def host(address, credentials, path, payload):
        if path == transport.NEXT_MESSAGE:
            assert payload['reason'] is None
            return {'message': next(offers)}
        results.append(payload)
        return {}
    def game(message=None):
        if message is None:
            raise TimeoutError
        posted.append(message)
        if message == messages[0]:
            raise TimeoutError
        return {'accepted': True, 'status': 'queued'}
    monkeypatch.setattr(transport, 'message_request', host)
    def poll(generation):
        return message_client.poll_once(tmp_path, (), {'device': 'a', 'pin': 'p'}, '', generation, game)
    with pytest.raises(TimeoutError):
        poll('before')
    poll('before')
    poll('after')
    poll('after')
    assert posted == messages
    assert results == [{'id': message['id'], 'status': 'delivery_unknown'} for message in messages]


# 見込み: 同じ起動回の本体だけを照会し、受信しただけでゲームに表示済みと報告しない。
# 合格: 模擬RustでGETとPOSTの経路・鍵・本文とqueued応答の区別を確認。実ゲームの表示は別試験。
def test_client_uses_local_family_endpoint():
    import message_client
    requests = []
    class Game(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, body, result):
            requests.append((self.command, self.path, self.headers['X-Dogido-Family-Key'], self.headers['Authorization'], body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def do_GET(self):
            self.reply(None, {'ready': False, 'reason': 'no_active_game'})

        def do_POST(self):
            self.reply(json.loads(self.rfile.read(int(self.headers['Content-Length']))), {'accepted': True, 'status': 'queued'})

    message = {'id': 'c' * 32, 'text': 'お母さんからやで。「おいで」って。'}
    with serving(ThreadingHTTPServer(('127.0.0.1', 0), Game)) as (host, port):
        url = f'http://{host}:{port}'
        assert message_client.local_request(url, 'key', 'token')['reason'] == 'no_active_game'
        assert message_client.local_request(url, 'key', 'token', message)['status'] == 'queued'
    assert requests == [(m, '/api/v1/family-message', 'key', 'Bearer token', data)
                        for m, data in [('GET', None), ('POST', message)]]


# 見込み: 有線とWi-Fiの窓口で端末登録と伝言を共有し、受付後に経路が切り替わっても完了する。
# 合格: 一方で登録受付を開き他方で1回ペアリング。本文を取った窓口を閉じても残る窓口で受領確定。実TLS＋一時DB。
def test_family_round_trip_across_listeners(tmp_path):
    from discovery import Peer
    codes = []
    def confirm(code):
        codes.append(code)
        return True
    listen = transport.host_listener(tmp_path / 'host', 1, confirm)
    wifi, wired = listen(('127.0.0.1', 0)), listen(('127.0.0.1', 0))
    with serving(wired) as ethernet:
        with serving(wifi) as wireless:
            wifi.open_pairing()
            candidates = [Peer('別の鍵', wireless, '0' * 64), Peer('共有', ethernet, wired.pin)]
            assert transport.pairing_address(candidates) == ethernet
            credentials = transport.pair(tmp_path / 'client', ethernet, confirm)
            assert len(codes) == 2 and codes[0] == codes[1]
            box = wired.messages
            box.select(credentials['device'])
            def poll(address):
                return transport.message_request(address, credentials, transport.NEXT_MESSAGE,
                                                 {'reason': None, 'player': '遊ぶ人'})['message']
            assert poll(wireless) is None
            mid = box.enqueue('ごはんだよ', source_id='line-fixture')
            box.prepare_next(lambda sender, player, body: body)
            assert poll(wireless) == {'id': mid, 'text': 'ごはんだよ'}
        result = {'id': mid, 'status': 'delivered', 'activity': ['combat']}
        transport.message_request(ethernet, credentials, transport.MESSAGE_RESULT, result)
        assert box.deliver_line('line-fixture', 'お母さん', 'ごはんだよ') == {'accepted': True, 'activity': ['combat']}
        assert poll(ethernet) is None
