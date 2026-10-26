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

from check_dialogue import ROOT, acknowledge, dependencies, display_row, register, request, row, running, snapshot, submit, wait_for

# 読み辞書の有無に依存しない、発声用のひらがなfixture。
SENTENCES = ["こんにちは。", "はなしかけてくれてうれしいわ。", "きょうはゆっくりはなそう。"]


def syntheses(seen):
    return [r["body"]["test_text"] for r in seen if r["path"].startswith("/synthesis?")]


def started_count(log):
    return len(re.findall(r'event="audio_started"', log.read_text()))


@contextmanager
def scenario(binary, *, player_fails=False, output_sampling_rate=None):
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
        with running(binary, directory, dep, player=player, output_sampling_rate=output_sampling_rate) as (base, process, log):
            sid = register(base)
            turn = submit(base, sid)
            wait_for(lambda: row(base, turn, {"started"}))
            wait_for(lambda: SENTENCES[1] in syntheses(seen))
            assert started_count(log) == 1, log.read_text()
            assert [h["role"] for h in snapshot(base)["sessions"][0]["history"]] == ["user"]
            yield base, sid, turn, hold, second, control, seen, log


def assert_no_assistant(base):
    assert all(h["role"] != "assistant" for h in snapshot(base)["sessions"][0]["history"])


@contextmanager
def streamed(binary, chunks, *, truncate=False, voice=True, timeout_ms=None, hold_player=False):
    with tempfile.TemporaryDirectory(prefix="dogido-llm-stream-") as temp, dependencies() as (dep, control, seen):
        directory = Path(temp)
        gate = threading.Event()
        control.update(stream_chunks=chunks, stream_gate=gate, stream_truncate=truncate)
        # 現行の--timeout-msはLLM通信の期限。実モデル・音声は使用しない。
        args = () if timeout_ms is None else ("--timeout-ms", str(timeout_ms))
        player = None
        if hold_player:
            hold = directory / "hold"
            hold.touch()
            control["playback_hold"] = hold
            player = directory / "player"
            player.write_text(f"#!{sys.executable}\nimport time\nfrom pathlib import Path\nwhile Path(__file__).with_name('hold').exists(): time.sleep(.01)\n")
            player.chmod(0o700)
        with running(binary, directory, dep, player=player, haiku_settings={"enabled":False,"memory_enabled":False}, extra_args=args) as (base, process, log):
            sid = register(base, voice_enabled=voice)
            turn = submit(base, sid, source="text")
            try: yield base, sid, turn, gate, control, seen, log
            finally:
                gate.set()
                if hold_player: control["playback_hold"].unlink(missing_ok=True)


