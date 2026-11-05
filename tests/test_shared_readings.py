"""一時保存先と模擬応答で、辞書同期の追加・削除・失敗後の保持を確認する。"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lan'))
import shared_readings as readings

PIN = 'a' * 64
ROW = {'id': 'corr_1', 'surface': '草地', 'reading': 'くさち', 'wrong_reading': None,
       'forbidden_readings': [], 'source': 'biome:meadow'}


# 見込み: コピー全体を置換すれば削除も反映し、同内容は再保存せず、旧個人辞書は保全する。
# 合格: 追加→変更→空の往復と再生成で同一結果。別親では空にし、元JSONLは不変。実音声は対象外。
def test_replica_replaces_and_preserves_local_audit(tmp_path):
    original = tmp_path / 'long_term/catalog_corrections.jsonl'
    original.parent.mkdir()
    original.write_text('private prior record\n')
    replica = readings.Replica(tmp_path, PIN)
    assert json.loads(replica.path.read_bytes())['corrections'] == []
    value = {'schema_version': 1, 'corrections': [ROW]}
    replica.save(value)
    before = replica.path.stat().st_mtime_ns
    replica.save(value)
    assert replica.path.stat().st_mtime_ns == before
    replica.save({'schema_version': 1, 'corrections': [dict(ROW, reading='くさっぱら')]})
    assert json.loads(replica.path.read_bytes())['corrections'][0]['reading'] == 'くさっぱら'
    assert readings.Replica(tmp_path, PIN).path.read_bytes() == replica.path.read_bytes()
    replica.save({'schema_version': 1, 'corrections': []})
    assert json.loads(replica.path.read_bytes())['corrections'] == []
    replica.save(value)
    readings.Replica(tmp_path, 'b' * 64)
    assert json.loads(replica.path.read_bytes())['corrections'] == []
    assert original.read_text() == 'private prior record\n'


# 見込み: 非200・壊れた全体・巨大応答は最後の正常な辞書を変更しない。
# 合格: 全失敗で保存bytes不変、接続close一回。TLSとネットワークはここでは模擬。
@pytest.mark.parametrize('response', ['offline', 'json', 'duplicate', 'private', 'too_large', 'wrong_host'])
def test_sync_failures_keep_last_valid(tmp_path, monkeypatch, response):
    replica = readings.Replica(tmp_path, PIN)
    replica.save({'schema_version': 1, 'corrections': [ROW]})
    before = replica.path.read_bytes()
    body = {'schema_version': 1, 'corrections': []}
    if response == 'duplicate':
        body['corrections'] = [ROW, ROW]
    if response == 'private':
        body['corrections'] = [dict(ROW, session_id='private')]
    raw = json.dumps(body).encode()
    if response == 'json':
        raw = b'{'
    if response == 'too_large':
        raw = b' ' * (readings.LIMIT + 1)
    closed = []
    conn = SimpleNamespace(sock=SimpleNamespace(settimeout=lambda _: None), request=lambda *a, **k: None,
        getresponse=lambda: SimpleNamespace(status=503 if response == 'offline' else 200,
            getheader=lambda *args: 'application/json', read=lambda _: raw), close=lambda: closed.append(True))
    monkeypatch.setattr(readings.transport, '_tls', lambda *a: (conn, None))
    with pytest.raises((OSError, ValueError)):
        replica.sync(('test', 123), {'pin': 'b' * 64 if response == 'wrong_host' else PIN,
                                     'device': 'device', 'token': 'token'})
    assert replica.path.read_bytes() == before
    assert closed == ([] if response == 'wrong_host' else [True])


# 見込み: 起動時の壊れたmarkerは個人辞書への書込を再開させない。
# 合格: 起動を明示失敗しmarkerを保持。Rust側の編集禁止は別試験。
@pytest.mark.parametrize('body', [b'{', b'{}' + b' ' * readings.LIMIT])
def test_corrupt_marker_fails_without_overwrite(tmp_path, body):
    marker = tmp_path / 'long_term/shared_readings.json'
    marker.parent.mkdir()
    marker.write_bytes(body)
    with pytest.raises(ValueError):
        readings.Replica(tmp_path, PIN)
    assert marker.read_bytes() == body
