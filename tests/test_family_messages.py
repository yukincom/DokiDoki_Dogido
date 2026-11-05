"""模擬ゲームの状態と一時DBで、送り主への失敗理由・宛先・再試行の境界を確認する。"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lan'))
from family_messages import MessageBox, TIMEOUT


# 見込みは保存成功後の通知を使うと、ユノの本文と結果を画面に一回ずつ表示できること。
# 合格はLINE再送・結果再報告でも重複せず、失敗理由も出る。一時DBだけを使い実ユノへ送信しない。
def test_yuno_message_and_result_display_after_commit(tmp_path):
    events = []
    box = MessageBox(tmp_path, lambda: ['a'], on_event=lambda *args: events.append(args))
    assert not box.deliver_line('line-missing', '家族', 'テスト')['accepted']
    assert events[0][0] == 'ユノ'
    box.select('a')
    box.poll('a', None, '仮名')
    mid = box.enqueue('ごはんですよ', '家族', 'line-test')
    assert box.enqueue('ごはんですよ', '家族', 'line-test') == mid
    box.prepare_next(lambda *_: 'ごはんですよ')
    box.poll('a', None, '仮名')
    box.report('a', mid, 'delivered')
    box.report('a', mid, 'delivered')
    assert events[1:] == [('ユノ', '家族から　ごはんですよ'), ('ユノ', 'ゲームで伝言の表示または再生を確認しました。')]


# 見込み: 選択した遊ぶMacから新しい状態を得たときだけ伝言を用意する。
# 合格: 未指定・端末不在・ゲームなしは理由付き、交戦中でも選択端末へ送る。LINE再送は同じ受付と状態報告を使う。
def test_selection_readiness_and_line_retry(tmp_path):
    box = MessageBox(tmp_path, lambda: ['a', 'b'])
    def send():
        return box.deliver_line('line-1', 'おとうさん', '18時ですよ')
    assert send() == {'accepted': False, 'reason': 'no_recipient'}
    box.select('a')
    assert send() == {'accepted': False, 'reason': 'client_offline'}
    for reason in ('no_active_game', 'ambiguous_game', 'server_stopping'):
        box.poll('a', reason, '仮の名前')
        assert send() == {'accepted': False, 'reason': reason}
    box.poll('a', 'game_busy', '仮の名前')
    mid = box.enqueue('ごはんですよ', 'おとうさん', 'line-1')
    assert box.enqueue('ごはんですよ', 'おとうさん', 'line-1') == mid
    box.select('b')
    box.prepare_next(lambda sender, player, text: f'{player}、{sender}から「{text}」やで')
    assert box.poll('b', None, '別の名前') is None
    assert box.poll('a', None, '仮の名前') == {'id': mid, 'text': '仮の名前、おとうさんから「ごはんですよ」やで'}
    box.report('a', mid, 'delivered', ['combat'])
    assert send() == {'accepted': True, 'activity': ['combat']}
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
    assert box.deliver_line('line-1', 'ママ', '一つ目') == {'accepted': False, 'reason': 'delivery_unknown'}
    assert box.deliver_line('line-2', 'パパ', '二つ目') == {
        'accepted': False, 'reason': 'delivery_unknown' if restart else 'client_offline'}
    assert mid != second and box.poll('a', None, '仮名') is None


# 見込み: 表示確認を待つ一件や呼び名の未設定が、次の伝言の受付を妨げない。
# 合格: 模擬時計で生成待ちの二件目を原文で提示し、端末が確認した順にUIへ報告。LINE由来の一件は同期結果のみに含める。
def test_unset_name_and_pending_messages_do_not_block_each_other(tmp_path, monkeypatch):
    now = [1000.]
    monkeypatch.setattr('family_messages.time.time', lambda: now[0])
    reports = []
    box = MessageBox(tmp_path, lambda: ['a'], reports.append)
    box.select('a')
    box.poll('a', 'game_busy', '')
    first = box.enqueue('もうすぐだよ')
    second = box.enqueue('18時に来てね', '父', 'line-1')
    box.prepare_next(lambda sender, player, text: f'{sender}から伝言や。「{text}」')
    assert box.poll('a', None, '')['id'] == first
    now[0] += TIMEOUT / 2
    assert box.poll('a', None, '') == {'id': second, 'text': '父から伝言や。「18時に来てね」'}
    box.report('a', second, 'delivered', ['senryu'])
    assert reports == []
    assert box.deliver_line('line-1', '父', '18時に来てね') == {'accepted': True, 'activity': ['senryu']}
    box.report('a', first, 'delivered', ['combat', 'senryu'])
    box.report('a', first, 'delivered', ['combat', 'senryu'])
    assert reports == [{'accepted': True, 'activity': ['combat', 'senryu']}]
