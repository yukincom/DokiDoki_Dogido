"""共有Macから登録PC全員への伝言と、端末ごとの表示・再生結果を管理する。"""
from contextlib import closing, contextmanager
import json
import secrets
import sqlite3
import time
import unicodedata

TIMEOUT = 35  # 秒。配送先が消えた私信をゲームの次回起動まで持ち越さない。
HEARTBEAT = 10
ACTIVE = ('preparing', 'ready', 'offered')
GAME_REASONS = {'no_active_game', 'game_busy', 'ambiguous_game', 'server_stopping'}
REASONS = {
    'no_recipient': '登録されているPCがありません。設定の「登録追加」から登録してください。',
    'client_offline': '遊ぶMacのドギドに接続できません。起動とLAN接続を確認してください。',
    'server_stopping': '遊ぶMacのドギド本体が応答していないみたい！',
    'no_active_game': 'マインクラフトが止まっているみたい！ ワールドを開いてね。',
    'game_busy': 'ドギドは今、戦闘や会話の途中みたい！ 少し待ってもう一度送ってね。',
    'ambiguous_game': 'ゲームが複数つながっていて、届け先を決められません。',
    'queue_full': '伝言が混み合っています。少し待ってください。',
    'delivery_failed': 'ドギドが伝言を表示・再生できませんでした。',
    'delivery_unknown': 'ドギドが伝えられたか確認できませんでした。',
    'partial_delivery': '一部のPCには届きましたが、全PCへの到着は確認できませんでした。',
    'player_name_unset': '遊ぶMacのドギドの呼び名を確認してください。',
}


def check_text(value, limit=500):
    if not isinstance(value, str) or any(unicodedata.category(ch) in ('Cc', 'Cs', 'Zl', 'Zp') for ch in value):
        raise ValueError('伝言は一行で入力してください。')
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise ValueError(f'伝言は1〜{limit}文字で入力してください。')
    return value


def check_activity(value):
    if (not isinstance(value, list) or len(value) > 2
            or any(tag not in ('combat', 'senryu') for tag in value) or len(set(value)) != len(value)):
        raise ValueError('ゲームの状態を確認できません。')
    return value


def _result(status, activity):
    if status == 'delivered':
        return {'accepted': True, 'activity': activity}
    return {'accepted': False, 'reason': status}


class _Transaction(sqlite3.Connection):
    """共有UIの報告はDBの確定後に、本文なしで呼出元へ渡す。"""
    ui_results: list
    display_events: list


class Unavailable(ValueError):
    def __init__(self, reason):
        super().__init__(REASONS[reason])
        self.reason = reason


