#!/usr/bin/env python3
"""文の先読み・順序・取消を実HTTPと模擬再生で確認。実モデル／音声は使わない。"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

from check_dialogue import ROOT, dependencies, register, request, row, running, snapshot, submit, wait_for

# 読み辞書の有無に依存しない、発声用のひらがなfixture。
SENTENCES = ["こんにちは。", "はなしかけてくれてうれしいわ。", "きょうはゆっくりはなそう。"]


def syntheses(seen):
    return [r["body"]["test_text"] for r in seen if r["path"].startswith("/synthesis?")]


def started_count(log):
    return len(re.findall(r'event="audio_started"', log.read_text()))


@contextmanager
def scenario(binary, *, player_fails=False):
    with tempfile.TemporaryDirectory(prefix="dogido-sentence-") as temp, dependencies() as (dep, control, seen):
        directory = Path(temp)
        hold = directory / "hold"
        hold.touch()
        player = directory / "player"
        player.write_text(f"#!{sys.executable}\nimport time,sys\nfrom pathlib import Path\n"
                          "hold=Path(__file__).with_name('hold')\n"
                          "while hold.exists(): time.sleep(.01)\n"
                          f"sys.exit({1 if player_fails else 0})\n")
        player.chmod(0o700)
        control["leaf"] = "".join(SENTENCES)
        second = threading.Event()
        control["tts_gates"][SENTENCES[1]] = second
        # runningのfinallyでサーバー・全player・helper・WAVの回収も検査する。
        with running(binary, directory, dep, player=player) as (base, process, log):
            sid = register(base)
            turn = submit(base, sid)
            wait_for(lambda: row(base, turn, {"started"}))
            wait_for(lambda: SENTENCES[1] in syntheses(seen))
            assert started_count(log) == 1, log.read_text()
            assert [h["role"] for h in snapshot(base)["sessions"][0]["history"]] == ["user"]
            yield base, sid, turn, hold, second, control, seen, log


def assert_no_assistant(base):
    assert all(h["role"] != "assistant" for h in snapshot(base)["sessions"][0]["history"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    binary = parser.parse_args().binary.resolve()
    passed = []
    with scenario(binary) as (base, sid, turn, hold, second, control, seen, log):
        # 二文目のHTTPを止めても一文目は再生を開始する。
        assert not second.is_set()
        assert syntheses(seen) == SENTENCES[:2]
        second.set()
        wait_for(lambda: 'event="audio_sentence_ready" sentence=2' in log.read_text())
        time.sleep(.1)
        # 再生中＋先読み一文の上限。三文目の合成は一文目終了まで始めない。
        assert syntheses(seen) == SENTENCES[:2], seen
        hold.unlink()
        wait_for(lambda: row(base, turn, {"completed"}))
        assert syntheses(seen) == SENTENCES
        playback_order = re.findall(r'event="audio_started" pid=Some\(\d+\) sentence=(\d+)', log.read_text())
        assert playback_order == ["1", "2", "3"], log.read_text()
        assert len(re.findall(r'playback_status="started"', log.read_text())) == 1
        assert [h["role"] for h in snapshot(base)["sessions"][0]["history"]] == ["user", "assistant"]
        for r in seen:
            if r["path"].startswith("/synthesis?"):
                assert "speaker=21" in r["path"]
                assert (r["body"]["speedScale"], r["body"]["pitchScale"], r["body"]["volumeScale"]) == (.88, 0, 1)
        passed.append("early_first_sentence_bounded_prefetch_order_one_turn_completion")

    with scenario(binary) as (base, sid, turn, hold, second, control, seen, log):
        control["fail_sentence"] = SENTENCES[1]
        second.set()
        hold.unlink()
        wait_for(lambda: row(base, turn, {"failed"}))
        assert started_count(log) == 1
        assert_no_assistant(base)
        assert SENTENCES[2] not in syntheses(seen)
        passed.append("later_sentence_failure_never_commits_full_reply")

    with scenario(binary) as (base, sid, turn, hold, second, control, seen, log):
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
        wait_for(lambda: row(base, turn, {"cancelled"}), timeout=3)
        assert_no_assistant(base)
        second.set()
        time.sleep(.1)
        assert started_count(log) == 1
        assert SENTENCES[2] not in syntheses(seen)
        assert not list((hold.parent / "audio").glob("*.wav"))
        passed.append("interrupt_stops_player_and_discards_late_prefetch")

    with scenario(binary) as (base, sid, turn, hold, second, control, seen, log):
        hold.unlink()
        wait_for(lambda: 'event="audio_stopped"' in log.read_text())
        # 一文目が完了しても、次の合成待ちを全返答のcompletedにしない。
        assert row(base, turn, {"started"})
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
        wait_for(lambda: row(base, turn, {"cancelled"}), timeout=3)
        assert_no_assistant(base)
        second.set()
        time.sleep(.1)
        assert started_count(log) == 1
        passed.append("interrupt_between_sentences_never_resumes_old_audio")

    with scenario(binary, player_fails=True) as (base, sid, turn, hold, second, control, seen, log):
        hold.unlink()
        # 次のHTTPを解除しなくても、再生失敗で先読みを取り消して終了する。
        wait_for(lambda: row(base, turn, {"failed"}), timeout=3)
        assert_no_assistant(base)
        assert started_count(log) == 1
        passed.append("player_failure_cancels_pending_synthesis")

    with scenario(binary) as (base, sid, turn, hold, second, control, seen, log):
        # 二文目のHTTPと一文目の再生を保留したまま、runningのSIGINT終了検査へ。
        assert not second.is_set() and hold.exists()
    passed.append("shutdown_reaps_player_helper_and_prefetch_without_waiting_for_engine")

    output = ROOT / "reports/sentence-audio-check.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({"passed": passed, "model": "mock", "tts": "mock", "playback": "mock",
                                  "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2) + "\n")
    print(f"PASS {len(passed)} sentence audio checks; all owned processes stopped")


if __name__ == "__main__":
    main()
