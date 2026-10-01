#!/usr/bin/env python3
"""Optional UniDic token adapter for one Rust-owned workshop turn.

No workshop parser, prompt, validator, catalog, state, or persistence is imported.
The shared token adapter lazily owns the existing optional dictionary singleton.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tts_shared_tokens import handle


def main():
    for line in sys.stdin:
        try:
            if len(line.encode("utf-8")) > 1_000_000:
                raise ValueError("helper frame too large")
            output = handle(json.loads(line))
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)
            return 1
        print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
