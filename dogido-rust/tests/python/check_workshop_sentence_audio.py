#!/usr/bin/env python3
"""見込み: 通常雑談と同じ文ストリームなら相談の一文目も全文より先に届く。合格: 一回生成・順序・配送一回・操作待ち・取消を実HTTPで照合。LLM/TTS/player/BERTは模擬し、実音声の体感は別確認。"""
import json
import re
import threading
from contextlib import contextmanager

from check_dialogue import acknowledge, display_row, register, request, row, submit, wait_for
from check_sentence_audio import syntheses
from check_workshop_edits import edit, revisions
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, session, step

SENTENCES = ["ひとつめやで。", "ふたつめやで。"]


def player_count(log, offset):
    return len(re.findall(r'event="audio_started"', log.read_text()[offset:]))


@contextmanager
def streaming(chunks, *, payload=None, truncate=False, extra_args=()):
    with fixture(extra_args=extra_args) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, _ = f
        calls = install(control, lambda text, prompt, n: payload or step(text, speech="".join(SENTENCES)))
        sid = register(base, preview=False, voice_enabled=True, body=True)
        send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
        gate = threading.Event()
        control.update(stream_chunks=chunks, stream_gate=gate, stream_truncate=truncate)
        offset, seen_offset = len(log.read_text()), len(seen)
        try:
            yield f, sid, gate, calls, offset, seen_offset
        finally:
            gate.set()


# 見込み: 文境界だけ先に受け取れば本文の続きを待たず発声できる。合格: gate中に一文のみ再生・履歴未確定、解除後は二文と履歴一回。生成・音声は固定。
def check_first_sentence(passed):
    prefix = '{"action":"explain","speech":"' + SENTENCES[0]
    with streaming([prefix, SENTENCES[1] + '"}']) as (f, sid, gate, calls, offset, seen_offset):
        base, _, log, _, seen, _, _, _, _, hud, _, stored, _ = f
        original, entries = hud(sid)["canonical_lines"], stored(sid)
        turn = submit(base, sid, "この句を説明して")
        wait_for(lambda: row(base, turn, {"started"}))
        assert not gate.is_set() and syntheses(seen[seen_offset:]) == SENTENCES[:1]
        assert not display_row(base, turn) and not session(base, sid)["workshop_history"]
        wait_for(lambda: 'event="audio_stopped"' in log.read_text()[offset:])
        assert row(base, turn, {"started"}), "一文目だけでは返答全体の完了にしない"
        gate.set()
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["text"] == "".join(SENTENCES)
        assert syntheses(seen[seen_offset:]) == SENTENCES and player_count(log, offset) == 2
        requests = [r["body"] for r in seen[seen_offset:] if r["path"] == "/v1/chat/completions"]
        assert len(calls) == len(requests) == 1 and requests[0]["stream"]
        assert requests[0]["tools"], "既存の参照toolを同じ要求へ載せる"
        acknowledge(base, sid, display_row(base, turn)); acknowledge(base, sid, display_row(base, turn))
        history = session(base, sid)["workshop_history"]
        assert len(history) == 1 and history[0]["dogido_text"] == result["text"]
        assert hud(sid)["canonical_lines"] == original and stored(sid) == entries
        passed.append("workshop_first_sentence_before_llm_end_one_request_one_delivery")


# 見込み: actionが後置された本文は用途確定まで保留できる。合格: gate中の合成0、explain確定後は本文二文だけを順に再生。モデルの意味理解は固定。
def check_action_last(passed):
    with streaming(['{"speech":"' + SENTENCES[0], SENTENCES[1] + '","action":"explain"}']) as (f, sid, gate, calls, offset, seen_offset):
        base, _, log, _, seen, _, _, _, _, _, _, _, _ = f
        turn = submit(base, sid, "この句を説明して")
        wait_for(lambda: len(calls) == 1)
        assert syntheses(seen[seen_offset:]) == [] and player_count(log, offset) == 0
        gate.set()
        wait_for(lambda: row(base, turn, {"completed"}))
        assert syntheses(seen[seen_offset:]) == SENTENCES and player_count(log, offset) == 2
        passed.append("workshop_action_last_waits_without_reading_json_or_reasoning")


