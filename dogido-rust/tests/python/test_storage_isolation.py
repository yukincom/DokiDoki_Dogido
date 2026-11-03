"""見込み: 共通起動の隔離で試験記録を個人本棚へ流さない。実HTTPは模擬モデル・音声、実Minecraftなし。"""
import json
from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from check_dialogue import ROOT, dependencies, register, running, submit, row, wait_for, request
from storage_fixture import isolated_storage
from body_senses_fixture import attach_body
from request_classifier_fixture import mock_request_classifier


# 見込み: 既定値も継承設定も試験用になる。合格: 全保存先が一時領域で本番環境を含まない。設定境界のみ。
def test_defaults_and_inherited_storage_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('DOGIDO_ACTIVE_PRIMARY_ROOT', '/Volumes/personal')
    monkeypatch.setenv('DOGIDO_ACTIVE_PRIMARY_ID', 'personal')
    monkeypatch.setenv('DOGIDO_EPISODE_MEMORY_DIR', '/Volumes/personal/eval')
    settings, env = isolated_storage(tmp_path, None, ())
    for key in ('memory_dir', 'screenshot_dir', 'capture_dir'):
        assert Path(settings[key]).is_relative_to(tmp_path)
    assert settings['internal_capture_enabled'] is False
    assert env['HOME'] == str(tmp_path / 'home')
    assert env['DOGIDO_EPISODE_MEMORY_DIR'] == str(tmp_path / 'episodes')
    assert set(key for key in env if key.startswith('DOGIDO_')) == {'DOGIDO_EPISODE_MEMORY_DIR'}


# 見込み: 外部パスとリンク先を起動前に拒否する。合格: 本番相当の番兵が不変。設定境界のみ。
@pytest.mark.parametrize('key', ['memory_dir', 'screenshot_dir', 'capture_dir'])
def test_storage_override_and_symlink_cannot_escape(tmp_path, key):
    sentinel = tmp_path / 'sentinel'
    sentinel.write_text('keep')
    outside = Path.home() / 'dogido-personal-sentinel'
    link = tmp_path / 'link'
    link.symlink_to(outside)
    for destination in (outside, link):
        with pytest.raises(ValueError, match='temporary directory'):
            isolated_storage(tmp_path, {key: str(destination)}, ())
    assert sentinel.read_text() == 'keep'
    assert not outside.exists()


# 見込み: 追加引数から保存設定を迂回できない。合格: 重複引数・イコール形式とも起動前に拒否。設定境界のみ。
@pytest.mark.parametrize('argument', ['--haiku-settings', '--haiku-settings={}', '--audio-dir=/tmp'])
def test_storage_cli_override_is_rejected(tmp_path, argument):
    with pytest.raises(ValueError, match='through the fixture'):
        isolated_storage(tmp_path, {}, (argument,))


# 見込み: 独立した一時撮影/採取fixtureも継続利用できる。合格: 明示した一時パスと採取有効を保持。設定境界のみ。
def test_explicit_temporary_capture_is_preserved(tmp_path):
    directory = tmp_path / 'server'
    capture = tmp_path / 'captures'
    settings, _ = isolated_storage(directory, {'capture_dir': str(capture),
                                               'internal_capture_enabled': True}, ())
    assert settings['capture_dir'] == str(capture)
    assert settings['internal_capture_enabled'] is True


# 見込み: 実HTTPの模擬接続でも通常終了・試験例外時とも個人領域に保存しない。
# 合格: 旅/評価ログは試験領域に現れ、親の本番相当フォルダは番兵だけ。モデル・音声は固定、Minecraftなし。
@pytest.mark.parametrize('interrupted', [False, True])
def test_http_trial_and_exception_only_write_temporary_storage(tmp_path, monkeypatch, interrupted):
    binary = Path(os.environ.get('DOGIDO_RUST_BINARY', ROOT / 'target/debug/dogido-rust')).resolve()
    assert binary.is_file(), 'build the debug server before this runtime check'
    personal = tmp_path / 'personal'
    personal.mkdir()
    sentinel = personal / 'sentinel'
    sentinel.write_bytes(b'keep')
    monkeypatch.chdir(personal)
    monkeypatch.setenv('HOME', str(personal))
    monkeypatch.setenv('DOGIDO_ACTIVE_PRIMARY_ROOT', str(personal))
    monkeypatch.setenv('DOGIDO_ACTIVE_PRIMARY_ID', 'personal')
    monkeypatch.setenv('DOGIDO_EPISODE_MEMORY_DIR', str(personal))
    directory = tmp_path / 'trial'
    directory.mkdir()
    with dependencies() as (dep, _, _), mock_request_classifier({"こんにちは": "not_target"}) as (classifier_args, _):
        def trial():
            with running(binary, directory, dep, extra_args=classifier_args) as (base, _, _):
                sid = register(base, preview=False, body=True)
                event = attach_body({
                    'schema_version': '2026-05-24', 'adapter': 'fixture', 'sequence': 1,
                    'observed_at': datetime.now(timezone.utc).isoformat(),
                    'event': {'name': 'status_snapshot', 'source_kind': 'system',
                              'priority_hint': 'background', 'certainty': 'high'},
                    'player': {'name': '試験', 'dimension': 'minecraft:overworld'},
                    'world': {'biome': 'minecraft:plains', 'time_phase': 'morning',
                              'weather': 'clear', 'sky_visible': True, 'ceiling_height': 20}})
                request(base, '/api/v1/game-events', event, sid=sid)
                turn = submit(base, sid, source='text')
                wait_for(lambda: row(base, turn, {'completed'}))
                if interrupted:
                    raise RuntimeError('trial failed after delivery')
        if interrupted:
            with pytest.raises(RuntimeError, match='trial failed'):
                trial()
        else:
            trial()
    assert list(personal.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b'keep'
    journeys = list((directory / 'memory/long_term/journeys').glob('*.json'))
    assert len(journeys) == 1
    assert json.loads(journeys[0].read_text())['player'] == '試験'
    assert (directory / 'episodes/eval/episodes.jsonl').is_file()
