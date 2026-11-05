"""Bonjour・選択・登録後の再接続を模擬端末で確認する。実機のWi-Fi・UI・モデルは別途確認する。"""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def lan(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / "scripts/lan"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location("dogido_test_connection", folder / "connection.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 発見の試験だけ通信を模擬する。伝言クライアントに模擬transportを残さない。
    module.transport = SimpleNamespace(IdentityError=module.transport.IdentityError,
                                       RevokedError=module.transport.RevokedError)
    return module


# 見込み: TXTの長さと公開鍵印を解釈すれば、同名サービスを誤登録せず候補を選べる。
# 合格: 正しい印だけ読み、長さ不正と非16進数は空。実Bonjourの探索と画面表示は別途。
def test_decodes_only_complete_pin(lan):
    import discovery
    value = b"pin=" + b"a" * 64
    assert discovery.txt_pin(b"\x03v=1" + bytes([len(value)]) + value) == "a" * 64
    assert not discovery.txt_pin(bytes([len(value) + 1]) + value)
    assert not discovery.txt_pin(bytes([len(value)]) + value[:-1] + b"z")


# 見込み: 候補選択と両側の番号確認を分離して、初回だけ選んだ共有相手を登録できる。
# 合格: 選択したaddressと確認callbackだけpairへ渡り、取消ではpairを呼ばない。ダイアログとTLSは模擬。
@pytest.mark.parametrize("selected", [1, None])
def test_first_pair_requires_explicit_selection(lan, monkeypatch, tmp_path, selected):
    from discovery import Peer
    peers = [Peer("同じ名前", ("10.0.0.2", 2048), "one"),
             Peer("同じ名前", ("10.0.0.3", 2049), "two")]
    recorded = []
    monkeypatch.setattr(lan, "choose", lambda *_: selected)
    monkeypatch.setattr(lan.transport, "pairing_address", lambda group: group[0].address, raising=False)
    monkeypatch.setattr(lan.transport, "pair", lambda *args: recorded.append(args), raising=False)
    if selected is None:
        with pytest.raises(RuntimeError, match="取り消"):
            lan.wait_for_pair(SimpleNamespace(discover=lambda: peers), tmp_path)
        assert not recorded
    else:
        lan.wait_for_pair(SimpleNamespace(discover=lambda: peers), tmp_path)
        assert recorded == [(tmp_path, peers[1].address, lan.confirm)]


# 見込み: 広告を見失っても旧経路を再認証し、経路が切れたら登録済みの別経路に移る。
# 合格: 一時欠落中は到達、実切断中は配送不可。偽の鍵を避け、同じ鍵の別IPで復帰。認証は模擬。
def test_paired_address_survives_sleep_and_ip_change(lan):
    from discovery import Peer
    credentials = {"pin": "registered"}
    route = lan.PeerRoute()
    old = Peer("共有", ("10.0.0.2", 10), "registered")
    fake = Peer("共有", ("10.0.0.3", 10), "different")
    denied = Peer("共有", ("10.0.0.4", 10), "registered")
    new = Peer("共有", ("10.0.0.5", 11), "registered")
    online = {old.address}
    seen = []
    def authenticate(address, cred):
        seen.append(address)
        if address == denied.address:
            raise lan.transport.IdentityError("別のTLS鍵")
        if address not in online:
            raise OSError("切断")
    route.probe([old], credentials, authenticate)
    route.probe([], credentials, authenticate)
    assert route.resolve() == old.address
    online.clear()
    route.probe([fake, denied], credentials, authenticate)
    with pytest.raises(OSError):
        route.resolve()
    online.add(new.address)
    route.probe([fake, old, new], credentials, authenticate)
    assert route.resolve() == new.address
    assert seen == [old.address, old.address, old.address, denied.address, old.address, new.address]


# 見込み: 登録済みクライアントは毎回の確認画面なしにlocalhostで応答を待つ。
# 合格: 既存資格情報でrelayを8080に起動し、発見した相手を認証して配送先とする。socket・モデルなし。
def test_registered_start_skips_pairing(lan, monkeypatch, tmp_path):
    from discovery import Peer
    peer = Peer("host", ("10.0.0.4", 6500), "abc")
    cred = {"pin": "abc"}
    calls = []
    monkeypatch.setattr(lan, "Bonjour", lambda: SimpleNamespace(discover=lambda: [peer]))
    monkeypatch.setattr(lan.transport, "load_pair", lambda _: cred, raising=False)
    monkeypatch.setattr(lan.transport, "probe", lambda address, credentials: calls.append((address, credentials)), raising=False)
    monkeypatch.setattr(lan.transport, "make_client_relay", lambda resolve, credentials, port: (resolve, port), raising=False)
    endpoint = []
    @contextmanager
    def serving(server):
        try:
            yield server
        finally:
            endpoint.append((server[0](), server[1]))
    monkeypatch.setattr(lan, "serving", serving)
    def stop(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(lan.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        lan.run_client(tmp_path)
    assert calls == [(peer.address, cred)]
    assert endpoint == [(peer.address, 8080)]


# 見込み: hostの通常受付と端末追加を分ければ、明示操作だけで登録の時間が開く。
# 合格: キャンセルでは登録しない、追加では120秒だけ受付、選んだ端末だけ解除。GUIとサーバーは模擬。
def test_host_controls_only_explicitly_enroll_and_revoke(lan, monkeypatch):
    events = []
    host = SimpleNamespace(open_pairing=lambda seconds: events.append(("open", seconds)),
                           device_records=lambda: [{'id': 'id1', 'name': 'PC一台目'}, {'id': 'id2', 'name': 'PC二台目'}],
                           messages=SimpleNamespace(selected=lambda: None),
                           revoke=lambda device: events.append(("revoke", device)))
    monkeypatch.setattr(lan, "choose", lambda *_: None)
    lan.manage(host, "")
    lan.manage(host, "pair")
    monkeypatch.setattr(lan, "choose", lambda *_: 1)
    lan.manage(host, "revoke")
    assert events == [("open", 120), ("revoke", "id2")]


# 見込み: ボタンから送る一行JSONは大文字や引用符を保ち、本文中のコマンドを管理操作として扱わない。
# 合格: 偽の共有画面から選択した宛先と原文を1件ずつ受付。GUIの音声認識・実端末への配送は別確認。
def test_family_controls_preserve_typed_or_dictated_text(lan, monkeypatch):
    import io
    text = '18時にSwitchを片付けて！ "REVOKE"'
    commands = lan.queue.SimpleQueue()
    monkeypatch.setattr(sys, 'stdin', io.StringIO('recipient\nmessage ' + lan.json.dumps(text) + '\n'))
    lan.read_controls(commands)
    seen = []
    host = SimpleNamespace(device_records=lambda: [{'id': 'other', 'name': '別のPC'}, {'id': 'play', 'name': '遊ぶPC'}],
                           messages=SimpleNamespace(select=seen.append, selected=lambda: None,
                               enqueue=lambda message, **kw: seen.append(message)))
    monkeypatch.setattr(lan, 'choose', lambda *_: 1)
    while not commands.empty():
        lan.manage(host, *commands.get())
    assert seen == ['play', text]


# 見込み: 名前は表示だけに使えば、同名PCでも選んだ登録を変更し、削除は指定した登録だけに限定できる。
# 合格: 一時DBと模擬登録で宛先と削除対象がIDどおり。空・混在・古い選択は無変更。実GUI/TLSは別試験。
def test_device_settings_use_names_but_target_ids(lan, tmp_path, capsys):
    devices = {'first': 'Mac mini', 'second': 'Mac mini', 'legacy': ''}
    messages = lan.MessageBox(tmp_path, lambda: devices)
    def revoke(device):
        devices.pop(device)
        messages.revoked(device)
    host = SimpleNamespace(device_records=lambda: [{'id': key, 'name': value} for key, value in devices.items()],
                           messages=messages, revoke=revoke)
    lan.manage(host, 'recipient "second"')
    snapshot = lan.device_settings(host)
    assert snapshot == {'devices': [{'id': 'first', 'name': 'Mac mini'}, {'id': 'second', 'name': 'Mac mini'},
                                   {'id': 'legacy', 'name': '登録PC 3'}], 'recipient': 'second'}
    assert 'second' not in capsys.readouterr().out
    for command in ('revoke []', 'revoke ["first", "unknown"]', 'revoke {}', 'recipient "unknown"'):
        lan.manage(host, command)
    assert messages.selected() == 'second' and len(devices) == 3
    lan.manage(host, 'revoke ["first"]')
    assert messages.selected() == 'second' and set(devices) == {'second', 'legacy'}
    lan.manage(host, 'revoke ["second", "legacy"]')
    assert not devices and messages.selected() is None


# 見込み: 共有画面の管理者名を既存のsenderへ渡せば、表示・生成・原文代替に同じ送り主を使える。
# 合格: 模擬stdinと一時DBで指定名が表示/生成へ届き、次の名前変更が受付済み本文へ遡及しない。実音声は対象外。
def test_ui_administrator_name_reaches_existing_message_delivery(lan, tmp_path):
    events, rendered = [], []
    messages = lan.MessageBox(tmp_path, lambda: ['play'], on_event=lambda *row: events.append(row))
    messages.select('play')
    messages.poll('play', None, 'プレイヤー')
    host = SimpleNamespace(messages=messages)
    for sender in ('おかあさん', '管理者テスト'):
        lan.manage(host, 'message ' + lan.json.dumps({'text': 'ごはんですよ', 'sender': sender}))
    def render(sender, player, body):
        rendered.append((sender, player, body))
        return f'{sender}から伝言や。「{body}」'
    assert messages.prepare_next(render) and messages.prepare_next(render)
    assert rendered == [('おかあさん', 'プレイヤー', 'ごはんですよ'), ('管理者テスト', 'プレイヤー', 'ごはんですよ')]
    assert events == [('伝言', 'おかあさんから　ごはんですよ'), ('伝言', '管理者テストから　ごはんですよ')]
    assert messages.poll('play', None, 'プレイヤー')['text'] == 'おかあさんから伝言や。「ごはんですよ」'


# 見込み: UI由来でも送り主を無検査で受け付けない。合格: 不正名/外形はDBへ登録されず、旧一行形式は汎用名で使える。
def test_ui_administrator_payload_validation_and_legacy_input(lan, tmp_path):
    messages = lan.MessageBox(tmp_path, lambda: ['play'])
    messages.select('play')
    messages.poll('play', None, 'プレイヤー')
    host = SimpleNamespace(messages=messages)
    for value in ({'text': '本文', 'sender': ''}, {'text': '本文', 'sender': '改\n行'},
                  {'text': '本文', 'sender': 'あ' * 81}, {'text': '本文'}, ['本文']):
        lan.manage(host, 'message ' + lan.json.dumps(value))
    with messages.transaction() as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone() == (0,)
    lan.manage(host, 'message ' + lan.json.dumps('旧形式の本文'))
    with messages.transaction() as db:
        assert db.execute('SELECT sender,body FROM messages').fetchall() == [('管理者', '旧形式の本文')]

# 見込み: Wi-Fi・有線は同時に候補になり、DHCPのない有線も対象。VPNと公開アドレスは広報しない。
# 合格: 模擬macOSから物理LANの2件だけを列挙する。接続と端末認証は別試験。
def test_lists_all_physical_lan_ports(lan, monkeypatch):
    ports = [(3, "en0"), (4, "en1"), (5, "utun0"), (6, "en3"), (7, "en4")]
    listing = """en0: flags=8863<UP,RUNNING>
    inet 192.168.2.4 netmask 0xffffff00
en1: flags=8863<UP,RUNNING>
    inet 169.254.1.4 netmask 0xffff0000
utun0: flags=8051<UP,RUNNING>
    inet 10.0.0.2 netmask 0xff000000
en3: flags=8863<UP,RUNNING>
en4: flags=8863<UP,RUNNING>
    inet 8.8.8.8 netmask 0xffffff00"""
    monkeypatch.setattr(lan.socket, "if_nameindex", lambda: ports)
    monkeypatch.setattr(lan.subprocess, "run", lambda args, **kw: SimpleNamespace(stdout=listing))
    assert lan.lan_interfaces() == {(3, "192.168.2.4"), (4, "169.254.1.4")}


# 見込み: 有線を抜き差ししてもWi-Fiの待受け・広告は継続する。逆方向も同じ管理を通す。
# 合格: 2経路を同時に公開し、有線を外した後も元のWi-Fi窓口でポーリング。広告・OS時刻を模擬。
def test_unplug_one_port_keeps_the_other_serving(lan, monkeypatch):
    wifi, wired = (1, "192.168.0.2"), (2, "192.168.0.3")
    iterations = iter([{wifi, wired}, {wifi}])
    events = []
    @contextmanager
    def advertise(discovery, listen, entry, port):
        events.append(("open", entry))
        try:
            yield SimpleNamespace(server_port=9000), lambda _: events.append(("poll", entry))
        finally:
            events.append(("close", entry))
    monkeypatch.setattr(lan, "advertised_port", advertise)
    monkeypatch.setattr(lan, "lan_interfaces", lambda: next(iterations))
    monkeypatch.setattr(lan.time, "monotonic", iter(range(0, 100, 5)).__next__)
    monkeypatch.setattr(lan.time, "sleep", lambda _: None)
    settings = SimpleNamespace(device_records=lambda: [], messages=SimpleNamespace(selected=lambda: None))
    with pytest.raises(StopIteration):
        lan.serve_lan(None, settings, lan.queue.SimpleQueue())
    assert events == [("open", wifi), ("open", wired), ("poll", wifi), ("poll", wired),
                      ("close", wired), ("poll", wifi), ("close", wifi)]
