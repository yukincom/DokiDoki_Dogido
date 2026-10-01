#!/usr/bin/env python3
"""句相談/戦闘の知識質問引継ぎ。実モデル・音声・共有8080は使わない。"""
import json
import time
from check_dialogue import register, request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, finish, revisions
from check_workshop_combat import SETTINGS, enter, end, returned, drive


def enqueue(base, sid, text):
    # 後続の解析を待たず連続配送し、同時入力でも質問が失われないことを試す。
    response=request(base, "/api/v1/player-input", {"session_id":sid,"text":text,"source":"voice"})
    assert response.get("reason")=="knowledge_input_routing" and response["accepted"], response
    return response["turn_id"]


def main():
    passed = []
    with fixture(combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, hud, rows, stored, folder = f
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        finish(base, send, sid, "この句の意味を教えて")
        before = session(base, sid)
        original = stored(sid)
        n = len(seen)
        for text in ["枕詞って何？", "川柳の決まりを教えて", "ソネットの形式は？"]:
            result = finish(base, send, sid, text)
            assert result.get("workshop_action") == "knowledge", (result, log.read_text())
            assert result["knowledge_status"] == "found" and result["reference_ids"]
            assert result["llm_reports"] == []
            assert session(base, sid)["workshop_followup"] == before["workshop_followup"]
            assert session(base, sid)["workshop_history"] == before["workshop_history"]
            assert session(base, sid)["foreground"]["route"] == "haiku_workshop"
        assert not any(r["path"] == "/v1/chat/completions" for r in seen[n:])
        assert hud(sid)["canonical_lines"] == LINES and stored(sid) == original
        assert finish(base, send, sid, "なるほど")["workshop_action"] == "acknowledge_meaning"
        passed.append("knowledge_detours_preserve_poem_history_followup_and_ownership_without_model")

        finish(base, send, sid, "枕詞って何？")
        assert session(base, sid)["workshop_followup"] == "close_confirmation"
        (folder / "player_mode").write_text("fail")
        assert finish(base, send, sid, "ソネットとは？")["playback_status"] == "failed"
        assert session(base, sid)["workshop_followup"] == "close_confirmation"
        (folder / "player_mode").write_text("ok")
        assert finish(base, send, sid, "うん")["workshop_action"] == "confirm_close"
        passed.append("knowledge_and_failed_audio_do_not_consume_close_confirmation")

        sid = ready(base, send, rows)
        install(control, lambda text, prompt, n: edit(text))
        finish(base, send, sid, "上五を『さくらいろ』にして")
        before_hud = hud(sid)
        before_history = session(base, sid)["workshop_history"]
        for text in ["枕詞って何？", "川柳の決まりを教えて"]:
            assert finish(base, send, sid, text)["workshop_action"] == "knowledge"
        after_hud = hud(sid)
        for key in ["canonical_lines", "pending_lines", "editing", "selected_line"]:
            assert after_hud[key] == before_hud[key], key
        assert session(base, sid)["workshop_history"] == before_history
        assert not revisions(folder, sid)
        install(control, lambda text, prompt, n: step(text))
        for text in ["この川柳の意味は？", "さくらいろって何？"]:
            r = finish(base, send, sid, text)
            assert r["workshop_action"] != "knowledge", r
        passed.append("pending_edit_is_unchanged_and_current_verse_questions_stay_in_workshop")

        enter(send, rows, sid)
        a = enqueue(base, sid, "枕詞って何？")
        b = enqueue(base, sid, "ソネットの形式は？")
        for turn in [a, b]: wait_for(lambda: row(base, turn, {"waiting_for_safety"}))
        dup = request(base, "/api/v1/player-input", {"session_id":sid,"text":"枕詞って何？","source":"text"})
        assert dup["deduplicated"] and dup["turn_id"] == a, dup
        end(send, rows, sid)
        returned(send, rows, sid)
        for turn in [a, b]:
            r = drive(send, sid, lambda: row(base, turn, {"completed"}))
            assert r["source"] == "voice" and r["workshop_action"] == "knowledge", r
        assert row(base, a, {"completed"})["completed_at"] < row(base, b, {"completed"})["completed_at"]
        assert session(base, sid)["workshop_followup"] == "combat_resume_confirmation"
        assert hud(sid)["pending_lines"] == before_hud["pending_lines"]
        assert len([r for r in rows(sid) if r["turn_id"] in [a,b]]) == 2
        passed.append("combat_questions_fifo_deduplicate_reuse_ids_and_preserve_resume_confirmation")

    with fixture(enabled=False, combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, hud, rows, stored, folder = f
        sid = register(base, preview=False); send(sid); enter(send, rows, sid)
        questions = ["枕詞って何？", "ソネットの形式は？", "川柳の決まりを教えて", "ソネットとは？",
                     "一の読みを教えて", "漢字の3は何年生で習うの？", "国語で幻の枕詞って何？",
                     "ダイヤモンドの剣のIDは？", "ダイヤモンドの剣の耐久値は？"]
        turns = [enqueue(base, sid, text) for text in questions]
        for turn in turns: wait_for(lambda: row(base, turn, {"waiting_for_safety"}))
        rejected = request(base, "/api/v1/player-input", {"session_id":sid,"text":"枕詞とは？","source":"voice"})
        assert rejected == {"accepted":False,"reason":"knowledge_queue_full"}, rejected
        assert not session(base, sid)["history"]
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id":sid})
        assert all(row(base, t, {"cancelled"}) for t in turns)
        end(send, rows, sid)
        for _ in range(5): send(sid); time.sleep(.1)
        assert not session(base, sid)["history"]
        passed.append("bounded_queue_rejects_overflow_and_manual_stop_clears_without_unheard_history")

        enter(send, rows, sid)
        first = enqueue(base, sid, "枕詞って何？")
        second = enqueue(base, sid, "ソネットの形式は？")
        for turn in [first,second]: wait_for(lambda: row(base, turn, {"waiting_for_safety"}))
        (folder / "player_mode").write_text("fail")
        # 安堵音声も失敗させたまま、安全観測で待ち列が進むことを確認。
        send(sid, event={"name":"combat_ended","source_kind":"system","priority_hint":"background","certainty":"high"})
        for turn in [first,second]: drive(send,sid,lambda:row(base,turn,{"failed"}))
        assert not any(r["role"]=="assistant" for r in session(base,sid)["history"])
        (folder / "player_mode").write_text("ok")
        passed.append("failed_answer_does_not_block_next_question_or_commit_assistant_history")

        for change in ["death", "dimension", "close"]:
            other = register(base, preview=False); send(other); enter(send, rows, other)
            turn = enqueue(base, other, "枕詞って何？")
            if change == "death":
                send(other, event={"name":"player_died","source_kind":"system","priority_hint":"background","certainty":"high"},
                     player={"name":"試験","dimension":"minecraft:overworld","health":0})
            elif change == "dimension":
                send(other, player={"name":"試験","dimension":"minecraft:the_nether"})
            else:
                request(base,"/api/v1/adapter-sessions/"+other,method="DELETE")
            wait_for(lambda: row(base,turn,{"cancelled"}))
            time.sleep(.4)  # 遅いparser結果が返っても保留を復活させない。
            assert row(base,turn,{"cancelled"})
        passed.append("death_dimension_and_session_close_cancel_even_before_classification")

    print(json.dumps({"passed":len(passed),"cases":passed,"all_owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
