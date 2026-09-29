#!/usr/bin/env python3
"""Automatic-haiku dictionary IPC only; all preparation and decisions are native."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tts_shared_tokens import handle
FRAME_LIMIT = 1_000_000

def main():
    while line := sys.stdin.buffer.readline(FRAME_LIMIT + 1):
        try:
            if len(line) > FRAME_LIMIT or not line.endswith(b'\n'):
                raise ValueError('token frame too large or incomplete')
            response = handle(json.loads(line))
            encoded = (json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8')
            if len(encoded) > FRAME_LIMIT:
                raise ValueError('token response too large')
        except Exception as exc:
            print(json.dumps({'error': f'token protocol: {type(exc).__name__}'}), flush=True)
            return 1
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
