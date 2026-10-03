#!/usr/bin/env python3
"""UniDic token IPC only. No prompts, overlays, dialogue, memory, or TTS policy."""
from __future__ import annotations
import json
import sys

FRAME_LIMIT = 1_000_000

class Unidic:
    def __init__(self, factory=None):
        self.factory = factory
        self.attempted = False
        self.tagger = None

    def initialize(self):
        if self.attempted:
            return self.tagger
        self.attempted = True
        try:
            factory = self.factory
            if factory is None:
                import fugashi
                factory = fugashi.Tagger
            tagger = factory()
            # Match the existing optional dictionary's one-time warmup.
            list(tagger('朝'))
            self.tagger = tagger
        except Exception:
            self.tagger = None
        return self.tagger

    def tokens(self, text):
        tagger = self.initialize()
        if tagger is None:
            return 'unavailable', []
        try:
            tokens = []
            for word in tagger(text):
                surface, feature = word.surface, word.feature
                if not isinstance(surface, str):
                    raise TypeError('invalid dictionary surface')
                row = {'surface': surface}
                for name in ('goshu', 'pos1', 'kana', 'pron'):
                    value = getattr(feature, name, None)
                    if value is not None and not isinstance(value, str):
                        raise TypeError('invalid dictionary feature')
                    row[name] = value
                tokens.append(row)
            return 'ok', tokens
        except Exception:
            # Never expose a partially parsed prefix as a successful reading.
            return 'parse_error', []


# 単独TTSはschema_version/request_id/textだけを送る。共有workerのtts_tokensは
# tts_shared_tokens.handleがopを外してここへ渡し、辞書取得と応答契約を一か所で検査する。
def handle(frame, reader):
    if (not isinstance(frame, dict)
        or set(frame) != {'schema_version', 'request_id', 'text'}
        or type(frame['schema_version']) is not int or frame['schema_version'] != 1
        or not isinstance(frame['request_id'], str) or not frame['request_id']
        or not isinstance(frame['text'], str)):
        raise ValueError('invalid token request')
    status, tokens = reader.tokens(frame['text'])
    return {'schema_version': 1, 'request_id': frame['request_id'], 'status': status, 'tokens': tokens}


def main():
    reader = Unidic()
    while line := sys.stdin.buffer.readline(FRAME_LIMIT + 1):
        try:
            if len(line) > FRAME_LIMIT or not line.endswith(b'\n'):
                raise ValueError('token request frame too large or incomplete')
            response = handle(json.loads(line), reader)
            encoded = (json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8')
            if len(encoded) > FRAME_LIMIT:
                raise ValueError('token response frame too large')
        except Exception as exc:
            print(json.dumps({'error': f'token protocol: {type(exc).__name__}'}), flush=True)
            return 1
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
