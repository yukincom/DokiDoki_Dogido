"""任意のMac実測。専用の一時サービス名でBonjourだけを検査する。モデル・ドギドの本番窓口は使わない。"""
import os
from pathlib import Path
import secrets
import socket
import sys
import time

import pytest


# 見込み: 同じ実LANインターフェースを使えば、OSに登録したサービスを探索・解決できる。
# 合格: 独立した探索でテスト用のpin、IP、ポートが一致する。1台でのBonjour実測。別Macの権限・Wi-Fi経路は対象外。
@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("DOGIDO_TEST_BONJOUR") != "1",
                    reason="explicit same-Mac Bonjour check")
def test_real_bonjour_advertisement_resolves(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts/lan"))
    import connection
    import discovery
    interface = connection.lan_interface()
    assert interface is not None, "有効なLANインターフェースが必要です"
    index, address = interface
    monkeypatch.setattr(discovery, "SERVICE", f"_dgi-{secrets.token_hex(3)}._tcp".encode())
    pin = secrets.token_hex(32)
    with socket.socket() as local:
        local.bind(("127.0.0.1", 0))
        port = local.getsockname()[1]
        with discovery.Bonjour().advertise(index, port, pin) as poll:
            poll(2)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if any(peer.pin == pin and peer.address == (address, port)
                       for peer in discovery.Bonjour().discover()):
                    return
                poll(0.2)
    pytest.fail("このMacのBonjour登録を指定したLANで解決できませんでした")
