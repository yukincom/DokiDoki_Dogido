#!/usr/bin/env python3
"""Rustのtts_tokens要求に対し、共有UniDicのtoken取得結果を返すworker。

辞書は初回利用時に初期化する。読みの判定・相談・保存はRustが所有する。
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# 受理するのはop=tts_tokensだけ。辞書結果を返し、読みや相談内容は判断しない。
# haiku_tokensの読込上限・改行・出力上限検査は共有しておらず、この入口は読込後の入力サイズだけを検査する。
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
