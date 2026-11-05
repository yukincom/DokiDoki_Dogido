"""模擬ゲームの状態と一時DBで、送り主への失敗理由・宛先・再試行の境界を確認する。"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lan'))
from family_messages import MessageBox, TIMEOUT


# 見込み: 選択した遊ぶMacから新しい状態を得たときだけ伝言を用意する。
# 合格: 未指定・端末不在・ゲームなし・交戦は別々の失敗、音声オフでも表示可能なら準備。同一IDは一行。
def test_selection_readiness_and_line_retry(tmp_path):
    box = MessageBox(tmp_path, lambda: ['a', 'b'])
    def send():
        return box.deliver_line('line-1', 'おとうさん', '18時ですよ')
    assert send() == 'no_recipient'
    box.select('a')
    assert send() == 'client_offline'
    for reason in ('no_active_game', 'game_busy'):
        box.poll('a', reason, '仮の名前')
        assert send() == reason
    box.poll('a', None, '仮の名前')
    mid = box.enqueue('ごはんですよ', 'おとうさん', 'line-1')
    assert box.enqueue('ごはんですよ', 'おとうさん', 'line-1') == mid
    box.select('b')
    box.prepare_next(lambda sender, player, text: f'{player}、{sender}から「{text}」やで')
    assert box.poll('b', None, '別の名前') is None
    assert box.poll('a', None, '仮の名前') == {'id': mid, 'text': '仮の名前、おとうさんから「ごはんですよ」やで'}
    box.report('a', mid, 'delivered')
    assert send() is None
    with box.transaction() as db:
        assert db.execute('SELECT sender,player,body,spoken,status FROM messages').fetchall() == [(None,None,None,None,'delivered')]


# 見込み: 提示後の切断は成否不明とし、再起動後や締切後に古い伝言をもう一度発話しない。
# 合格: 送信済みは不明、未送信のまま接続が切れたものは端末オフライン。一時DB・模擬時計で確認。
@pytest.mark.parametrize('restart', [True, False])
def test_expiry_or_restart_after_offer(tmp_path, monkeypatch, restart):
    now = [1000.]
    monkeypatch.setattr('family_messages.time.time', lambda: now[0])
    box = MessageBox(tmp_path, lambda: ['a'])
    box.select('a')
    box.poll('a', None, '仮名')
    mid = box.enqueue('一つ目', 'ママ', 'line-1')
    box.prepare_next(lambda *_: '一つ目やで')
    box.poll('a', None, '仮名')
    second = box.enqueue('二つ目', 'パパ', 'line-2')
    if restart:
        box = MessageBox(tmp_path, lambda: ['a'])
    else:
        now[0] += TIMEOUT
    assert box.deliver_line('line-1', 'ママ', '一つ目') == 'delivery_unknown'
    assert box.deliver_line('line-2', 'パパ', '二つ目') == ('delivery_unknown' if restart else 'client_offline')
    assert mid != second and box.poll('a', None, '仮名') is None
