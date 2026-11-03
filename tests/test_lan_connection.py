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
    # Test discovery orchestration, not TLS (transport has independent tests).
    identity_error = type("IdentityError", (ValueError,), {})
    monkeypatch.setitem(sys.modules, "transport", SimpleNamespace(IdentityError=identity_error,
                        RevokedError=type("RevokedError", (identity_error,), {})))
    spec = importlib.util.spec_from_file_location("dogido_test_connection", folder / "connection.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
    monkeypatch.setattr(lan.transport, "pair", lambda *args: recorded.append(args), raising=False)
    if selected is None:
        with pytest.raises(RuntimeError, match="取り消"):
            lan.wait_for_pair(SimpleNamespace(discover=lambda: peers), tmp_path)
        assert not recorded
    else:
        lan.wait_for_pair(SimpleNamespace(discover=lambda: peers), tmp_path)
        assert recorded == [(tmp_path, peers[1].address, lan.confirm)]


# 見込み: 記録した相手を毎回照合すれば、スリープやIP変更後にも一度の登録で同じMacへ戻れる。
# 合格: 消失中の転送を拒否し、同名の別鍵や認証失敗も拒否、別IPの同じ鍵だけ復帰する。発見・認証は模擬。
def test_paired_address_survives_sleep_and_ip_change(lan):
    from discovery import Peer
    credentials = {"pin": "registered"}
    route = lan.PeerRoute()
    seen = []
    def authenticate(address, cred):
        seen.append((address, cred))
        if address[0] == "10.0.0.4":
            raise lan.transport.IdentityError("未登録")
    old = Peer("ドギド", ("10.0.0.2", 10), "registered")
    fake = Peer("ドギド", ("10.0.0.3", 10), "different")
    denied = Peer("ドギド", ("10.0.0.4", 10), "registered")
    new = Peer("ドギド", ("10.0.0.5", 11), "registered")
    route.probe([old], credentials, authenticate)
    assert route.resolve() == old.address
    for candidates in ([], [fake], [denied]):
        route.probe(candidates, credentials, authenticate)
        with pytest.raises(OSError):
            route.resolve()
    route.probe([fake, new], credentials, authenticate)
    assert route.resolve() == new.address
    assert seen == [(p.address, credentials) for p in (old, denied, new)]


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
                           devices=lambda: ["id1", "id2"], revoke=lambda device: events.append(("revoke", device)))
    monkeypatch.setattr(lan, "choose", lambda *_: None)
    lan.manage(host, "")
    lan.manage(host, "pair")
    monkeypatch.setattr(lan, "choose", lambda *_: 1)
    lan.manage(host, "revoke")
    assert events == [("open", 120), ("revoke", "id2")]