class MessageBox:
    def __init__(self, root, devices, on_ui_result=None, on_event=None, device_name=None):
        self.path = root / 'family-messages.sqlite3'
        self.devices = devices
        self.on_ui_result = on_ui_result
        self.on_event = on_event
        self.device_name = device_name or (lambda _: '登録PC')
        self.last_offered = {}
        # 状態はこの起動だけが持つ。DBトランザクションの中で読み書きし、定期報告をディスクに残さない。
        self.presence = {}
        with self.transaction() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id TEXT PRIMARY KEY, device TEXT, sender TEXT, player TEXT, body TEXT, spoken TEXT,
                    created REAL NOT NULL, source TEXT UNIQUE, status TEXT NOT NULL
                )
            """)
            if 'activity' not in {row[1] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute("""ALTER TABLE messages ADD COLUMN activity TEXT NOT NULL DEFAULT '[]'""")
            columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
            if 'batch_id' not in columns:
                db.execute('ALTER TABLE messages ADD COLUMN batch_id TEXT')
                db.execute('ALTER TABLE messages ADD COLUMN batch_reported INTEGER NOT NULL DEFAULT 0')
                # 旧一台宛ての通知は一件の配送群として引き継ぎ、LINE再送で再配送しない。
                db.execute('UPDATE messages SET batch_id=id')
            db.execute('CREATE INDEX IF NOT EXISTS messages_batch ON messages(batch_id)')
            db.execute('DROP TABLE IF EXISTS recipient')
            # 起動直後に前回の未確認分を再生すると、既に届いた私信を二度読むおそれがある。
            db.execute("""UPDATE messages SET status='delivery_unknown', body=NULL,spoken=NULL,sender=NULL,player=NULL
                          WHERE status IN ('preparing','ready','offered')""")
            db.execute('UPDATE messages SET batch_reported=1')
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, factory=_Transaction)) as db, db:
            reports = db.ui_results = []
            events = db.display_events = []
            db.execute('PRAGMA secure_delete = ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
        for result in reports:
            self.on_ui_result(result)
        for speaker, text in events:
            self.on_event(speaker, text)

    def _batch_result(self, db, batch):
        rows = db.execute('SELECT status,activity FROM messages WHERE batch_id=?', (batch,)).fetchall()
        if not rows:
            return _result('delivery_unknown', [])
        states = {state for state, _ in rows}
        if states & set(ACTIVE):
            return None
        if states == {'delivered'}:
            activity = {tag for _, value in rows for tag in json.loads(value)}
            return _result('delivered', sorted(activity))
        if 'delivered' in states:
            reason = 'partial_delivery'
        elif len(states) == 1:
            reason = next(iter(states))
        else:
            reason = 'delivery_unknown' if 'delivery_unknown' in states else 'delivery_failed'
        return _result(reason, [])

    def _report_batch(self, db, batch):
        # 外部の結果通知は一回の送信単位。全員が終端する前に一台の成功を全体成功へしない。
        result = self._batch_result(db, batch)
        if result is None:
            return
        row = db.execute('SELECT source,batch_reported FROM messages WHERE id=?', (batch,)).fetchone()
        if row and not row[1]:
            db.execute('UPDATE messages SET batch_reported=1 WHERE id=?', (batch,))
            if self.on_ui_result and row[0] is None:
                db.ui_results.append(result)

    def _finish(self, db, message_id, status, activity=None):
        # 端末で表示・再生を確認した本文だけを画面へ渡す。失敗時の生成文は発話済みにしない。
        spoken, device, batch = db.execute('SELECT spoken,device,batch_id FROM messages WHERE id=?', (message_id,)).fetchone()
        if self.on_event and status == 'delivered' and spoken:
            db.display_events.append(('ドギド', spoken))
        # 成否と受付IDのみを残し、送信者名・呼び名・原文・発話文は消す。
        activity = activity or []
        db.execute('UPDATE messages SET status=?,activity=?,body=NULL,spoken=NULL,sender=NULL,player=NULL WHERE id=?',
                   (status, json.dumps(activity), message_id))
        text = 'ゲームで伝言の表示または再生を確認しました。' if status == 'delivered' else REASONS[status]
        count, = db.execute('SELECT count(*) FROM messages WHERE batch_id=?', (batch,)).fetchone()
        if count > 1:
            text = f'{self.device_name(device)}：{text}'
        if self.on_event:
            source, = db.execute('SELECT source FROM messages WHERE id=?', (batch,)).fetchone()
            db.display_events.append(('ユノ' if source else '伝言', text))
        else:
            print(text, flush=True)
        self._report_batch(db, batch)

    def revoked(self, device):
        with self.transaction() as db:
            self.presence.pop(device, None)
            self.last_offered.pop(device, None)
            for mid, state in db.execute("SELECT id,status FROM messages WHERE device=? AND body IS NOT NULL", (device,)):
                self._finish(db, mid, 'delivery_unknown' if state == 'offered' else 'no_recipient')

    def enqueue(self, text, sender='管理者', source_id=None):
        text = check_text(text, 5000 if source_id else 500)
        sender = check_text(sender, 80)
        with self.transaction() as db:
            if source_id:
                existing = db.execute('SELECT id FROM messages WHERE source=?', (source_id,)).fetchone()
                if existing:
                    return existing[0]
            devices = sorted(set(self.devices()))
            if not devices:
                raise Unavailable('no_recipient')
            now = time.time()
            count, = db.execute('SELECT count(DISTINCT batch_id) FROM messages WHERE body IS NOT NULL').fetchone()
            if count >= 16:
                raise Unavailable('queue_full')
            batch = secrets.token_hex(16)
            failures = []
            # 受付時の全登録を固定してから個別の成否を確定する。未接続の一台で他の配送を止めない。
            for index, device in enumerate(devices):
                mid = batch if index == 0 else secrets.token_hex(16)
                presence = self.presence.get(device)
                reason = ('client_offline' if not presence or now - presence[0] > HEARTBEAT else
                          presence[1] if presence[1] != 'game_busy' else None)
                db.execute("""INSERT INTO messages(id,device,sender,player,body,created,source,status,batch_id)
                              VALUES(?,?,?,?,?,?,?,'preparing',?)""",
                           (mid, device, sender, presence[2] if presence else '', text, now,
                            source_id if index == 0 else None, batch))
                if reason:
                    failures.append((mid, reason))
            if self.on_event:
                db.display_events.append(('ユノ' if source_id else '伝言', f'{sender}から　{text}'))
            for mid, reason in failures:
                self._finish(db, mid, reason)
            return batch

    def deliver_line(self, source_id, sender, text):
        try:
            mid = self.enqueue(text, sender, source_id)
        except Unavailable as error:
            if self.on_event:
                self.on_event('ユノ', str(error))
            return _result(error.reason, [])
        while True:
            with self.transaction() as db:
                for delivery, in db.execute('SELECT id FROM messages WHERE batch_id=?', (mid,)).fetchall():
                    self._status(db, delivery)
                result = self._batch_result(db, mid)
                if result is not None:
                    return result
            time.sleep(0.25)

    def _status(self, db, mid):
        row = db.execute('SELECT status, created, device FROM messages WHERE id=?', (mid,)).fetchone()
        if not row:
            return 'delivery_unknown'
        state, created, device = row
        if state in ACTIVE and time.time() - created >= TIMEOUT:
            presence = self.presence.get(device)
            if state == 'offered':
                state = 'delivery_unknown'
            elif not presence or time.time() - presence[0] > HEARTBEAT:
                state = 'client_offline'
            else:
                state = presence[1] or 'delivery_failed'
            self._finish(db, mid, state)
        return state

    def prepare_next(self, render):
        with self.transaction() as db:
            # 群の先頭だけ先に消すとsourceの再送照合を失うため、全配送が終了した群をまとめて整理する。
            db.execute("""DELETE FROM messages WHERE batch_id IN (
                SELECT batch_id FROM messages GROUP BY batch_id
                HAVING max(created)<? AND sum(status IN ('preparing','ready','offered'))=0
            )""", (time.time() - 86400,))
            for mid, in db.execute('SELECT id FROM messages WHERE body IS NOT NULL AND created<=?',
                                   (time.time() - TIMEOUT,)):
                self._status(db, mid)
            row = db.execute("""SELECT id,sender,player,body FROM messages WHERE status='preparing'
                                ORDER BY created,rowid LIMIT 1""").fetchone()
        if not row:
            return False
        mid, sender, player, body = row
        # モデル応答中に通信・設定・結果照会がロック待ちにならないよう、この区間はDBを閉じる。
        spoken = render(sender, player, body)
        with self.transaction() as db:
            if self._status(db, mid) == 'preparing':
                db.execute("UPDATE messages SET spoken=?,status='ready' WHERE id=?", (spoken, mid))
        return True

    def poll(self, device, reason, player):
        with self.transaction() as db:
            now = time.time()
            rows = db.execute("""SELECT id,spoken,sender,body,created FROM messages WHERE device=? AND body IS NOT NULL
                                 ORDER BY created,rowid""", (device,)).fetchall()
            available = []
            for mid, spoken, sender, body, created in rows:
                state = self._status(db, mid)
                if state not in ACTIVE:
                    continue
                if state != 'offered' and reason not in (None, 'game_busy'):
                    self._finish(db, mid, reason)
                    continue
                if state == 'preparing':
                    # モデルの生成待ちが重なっても、LINEの結果待ち期限内に原文を届ける余裕を取る。
                    if now - created < TIMEOUT / 2:
                        continue
                    spoken = f'{sender}から伝言や。「{body}」'
                available.append((mid, spoken))
            self.presence[device] = (now, reason, player)
            if not available:
                return None
            # 表示確認が遅い一件だけを再提示し続けず、同じ登録端末に届いた他の伝言も順に渡す。
            ids = [mid for mid, _ in available]
            last = self.last_offered.get(device)
            index = (ids.index(last) + 1) % len(ids) if last in ids else 0
            mid, spoken = available[index]
            self.last_offered[device] = mid
            db.execute("UPDATE messages SET status='offered',spoken=? WHERE id=?", (spoken, mid))
            return {'id': mid, 'text': spoken}

    def report(self, device, mid, status, activity=None):
        with self.transaction() as db:
            if db.execute('SELECT device,status FROM messages WHERE id=?', (mid,)).fetchone() == (device, 'offered'):
                if status == 'game_busy':
                    # 旧本体が受付前に交戦等を返した場合だけ、期限内に同じIDで再度届ける。
                    db.execute("UPDATE messages SET status='ready' WHERE id=?", (mid,))
                else:
                    self._finish(db, mid, status, activity)