# 見込み: tool差分は発声せず完成後に既存実行へ渡せる。合格: gate中の音声・revision0、解除後に上五だけ一度保存し結果文のみ再生。編集とBERTの判断は固定。
def check_edit_tool(passed):
    with fixture() as f:
        base, _, log, control, seen, _, _, _, send, hud, rows, _, folder = f
        calls = install(control, lambda text, prompt, n: edit(text))
        sid = ready(base, send, rows)
        original = hud(sid)["canonical_lines"]
        gate = threading.Event()
        def fragments(incoming, complete):
            call = complete["message"]["tool_calls"][0]
            arguments = call["function"]["arguments"]
            split = len(arguments) // 2
            return [{"tool_calls":[{"index":0,"id":call["id"],"type":"function",
                        "function":{"name":call["function"]["name"],"arguments":arguments[:split]}}]},
                    {"tool_calls":[{"index":0,"function":{"arguments":arguments[split:]}}]}]
        control.update(stream_gate=gate, stream_events=fragments)
        offset, seen_offset = len(log.read_text()), len(seen)
        try:
            turn = submit(base, sid, "上五を『さくらいろ』にして")
            wait_for(lambda: len(calls) == 1)
            assert not revisions(folder, sid) and not syntheses(seen[seen_offset:])
            gate.set()
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_outcome"] == "player_edit_saved" and len(revisions(folder, sid)) == 1
            assert hud(sid)["canonical_lines"] == ["さくらいろ", *original[1:]]
            assert len(calls) == 1 and player_count(log, offset) == 1
            assert result["text"] == "変更したで。" and not result.get("audio_streamed")
            passed.append("native_stream_fragments_wait_for_edit_and_save_once")
        finally:
            gate.set()


# 見込み: 先行再生後の破損は未完成の返答を確定せず終えられる。合格: 追加生成・定型返答・履歴・句変更なし。通信断と外形不正を固定し、実モデルの頻度は測らない。
def check_late_failures(passed):
    prefix = '{"action":"explain","speech":"' + SENTENCES[0]
    for label, tail, truncate in [("transport", '"}', True), ("json", '",}', False)]:
        with streaming([prefix, tail], truncate=truncate) as (f, sid, gate, calls, offset, seen_offset):
            base, _, log, _, seen, _, _, _, _, hud, _, stored, _ = f
            original, entries = hud(sid)["canonical_lines"], stored(sid)
            turn = submit(base, sid, "この句を説明して")
            wait_for(lambda: row(base, turn, {"started"}))
            gate.set()
            wait_for(lambda: row(base, turn, {"failed"}))
            assert len(calls) == 1 and player_count(log, offset) == 1
            assert syntheses(seen[seen_offset:]) == SENTENCES[:1]
            assert not display_row(base, turn) and not session(base, sid)["workshop_history"]
            assert hud(sid)["canonical_lines"] == original and stored(sid) == entries
            passed.append("workshop_late_" + label + "_failure_never_retries_or_commits_reply")


# 見込み: 手動取消は共通の生成・合成・再生を回収できる。合格: 続きの受信を解除しても二文目・履歴・再生の復活なし。実停止操作をHTTPから送り、音声は模擬。
def check_manual_stop(passed):
    prefix = '{"action":"explain","speech":"' + SENTENCES[0]
    with streaming([prefix, SENTENCES[1] + '"}']) as (f, sid, gate, calls, offset, seen_offset):
        base, _, log, _, seen, _, _, _, _, _, _, _, _ = f
        turn = submit(base, sid, "この句を説明して")
        wait_for(lambda: row(base, turn, {"started"}))
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id":sid})
        wait_for(lambda: row(base, turn, {"cancelled"}))
        gate.set()
        assert len(calls) == 1 and player_count(log, offset) == 1
        assert syntheses(seen[seen_offset:]) == SENTENCES[:1] and not session(base, sid)["workshop_history"]
        passed.append("workshop_stream_manual_stop_reaps_without_late_playback")


def main():
    passed = []
    check_first_sentence(passed)
    check_action_last(passed)
    check_edit_tool(passed)
    check_late_failures(passed)
    check_manual_stop(passed)
    print(json.dumps({"passed":passed, "all_owned_processes_stopped":True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
