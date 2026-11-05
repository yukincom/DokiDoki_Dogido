"""親の読み辞書だけを配り、子の既存MemoryStoreが読む複製を原子的に更新する。"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import unicodedata

import transport

LIMIT = 1 << 20
INTERVAL = 10
FIELDS = {'id', 'surface', 'reading', 'wrong_reading', 'forbidden_readings', 'source'}


def kana(value):
    return isinstance(value, str) and 1 <= len(value) <= 80 and all('ぁ' <= c <= 'ん' or c == 'ー' for c in value)


def text(value, limit):
    return (isinstance(value, str) and 1 <= len(value) <= limit and value.strip() == value
            and not any(unicodedata.category(c) == 'Cc' for c in value))


def snapshot(value):
    """通信の境界で全体を確認する。一行の欠落や壊れを削除として適用しない。"""
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'corrections'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['corrections'], list)):
        raise ValueError('読み辞書の形式を確認できません。')
    surfaces = set()
    for row in value['corrections']:
        if (not isinstance(row, dict) or set(row) != FIELDS
                or not text(row['id'], 512) or not text(row['surface'], 80)
                or row['surface'] in surfaces or not kana(row['reading'])
                or row['wrong_reading'] is not None and (not kana(row['wrong_reading']) or row['wrong_reading'] == row['reading'])
                or not isinstance(row['forbidden_readings'], list)
                or not all(kana(item) for item in row['forbidden_readings'])
                or row['reading'] in row['forbidden_readings']
                or len(set(row['forbidden_readings'])) != len(row['forbidden_readings'])
                or row['source'] is not None and not text(row['source'], 512)):
            raise ValueError('読み辞書の項目を確認できません。')
        surfaces.add(row['surface'])
    if len(json.dumps(value, ensure_ascii=False).encode()) > LIMIT:
        raise ValueError('読み辞書のサイズを確認してください。')
    return value


class ParentExport:
    """本体が停止中でも、既存設定とRustの正本readerから辞書を取得する。"""
    def __init__(self, settings_dir):
        self.settings_dir = Path(settings_dir)

    def __call__(self):
        # 設定は各取得時に通常と同じget_settingsで解決し、保存先の別正本を作らない。
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('export_readings.py')),
                                 str(self.settings_dir)], capture_output=True, timeout=5, check=True)
        if len(result.stdout) > LIMIT:
            raise ValueError('読み辞書のサイズを確認してください。')
        return snapshot(json.loads(result.stdout))


def parent_export():
    config = Path(__file__).resolve().parent.parent / 'launch.plist'
    if config.is_file():
        root = plistlib.loads(config.read_bytes()).get('readings_settings_dir')
        if not root:
            return None
    else:
        root = Path(__file__).resolve().parents[2]
    return ParentExport(root)


def read_replica(path):
    if path.is_symlink():
        raise ValueError('読み辞書の保存先がリンクになっています。')
    try:
        with path.open('rb') as handle:
            body = handle.read(LIMIT + 1)
    except FileNotFoundError:
        return None
    if len(body) > LIMIT:
        raise ValueError('読み辞書のサイズを確認してください。')
    return json.loads(body)


class Replica:
    def __init__(self, memory_dir, pin):
        if not isinstance(pin, str) or not transport.HEX64.fullmatch(pin):
            raise ValueError('読み辞書の共有元を確認できません。')
        self.pin = pin
        self.path = Path(memory_dir) / 'long_term/shared_readings.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 本体が起動する前に閲覧専用を確定。別の親へ登録した場合、旧親の辞書を混ぜない。
        current = read_replica(self.path)
        if current is not None:
            if (not isinstance(current, dict) or set(current) != {'schema_version', 'host_pin', 'corrections'}
                    or not isinstance(current['host_pin'], str)
                    or not transport.HEX64.fullmatch(current['host_pin'])):
                raise ValueError('保存済みの読み辞書を確認できません。')
            snapshot({key: value for key, value in current.items() if key != 'host_pin'})
        if current is None or current.get('host_pin') != pin:
            self.save({'schema_version': 1, 'corrections': []})

    def save(self, value):
        value = dict(snapshot(value), host_pin=self.pin)
        # 共通atomic writerの実encodingで上限を確認し、Rustが読めない大きさの複製を残さない。
        if len(json.dumps(value).encode()) > LIMIT:
            raise ValueError('読み辞書のサイズを確認してください。')
        if read_replica(self.path) != value:
            transport._save(self.path, value)

    def sync(self, address, credentials):
        if credentials['pin'] != self.pin:
            raise ValueError('読み辞書の共有元が変更されています。')
        conn, _ = transport._tls(address, self.pin)
        try:
            conn.sock.settimeout(5)
            conn.request('GET', transport.READINGS, headers={'Authorization': transport._auth(credentials)})
            response = conn.getresponse()
            if response.status != 200 or response.getheader('Content-Type', '').split(';')[0] != 'application/json':
                raise OSError('共有元の読み辞書を取得できません。')
            body = response.read(LIMIT + 1)
            if len(body) > LIMIT:
                raise ValueError('読み辞書のサイズを確認してください。')
            self.save(json.loads(body))
        finally:
            conn.close()


def client_replica(credentials):
    root = os.environ.get('DOGIDO_SHARED_READINGS_MEMORY_DIR')
    return Replica(root, credentials['pin']) if root else None


def run(stop, replica, resolve, credentials):
    if replica is None:
        return
    while not stop.is_set():
        try:
            replica.sync(resolve(), credentials)
        except (OSError, ValueError, http.client.HTTPException):
            # 切断・取得失敗は最後の有効な複製を保持し、空の辞書へ読み替えない。
            pass
        stop.wait(INTERVAL)
