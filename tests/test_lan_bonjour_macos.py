"""任意のMac実測。専用の一時サービス名と証明書でBonjourとTLSを検査する。"""
from contextlib import ExitStack
import os
from pathlib import Path
import secrets
import sys
import time

import pytest


# 見込み: このMacで実際に有効な全LANポートを同時に公開し、それぞれの窓口を発見できる。
# 合格: 別の探索で全広告を解決し各アドレスへ同じ鍵でTLS接続。登録は閉じ、別Macの経路・権限は別確認。
@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("DOGIDO_TEST_BONJOUR") != "1",
                    reason="explicit same-Mac Bonjour check")
def test_real_bonjour_advertisement_resolves(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts/lan"))
    import connection
    import discovery
    interfaces = connection.lan_interfaces()
    assert interfaces, "有効なLANインターフェースが必要です"
    import transport
    monkeypatch.setattr(discovery, "SERVICE", f"_dgi-{secrets.token_hex(3)}._tcp".encode())
    bonjour = discovery.Bonjour()
    listen = transport.host_listener(tmp_path / "host", 1, lambda _: False)
    expected, reached = set(), set()
    polls = []
    port = 0
    with ExitStack() as stack:
        for index, address in sorted(interfaces):
            server, poll = stack.enter_context(connection.advertised_port(bonjour, listen, (index, address), port))
            port = server.server_port
            expected.add((address, port))
            polls.append(poll)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and reached != expected:
            for poll in polls:
                poll(0.1)
            for peer in discovery.Bonjour().discover():
                if peer.pin == server.pin and peer.address in expected:
                    reached.add(transport.pairing_address([peer]))
            reached.discard(None)
        assert reached == expected
