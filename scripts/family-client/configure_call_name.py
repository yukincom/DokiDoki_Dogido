"""Set only Dogido's spoken call name in the kit-local .env file."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unicodedata
import uuid

KEY = 'DOGIDO_DEFAULT_CALL_NAME'
MAX_CHARS = 32


def checked_name(value: str) -> str:
    value = unicodedata.normalize('NFC', value.strip())
    if not value:
        raise ValueError('呼び名を空にはできません')
    if len(value) > MAX_CHARS:
        raise ValueError(f'呼び名は{MAX_CHARS}文字以内にしてください')
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise ValueError('呼び名に改行や制御文字は使えません')
    return value


def update_env(path: Path, value: str) -> None:
    value = checked_name(value)
    if path.is_symlink() or not path.is_file():
        raise ValueError('このセットの .env が見つからないか、リンクになっています')
    lines = path.read_text(encoding='utf-8').splitlines()
    indexes = [index for index, line in enumerate(lines)
               if line.lstrip().startswith(KEY + '=') and not line.lstrip().startswith('#')]
    if len(indexes) > 1:
        raise ValueError(f'{KEY} が重複しています。自動変更せず停止しました')
    encoded = KEY + '=' + json.dumps(value, ensure_ascii=False)
    if indexes:
        lines[indexes[0]] = encoded
    else:
        lines.extend(['', '# ドギドが発話で使う呼び名。Minecraftのアカウント名やUUIDは変えない。', encoded])
    temporary = path.with_name('.env.dogido-call-name-' + uuid.uuid4().hex + '.tmp')
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(temporary, flags, 0o600), 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', nargs='?')
    parser.add_argument('--env', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    args = parser.parse_args()
    value = args.name
    if value is None:
        value = input('ドギドに呼んでほしい名前を入力してください: ')
    update_env(args.env, value)
    print(f'呼び名を「{checked_name(value)}」に設定しました。Minecraftのアカウント名は変更していません。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f'設定できません: {error}', file=sys.stderr)
        raise SystemExit(1)
