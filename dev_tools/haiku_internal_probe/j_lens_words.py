#!/usr/bin/env python3
"""日本語の除外判定だけ。既存の共有UniDicを使い、翻訳モデルは呼ばない。"""
import json
from pathlib import Path
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'dogido-rust/scripts'))
from tts_shared_tokens import handle


def copy_original(piece):
    letters = [c for c in piece if c.isalpha()]
    if not letters or piece.startswith('<') and piece.endswith('>'):
        return True
    def japanese(c):
        return any(name in unicodedata.name(c, '') for name in ('HIRAGANA', 'KATAKANA', 'CJK UNIFIED', 'CJK COMPATIBILITY'))
    if not all(japanese(c) for c in letters):
        return False
    if any('HIRAGANA' in unicodedata.name(c, '') or 'KATAKANA' in unicodedata.name(c, '') for c in letters):
        return True
    # 漢字のみの中国語を日本語と決めつけない。辞書に読みがある語だけそのまま写す。
    text = ''.join(letters)
    result = handle({'op': 'tts_tokens', 'schema_version': 1, 'request_id': 'j-lens-japanese', 'text': text})
    if result['status'] != 'ok':
        raise RuntimeError('Japanese dictionary unavailable')
    return (''.join(t['surface'] for t in result['tokens']) == text
            and all(t.get('kana') and t['kana'] != '*' for t in result['tokens']))


if __name__ == '__main__':
    pieces = json.load(sys.stdin)
    if not isinstance(pieces, list) or len(pieces) != 16 or not all(isinstance(p, str) and len(p) < 1000 for p in pieces):
        raise ValueError('invalid candidate pieces')
    print(json.dumps([copy_original(p) for p in pieces]))
