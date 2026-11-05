"""模擬ゲームの状態と一時DBで、送り主への失敗理由・宛先・再試行の境界を確認する。"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lan'))
from family_messages import MessageBox, TIMEOUT


# 見込みは配達確定時に本文を読むと、原文に加えてドギドの加工後の伝言を表示できること。
# 合格はLINE再送・結果再報告でも原文・加工後本文・結果が一度ずつ出て、本文はDBから消える。一時DB・模擬生成のみ。
def test_yuno_message_and_result_display_after_commit(tmp_path):
    events = []
    devices = []
    box = MessageBox(tmp_path, lambda: devices, on_event=lambda *args: events.append(args))
    assert not box.deliver_line('line-missing', '家族', 'テスト')['accepted']
    assert events[0][0] == 'ユノ'
    devices.append('a')
    box.poll('a', None, '仮名')
    mid = box.enqueue('ごはんですよ', '家族', 'line-test')
    assert box.enqueue('ごはんですよ', '家族', 'line-test') == mid
    box.prepare_next(lambda *_: '家族から伝言やで。ごはんできたんやって。')
    box.poll('a', None, '仮名')
    assert events[1:] == [('ユノ', '家族から　ごはんですよ')]
    box.report('a', mid, 'delivered')
    box.report('a', mid, 'delivered')
    assert events[1:] == [('ユノ', '家族から　ごはんですよ'),
                          ('ドギド', '家族から伝言やで。ごはんできたんやって。'),
                          ('ユノ', 'ゲームで伝言の表示または再生を確認しました。')]
    with box.transaction() as db:
        assert db.execute('SELECT body,spoken FROM messages WHERE id=?', (mid,)).fetchone() == (None, None)


# 見込み: 画面からの伝言も、生成待ちを原文で代替した場合を含めて実際の配送本文を示せる。
# 合格: 未送達ではドギド欄に出ず、送達では端末へ渡した全文だけが一度出る。一時DB・模擬時計で確認する。
@pytest.mark.parametrize('status', ['delivered', 'delivery_failed', 'delivery_unknown'])
@pytest.mark.parametrize('fallback', [False, True])
def test_ui_message_displays_only_confirmed_delivery(tmp_path, monkeypatch, status, fallback):
    now = [1000.]
    monkeypatch.setattr('family_messages.time.time', lambda: now[0])
    events = []
    box = MessageBox(tmp_path, lambda: ['a'], on_event=lambda *args: events.append(args))
    box.poll('a', None, '仮名')
    mid = box.enqueue('18時に来てね', sender='おかあさん')
    if fallback:
        now[0] += TIMEOUT / 2
        expected = 'おかあさんから伝言や。「18時に来てね」'
    else:
        expected = 'おかあさんが18時に来てなって言うとるで。'
        box.prepare_next(lambda *_: expected)
    assert box.poll('a', None, '仮名') == {'id': mid, 'text': expected}
    assert events == [('伝言', 'おかあさんから　18時に来てね')]
    box.report('a', mid, status)
    box.report('a', mid, status)
    spoken = [text for speaker, text in events if speaker == 'ドギド']
    assert spoken == ([expected] if status == 'delivered' else [])


# 見込み: 登録PC全員を受付時に固定すれば、選択不要で個別の呼び名へ送り、再送でも対象を増やさない。
# 合格: 一時DB・模擬生成で二台の異なるIDと呼び名、全完了前は通知なし、全完了後は成功一回。追加PCへ再送しない。
def test_broadcast_all_registered_devices_and_line_retry(tmp_path):
    devices = ['a', 'b']
    box = MessageBox(tmp_path, lambda: devices)
    box.poll('a', 'game_busy', '一人目')
    box.poll('b', None, '二人目')
    batch = box.enqueue('ごはんですよ', '管理者', 'line-1')
    assert box.enqueue('二重に送らない', '管理者', 'line-1') == batch
    rendered = []
    def render(sender, player, text):
        rendered.append((sender, player, text))
        return f'{player}、{text}'
    assert box.prepare_next(render) and box.prepare_next(render)
    first = box.poll('a', None, '一人目')
    second = box.poll('b', None, '二人目')
    assert first == {'id': batch, 'text': '一人目、ごはんですよ'}
    assert second['id'] != first['id'] and second['text'] == '二人目、ごはんですよ'
    assert rendered == [('管理者', '一人目', 'ごはんですよ'), ('管理者', '二人目', 'ごはんですよ')]
    box.report('b', first['id'], 'delivered')
    with box.transaction() as db:
        assert box._batch_result(db, batch) is None
    box.report('a', first['id'], 'delivered', ['combat'])
    box.report('b', second['id'], 'delivered', ['senryu'])
    devices.append('c')
    assert box.deliver_line('line-1', '管理者', 'ごはんですよ') == {'accepted': True, 'activity': ['combat', 'senryu']}
    assert box.poll('c', None, '追加PC') is None
    with box.transaction() as db:
        assert db.execute('SELECT sender,player,body,spoken,status FROM messages').fetchall() == [(None,None,None,None,'delivered')] * 2


# 見込み: 一台の停止をその配送だけに留めれば、ほかの登録PCへ届き、全体成功とは区別できる。
# 合格: 一時DBで未接続の失敗と接続中の成功を保持し、UI結果はpartial_deliveryを一度だけ通知。遅い再接続へ持越さない。
def test_offline_device_does_not_block_others_or_report_full_success(tmp_path):
    reports, events = [], []
    names = {'a': '未接続Mac', 'b': '遊ぶMac'}
    box = MessageBox(tmp_path, lambda: names, reports.append, lambda *row: events.append(row), names.get)
    box.poll('b', None, '遊ぶ人')
    box.enqueue('ごはんですよ')
    assert reports == []
    box.prepare_next(lambda *args: 'ごはんですよ')
    message = box.poll('b', None, '遊ぶ人')
    box.report('b', message['id'], 'delivered')
    box.report('b', message['id'], 'delivered')
    assert reports == [{'accepted': False, 'reason': 'partial_delivery'}]
    assert any(text.startswith('未接続Mac：') for _, text in events)
    assert any(text.startswith('遊ぶMac：') for _, text in events)
    assert box.poll('a', None, 'あとで起動') is None


# 見込み: 同じ送信の各配送を独立に確定すれば、順序が前後しても全員成功の通知は一回だけになる。
# 合格: 一時DBと模擬ACKで先頭行の完了後も通知せず、最後のACKで活動タグの和集合を一回通知。
def test_ui_broadcast_notifies_once_after_all_acknowledgements(tmp_path):
    reports = []
    box = MessageBox(tmp_path, lambda: ['a', 'b'], reports.append)
    for device in ('a', 'b'):
        box.poll(device, None, '')
    box.enqueue('集合してね')
    assert box.prepare_next(lambda *_: '集合してね')
    assert box.prepare_next(lambda *_: '集合してね')
    first, second = [box.poll(device, None, '') for device in ('a', 'b')]
    box.report('a', first['id'], 'delivered', ['combat'])
    assert reports == []
    box.report('b', second['id'], 'delivered', ['combat', 'senryu'])
    assert reports == [{'accepted': True, 'activity': ['combat', 'senryu']}]


# 見込み: 旧宛先設定を廃止しても、既に配達したLINEのIDは保持できる。
# 合格: 旧形式DBを移行し、選択テーブルは撤去、旧LINEは再配送ゼロ、新通知は旧選択外も含む二台。実サービスなし。
def test_legacy_recipient_is_removed_without_replaying_old_line(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / 'family-messages.sqlite3') as db:
        db.executescript("""CREATE TABLE recipient(device TEXT NOT NULL);
            INSERT INTO recipient VALUES('a');
            CREATE TABLE messages(id TEXT PRIMARY KEY, device TEXT,sender TEXT,player TEXT,body TEXT,spoken TEXT,
                                  created REAL NOT NULL,source TEXT UNIQUE,status TEXT NOT NULL);
            INSERT INTO messages VALUES('old','a',NULL,NULL,NULL,NULL,1000,'line-old','delivered');""")
    box = MessageBox(tmp_path, lambda: ['a', 'b'])
    assert box.deliver_line('line-old', '管理者', '古い本文') == {'accepted': True, 'activity': []}
    for device in ('a', 'b'):
        assert box.poll(device, None, '') is None
    batch = box.enqueue('新しい本文')
    with box.transaction() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='recipient'").fetchall() == []
        assert db.execute('SELECT device FROM messages WHERE batch_id=? ORDER BY device', (batch,)).fetchall() == [('a',), ('b',)]


# 見込み: 移行全体を同じtransactionで扱えば、列追加中の失敗から元DBへ戻せる。
# 合格: 一時DBで二列目の追加を模擬失敗させても列が半分残らず、再起動で移行と旧LINE照合が成功。実停止なし。
def test_interrupted_schema_migration_rolls_back_as_one_transaction(tmp_path, monkeypatch):
    import sqlite3
    from family_messages import _Transaction
    path = tmp_path / 'family-messages.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE messages(id TEXT PRIMARY KEY, device TEXT,sender TEXT,player TEXT,body TEXT,spoken TEXT,
                      created REAL NOT NULL,source TEXT UNIQUE,status TEXT NOT NULL)''')
        db.execute("INSERT INTO messages VALUES('old','a',NULL,NULL,NULL,NULL,1000,'line-old','delivered')")
    execute = _Transaction.execute
    def interrupted(db, statement, *args):
        if statement.startswith('ALTER TABLE messages ADD COLUMN batch_reported'):
            raise RuntimeError('模擬の移行停止')
        return execute(db, statement, *args)
    with monkeypatch.context() as patch:
        patch.setattr(_Transaction, 'execute', interrupted)
        with pytest.raises(RuntimeError, match='模擬の移行停止'):
            MessageBox(tmp_path, lambda: ['a'])
    with sqlite3.connect(path) as db:
        assert 'batch_id' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}
    box = MessageBox(tmp_path, lambda: ['a'])
    assert box.deliver_line('line-old', '管理者', '古い本文') == {'accepted': True, 'activity': []}


# 見込み: 一台の送達後に残りが期限切れ・登録削除・再起動になっても、既送達分を再生し直さない。
# 合格: 一時DBと模擬時計で各終了後のLINE結果は一部成功、同じsourceの再受付や再pollでも新規配送ゼロ。
@pytest.mark.parametrize('ending', ['timeout', 'revoke', 'restart'])
def test_partial_completion_survives_remaining_delivery_end(tmp_path, monkeypatch, ending):
    now = [1000.]
    monkeypatch.setattr('family_messages.time.time', lambda: now[0])
    box = MessageBox(tmp_path, lambda: ['a', 'b'])
    for device in ('a', 'b'):
        box.poll(device, None, '')
    batch = box.enqueue('集合', '管理者', 'line-one')
    box.prepare_next(lambda *_: '集合')
    box.report('a', box.poll('a', None, '')['id'], 'delivered')
    if ending == 'timeout':
        now[0] += TIMEOUT
    elif ending == 'revoke':
        box.revoked('b')
    else:
        box = MessageBox(tmp_path, lambda: ['a', 'b'])
    assert box.deliver_line('line-one', '管理者', '集合') == {'accepted': False, 'reason': 'partial_delivery'}
    assert box.enqueue('集合', '管理者', 'line-one') == batch
    assert all(box.poll(device, None, '') is None for device in ('a', 'b'))


# 見込み: 提示後の切断は成否不明とし、再起動後や締切後に古い伝言をもう一度発話しない。
# 合格: 送信済みは不明、未送信のまま接続が切れたものは端末オフライン。一時DB・模擬時計で確認。
@pytest.mark.parametrize('restart', [True, False])
def test_expiry_or_restart_after_offer(tmp_path, monkeypatch, restart):
    now = [1000.]
    monkeypatch.setattr('family_messages.time.time', lambda: now[0])
    box = MessageBox(tmp_path, lambda: ['a'])
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
