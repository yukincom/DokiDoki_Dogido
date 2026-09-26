#!/usr/bin/env python3
"""指定済みLLMで一句の相談だけを実生成。観測/発句/TTS/playerは模擬、実音声なし。

このスクリプトはMLXを起動・停止しない。--base-urlは明示必須。
現行モデルの終了理由と生成量をそのままRustへ返し、少数ケースを記録する。
"""
import argparse
import json
from pathlib import Path
import threading
import time
import urllib.request

from check_dialogue import row, submit, wait_for
from check_haiku_runtime import fixture
from check_workshop_runtime import ready, session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"scope": "live workshop model with simulated observation, poem, TTS and player", "turns": []}
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def actual(incoming):
            if incoming["max_tokens"] != 420:
                return None
            req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                json.dumps(incoming).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=35) as response:
                return json.load(response)
        control["completion_override"] = actual
        sid = ready(base, send, rows)
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(.25):
                if hud(sid)["state"] == "closed":
                    return
                send(sid)
        thread = threading.Thread(target=heartbeat)
        thread.start()
        try:
            for text in ["この句はどういう意味？", "音数を確認して",
                    "『終了』って言葉が気になっただけで、まだ相談を続けたい",
                    "今日は句の相談をここでおしまいにしよう"]:
                started = time.monotonic()
                turn = submit(base, sid, text)
                result = wait_for(lambda: row(base, turn, {"completed", "failed", "cancelled"}), timeout=45)
                summary = {"input": text, "text": result["text"], "status": result["playback_status"],
                    "action": result.get("workshop_action"), "reason": result.get("workshop_reason"),
                    "elapsed_ms_with_mock_audio": round((time.monotonic()-started)*1000),
                    "state": hud(sid)["state"], "steps": result.get("workshop_steps"),
                    "calls": result.get("llm_reports", [])}
                report["turns"].append(summary)
                print(json.dumps(summary, ensure_ascii=False), flush=True)
            report["workshop_history_pairs"] = len(session(base, sid)["workshop_history"])
            report["saved_entries"] = len(stored(sid))
        finally:
            stop.set(); thread.join(timeout=3)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
