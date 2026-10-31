#!/usr/bin/env python3
"""Rustの一案の相談・部分置換・差し替え表現の訂正を指定済み実モデルで確認する。

発句・Minecraft観測・TTS・再生は模擬。既存推論サーバーを起動/停止しない。
生成結果の本文・終了理由・トークン数を加工せずRustに渡す。
"""
import argparse
import json
from pathlib import Path
import threading
import time
import urllib.request

from check_dialogue import row, submit, wait_for
from check_haiku_runtime import LINES, fixture
from check_workshop_runtime import ready

CASES = [
    ("partial_direct", ["くろいをしろいに変えて"], [LINES[0], "しろいおのへと", LINES[2]]),
    ("one_idea", ["『くろい』を『しろい』にするのはどう？", "明るい感じがするね", "そうしましょう"],
        [LINES[0], "しろいおのへと", LINES[2]]),
    ("correct_replacement", ["『くろい』を『あお』にするのはどう？", "それにして", "やっぱり『あおい』にして"],
        [LINES[0], "あおいおのへと", LINES[2]]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=[case[0] for case in CASES], action="append")
    args = parser.parse_args()
    # Do not overwrite an earlier measurement.
    with args.output.open("x") as output:
        for name, texts, expected_current in CASES:
            if args.case and name not in args.case:
                continue
            with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
                calls = []
                def actual(incoming):
                    if incoming["max_tokens"] != 420:
                        return None
                    outgoing = {**incoming, "model": args.model}
                    started = time.monotonic()
                    req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                        json.dumps(outgoing).encode(), {"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=60) as response:
                        result = json.load(response)
                    calls.append({"request": outgoing, "response": result, "elapsed_ms": round((time.monotonic()-started)*1000)})
                    return result
                control["completion_override"] = actual
                sid = ready(base, send, rows)
                stop = threading.Event()
                def heartbeat():
                    while not stop.wait(.25):
                        if hud(sid)["state"] == "closed":
                            return
                        send(sid)
                thread = threading.Thread(target=heartbeat); thread.start()
                results = []
                try:
                    for text in texts:
                        count = len(calls)
                        turn = submit(base, sid, text)
                        result = wait_for(lambda: row(base, turn, {"completed", "failed", "cancelled"}), timeout=100)
                        view = hud(sid)
                        record = {"case": name, "input": text, "result": result,
                            "hud": view, "model_calls": calls[count:]}
                        results.append(record)
                        output.write(json.dumps(record, ensure_ascii=False) + "\n"); output.flush()
                        print(json.dumps({"case": name, "input": text, "action": result.get("workshop_action"),
                            "reason": result.get("workshop_reason"), "status": result["playback_status"],
                            "calls": len(calls)-count, "reply": result.get("text"), "pending": view["pending_lines"]}, ensure_ascii=False), flush=True)
                    passed = (not hud(sid)["pending_lines"] and hud(sid)["canonical_lines"] == expected_current)
                    if name == "correct_replacement":
                        passed = passed and results[-2]["result"].get("workshop_outcome") == "player_edit_rejected"
                    summary = {"case": name, "passed": passed, "saved_entries": len(stored(sid)),
                        "all_turns_completed": all(r["result"]["playback_status"] == "completed" for r in results)}
                    output.write(json.dumps(summary, ensure_ascii=False) + "\n"); output.flush()
                    print(json.dumps(summary, ensure_ascii=False), flush=True)
                finally:
                    stop.set(); thread.join(timeout=3)
    print("試験用のサーバー・helper・再生プロセスは回収済み。既存の推論サーバーは変更していません。")


if __name__ == "__main__":
    main()