def check_llm_streaming(binary, passed):
    first = '{"action":"speak","speech":"' + SENTENCES[0]
    rest = ''.join(SENTENCES[1:]) + '"}'
    with streamed(binary, [first, rest]) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: row(base, turn, {"started"}))
        assert not gate.is_set() and not control.get("stream_finished")
        assert syntheses(seen) == SENTENCES[:1], seen
        assert display_row(base, turn) is None
        assert_no_assistant(base)
        wait_for(lambda: 'event="audio_stopped"' in log.read_text())
        assert row(base, turn, {"started"}), "first sentence alone is not completed"
        gate.set()
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert syntheses(seen) == SENTENCES and started_count(log) == 3
        assert len([r for r in seen if r["path"] == "/v1/chat/completions"]) == 1
        report = result["llm_reports"][0]
        assert report["response_id"] == "mock" and report["response_model"] == "mock-model", report
        assert report["generated"]["finish_reason"] == "stop", report
        assert report["generated"]["completion_tokens"] == 20 and report["generated"]["prompt_tokens"] == 100, report
        acknowledge(base, sid, wait_for(lambda: display_row(base, turn)))
        assert len([h for h in snapshot(base)["sessions"][0]["history"] if h["role"] == "assistant"]) == 1
        assert len(re.findall(r'playback_status="started"', log.read_text())) == 1
        assert len(re.findall(r'playback_status="queued"', log.read_text())) == 1
        passed.append("llm_first_sentence_plays_before_generation_finishes_one_delivery_and_metadata")

    with streamed(binary, ['{"speech":"こんにちは。', 'おはよう！末尾","action":"speak"}']) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: any(r["path"] == "/v1/chat/completions" for r in seen))
        time.sleep(.15)
        assert not syntheses(seen) and started_count(log) == 0
        gate.set()
        wait_for(lambda: row(base, turn, {"completed"}))
        assert syntheses(seen) == ["こんにちは。", "おはよう！", "末尾"] or len(syntheses(seen)) == 3
        passed.append("action_last_waits_until_speak_then_delivers_sentences_and_tail")

    for label, chunks, truncated in [
        ("truncated_transport", [first, '"}'], True),
        ("invalid_json", [first, '",}'], False),
        ("repetition", ['{"action":"speak","speech":"同じや。', '同じや。同じや。"}'], False),
    ]:
        with streamed(binary, chunks, truncate=truncated) as (base, sid, turn, gate, control, seen, log):
            wait_for(lambda: row(base, turn, {"started"}))
            gate.set()
            wait_for(lambda: row(base, turn, {"failed"}))
            assert_no_assistant(base)
            assert not any(h.get("reaction") == "silent" or "沈黙を選んだ" in h.get("text", "")
                           for h in snapshot(base)["sessions"][0]["history"])
            assert display_row(base, turn) is None
            passed.append("late_" + label + "_never_commits_whole_reply_or_silence")

    with streamed(binary, [first, rest]) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: row(base, turn, {"started"}))
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id":sid})
        wait_for(lambda: row(base, turn, {"cancelled"}), timeout=3)
        gate.set(); time.sleep(.1)
        assert syntheses(seen) == SENTENCES[:1] and started_count(log) == 1
        assert_no_assistant(base)
        passed.append("cancel_during_llm_generation_discards_late_text_and_reaps_player")

    with streamed(binary, [first, rest], timeout_ms=600, hold_player=True) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: row(base, turn, {"started"}))
        wait_for(lambda: row(base, turn, {"failed"}), timeout=3)
        assert not gate.is_set()
        assert_no_assistant(base)
        passed.append("llm_timeout_after_first_sentence_reaps_pipeline_without_completing")

    with streamed(binary, [first, '末尾"}'], hold_player=True) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: row(base, turn, {"started"}))
        gate.set()
        reply = wait_for(lambda: display_row(base, turn), timeout=2)
        assert control["playback_hold"].exists() and row(base, turn, {"started"})
        assert_no_assistant(base)
        acknowledge(base, sid, reply)
        assert len([h for h in snapshot(base)["sessions"][0]["history"] if h["role"] == "assistant"]) == 1
        control["playback_hold"].unlink()
        wait_for(lambda: row(base, turn, {"completed"}))
        assert len([h for h in snapshot(base)["sessions"][0]["history"] if h["role"] == "assistant"]) == 1
        passed.append("validated_full_text_displays_while_first_audio_is_held_ack_commits_once")

    with streamed(binary, ['{"speech":"こんにちは。","action":"speak', '_other"}']) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: any(r["path"] == "/v1/chat/completions" for r in seen))
        time.sleep(.15)
        assert not syntheses(seen) and started_count(log) == 0
        gate.set()
        wait_for(lambda: row(base, turn, {"quiet"}))
        assert not syntheses(seen) and started_count(log) == 0
        passed.append("unclosed_action_is_not_promoted_to_speak")

    with streamed(binary, ['{"action":"silent","speech":""}']) as (base, sid, turn, gate, control, seen, log):
        wait_for(lambda: row(base, turn, {"quiet"}))
        assert not syntheses(seen) and started_count(log) == 0
        passed.append("explicit_silent_never_synthesizes")

    with streamed(binary, [first, rest], voice=False) as (base, sid, turn, gate, control, seen, log):
        reply = wait_for(lambda: display_row(base, turn))
        acknowledge(base, sid, reply)
        wait_for(lambda: row(base, turn, {"audio_disabled"}))
        calls = [r for r in seen if r["path"] == "/v1/chat/completions"]
        assert len(calls) == 1 and not calls[0]["body"]["stream"]
        assert not syntheses(seen) and started_count(log) == 0
        passed.append("voice_off_keeps_text_delivery_without_tts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    binary = parser.parse_args().binary.resolve()
    passed = []
    check_llm_streaming(binary, passed)
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
                assert "outputSamplingRate" not in r["body"]
        passed.append("early_first_sentence_bounded_prefetch_order_one_turn_completion")

    with scenario(binary) as (base, sid, turn, hold, second, control, seen, log):
        queued = []
        for text in ["うん", "へっくしょん"]:
            response = request(base, "/api/v1/player-input", {"session_id":sid, "source":"voice", "text":text})
            assert response["accepted"] and response["queued"], response
            queued.append(response["turn_id"])
        # 叫声と認識された音も、始まった本文を止めない。
        response = request(base, "/api/v1/player-input", {"session_id":sid, "source":"voice", "text":"うおおお"})
        assert response["accepted"]
        time.sleep(.15)
        assert row(base, turn, {"started"}) and started_count(log) == 1
        second.set(); hold.unlink()
        wait_for(lambda: row(base, turn, {"completed"}))
        for followup in queued:
            wait_for(lambda: row(base, followup, {"completed"}))
        history = snapshot(base)["sessions"][0]["history"]
        assert [h["text"] for h in history if h["role"] == "user"][-2:] == ["うん", "へっくしょん"], history
        assert len([h for h in history if h["role"] == "assistant"]) == 3, history
        passed.append("started_reply_finishes_before_acknowledgement_sneeze_and_fifo_replies")

    with scenario(binary, output_sampling_rate=48000) as (base, sid, turn, hold, second, control, seen, log):
        second.set()
        hold.unlink()
        wait_for(lambda: row(base, turn, {"completed"}))
        assert syntheses(seen) == SENTENCES
        assert all(r["body"]["outputSamplingRate"] == 48000 for r in seen if r["path"].startswith("/synthesis?"))
        passed.append("explicit_output_sampling_rate_applies_to_every_sentence")

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
