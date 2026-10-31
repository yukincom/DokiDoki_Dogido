#!/usr/bin/env python3
"""明示実行用。既存8080モデルとVOICEVOX、afplayで短い二往復。実マイクは使わない。"""
from pathlib import Path
import argparse
import json
import tempfile
import time
from check_dialogue import ROOT, running, register, submit, wait_for, row, snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-seconds", type=int, choices=range(0,61), default=0)
    args=parser.parse_args()
    reports = []
    destination = ROOT / "reports"
    destination.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dogido-live-dialogue-") as directory:
        with running(ROOT / "target/release/dogido-rust", Path(directory), "http://127.0.0.1:8080",
                     live=True, player=Path("/usr/bin/afplay")) as (base, process, log):
            print("UI確認: " + base + "/rust-chat", flush=True)
            sid = register(base)
            for text in ["こんにちは、今日も一緒に遊ぼう。", "さっきの遊ぼうは、ゆっくり話そうって意味だよ。"]:
                start = time.monotonic()
                turn = submit(base, sid, text)
                result = wait_for(lambda: row(base, turn, {"completed", "failed", "unsupported", "quiet"}), timeout=110)
                result["wall_ms"] = round((time.monotonic()-start)*1000)
                reports.append(result)
                print(json.dumps({"input": text, "reply": result["text"], "status": result["playback_status"],
                                  "wall_ms": result["wall_ms"]}, ensure_ascii=False), flush=True)
                assert result["playback_status"] == "completed", result
            history = snapshot(base)["sessions"][0]["history"]
            assert [r["role"] for r in history] == ["user", "assistant", "user", "assistant"]
            if args.inspect_seconds: time.sleep(args.inspect_seconds)
        (destination / "dialogue-live.log").write_text(log.read_text())
    output = {"scope":"text_input_real_model_voicevox_afplay", "turns":reports, "history":history,
              "actual_microphone_tested":False, "minecraft_tested":False, "all_owned_processes_stopped":True}
    (destination / "dialogue-live.json").write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n")
    print("実モデル・合成・再生プロセス完了を確認。試験サーバーと子プロセスは全て終了しました。")


if __name__ == "__main__": main()
