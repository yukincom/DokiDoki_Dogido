"""共有Macで伝言の宛先と、ゲーム側の表示・再生結果を管理する。"""
from contextlib import closing, contextmanager
import secrets
import sqlite3
import time
import unicodedata

TIMEOUT = 35  # 秒。配送先が消えた私信をゲームの次回起動まで持ち越さない。
HEARTBEAT = 10
ACTIVE = ('preparing', 'ready', 'offered')
GAME_REASONS = {'no_active_game', 'game_busy', 'ambiguous_game', 'server_stopping'}
REASONS = {
    'no_recipient': '「伝言の宛先」で遊ぶMacを選んでください。',
    'client_offline': '遊ぶMacのドギドに接続できません。起動とLAN接続を確認してください。',
    'server_stopping': '遊ぶMacのドギド本体が応答していないみたい！',
    'no_active_game': 'マインクラフトが止まっているみたい！ ワールドを開いてね。',
    'game_busy': 'ドギドは今、戦闘や会話の途中みたい！ 少し待ってもう一度送ってね。',
    'ambiguous_game': 'ゲームが複数つながっていて、届け先を決められません。',
    'queue_full': '伝言が混み合っています。少し待ってください。',
    'delivery_failed': 'ドギドが伝言を表示・再生できませんでした。',
    'delivery_unknown': 'ドギドが伝えられたか確認できませんでした。',
    'player_name_unset': '遊ぶMacのドギドの呼び名を確認してください。',
}


def check_text(value, limit=500):
    if not isinstance(value, str) or any(unicodedata.category(ch) in ('Cc', 'Cs', 'Zl', 'Zp') for ch in value):
        raise ValueError('伝言は一行で入力してください。')
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise ValueError(f'伝言は1〜{limit}文字で入力してください。')
    return value


class Unavailable(ValueError):
    def __init__(self, reason):
        super().__init__(REASONS[reason])
        self.reason = reason


class MessageBox:
    def __init__(self, root, devices):
        self.path = root / 'family-messages.sqlite3'
        self.devices = devices
        # 状態はこの起動だけが持つ。DBトランザクションの中で読み書きし、定期報告をディスクに残さない。
        self.presence = {}
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recipient(device TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    id TEXT PRIMARY KEY, device TEXT, sender TEXT, player TEXT, body TEXT, spoken TEXT,
                    created REAL NOT NULL, source TEXT UNIQUE, status TEXT NOT NULL
                );
            """)
            # 起動直後に前回の未確認分を再生すると、既に届いた私信を二度読むおそれがある。
            db.execute("""UPDATE messages SET status='delivery_unknown', body=NULL,spoken=NULL,sender=NULL,player=NULL
                          WHERE status IN ('preparing','ready','offered')""")
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA secure_delete = ON')
            db.execute('BEGIN IMMEDIATE')
            yield db

    def select(self, device):
        with self.transaction() as db:
            if device not in self.devices():
                raise Unavailable('no_recipient')
            db.execute('DELETE FROM recipient')
            db.execute('INSERT INTO recipient VALUES(?)', (device,))

    @staticmethod
    def _finish(db, message_id, status):
        # 成否と受付IDのみを残し、送信者名・呼び名・原文・発話文は消す。
        db.execute('UPDATE messages SET status=?, body=NULL,spoken=NULL,sender=NULL,player=NULL WHERE id=?',
                   (status, message_id))
        print('ゲームで伝言の表示または再生を確認しました。' if status == 'delivered' else REASONS[status], flush=True)

    def revoked(self, device):
        with self.transaction() as db:
            db.execute('DELETE FROM recipient WHERE device=?', (device,))
            self.presence.pop(device, None)
            for mid, state in db.execute("SELECT id,status FROM messages WHERE device=? AND body IS NOT NULL", (device,)):
                self._finish(db, mid, 'delivery_unknown' if state == 'offered' else 'no_recipient')

    def enqueue(self, text, sender='おかあさん', source_id=None):
        text = check_text(text, 5000 if source_id else 500)
        sender = check_text(sender, 80)
        with self.transaction() as db:
            if source_id:
                existing = db.execute('SELECT id FROM messages WHERE source=?', (source_id,)).fetchone()
                if existing:
                    return existing[0]
            selected = db.execute('SELECT device FROM recipient').fetchone()
            if not selected or selected[0] not in self.devices():
                raise Unavailable('no_recipient')
            device = selected[0]
            presence = self.presence.get(device)
            now = time.time()
            if not presence or now - presence[0] > HEARTBEAT:
                raise Unavailable('client_offline')
            if presence[1]:
                raise Unavailable(presence[1])
            if not presence[2]:
                raise Unavailable('player_name_unset')
            count, = db.execute('SELECT count(*) FROM messages WHERE body IS NOT NULL').fetchone()
            if count >= 16:
                raise Unavailable('queue_full')
            mid = secrets.token_hex(16)
            db.execute("""INSERT INTO messages(id,device,sender,player,body,created,source,status)
                          VALUES(?,?,?,?,?,?,?,'preparing')""", (mid, device, sender, presence[2], text, now, source_id))
            return mid

    def deliver_line(self, source_id, sender, text):
        try:
            mid = self.enqueue(text, sender, source_id)
        except Unavailable as error:
            return error.reason
        while True:
            with self.transaction() as db:
                state = self._status(db, mid)
            if state not in ACTIVE:
                return None if state == 'delivered' else state
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
                state = presence[1] or 'game_busy'
            self._finish(db, mid, state)
        return state

    def prepare_next(self, render):
        with self.transaction() as db:
            db.execute('DELETE FROM messages WHERE body IS NULL AND created<?', (time.time() - 86400,))
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
            self.presence[device] = (time.time(), reason, player)
            row = db.execute("""SELECT id,spoken,player FROM messages WHERE device=? AND body IS NOT NULL
                                ORDER BY created,rowid LIMIT 1""", (device,)).fetchone()
            if not row:
                return None
            mid, spoken, recipient = row
            state = self._status(db, mid)
            if state not in ACTIVE:
                return None
            if state != 'offered' and (reason or recipient != player):
                self._finish(db, mid, reason or 'player_name_unset')
                return None
            if state == 'preparing':
                return None
            db.execute("UPDATE messages SET status='offered' WHERE id=?", (mid,))
            return {'id': mid, 'text': spoken}

    def report(self, device, mid, status):
        with self.transaction() as db:
            if db.execute('SELECT device,status FROM messages WHERE id=?', (mid,)).fetchone() == (device, 'offered'):
                self._finish(db, mid, status)
