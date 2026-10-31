#!/usr/bin/env python3
"""句相談/戦闘の知識質問引継ぎ。実モデル・音声・共有8080は使わない。"""
import json
import time
from check_dialogue import register, request, row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import finish, revisions
from check_workshop_followup import meaning_ack
from check_workshop_revision import wire
from check_workshop_combat import SETTINGS, MOB, enter, end, returned


def enqueue(base, sid, text):
    # 後続の解析を待たず連続配送し、同時入力でも質問が失われないことを試す。
    response=request(base, "/api/v1/player-input", {"session_id":sid,"text":text,"source":"voice"})
    assert response.get("reason")=="reply_input_routing" and response["accepted"], response
    return response["turn_id"]


def main():
    passed = []
    with fixture(combat_settings=SETTINGS, bert_labels={
        "この句の相談は終了でいいよ": "senryu_end",
    }) as f:
        base, _, log, control, seen, _, _, _, send, hud, rows, stored, folder = f
        def choose(text, prompt, n):
            if text == "なるほど":
                assert "会話段階: meaning_explained" in prompt
                return meaning_ack(text)
            if text == "うん":
                return step(text, "respond", "うん、この句の話を続けよか。")
            return step(text)
        calls = install(control, choose)
        sid = ready(base, send, rows)
        finish(base, send, sid, "この句の意味を教えて")
        before = session(base, sid)
        assert before["workshop_followup"] == "meaning_explained"
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
        passed.append("knowledge_detours_preserve_poem_history_followup_and_ownership_without_model")

        (folder / "player_mode").write_text("fail")
        failed = finish(base, send, sid, "ソネットとは？")
        assert failed["playback_status"] == "failed" and failed["workshop_action"] == "knowledge", failed
        after_failure = session(base, sid)
        assert after_failure["workshop_followup"] == before["workshop_followup"]
        assert after_failure["workshop_history"] == before["workshop_history"]
        assert not any(h["turn_id"] == failed["turn_id"] + ":reply" for h in after_failure["history"])
        assert len(calls) == 1
        (folder / "player_mode").write_text("ok")
        # 納得への返事はモデルが選ぶ。相槌だけでは終了確認へ進まない。
        acknowledged = finish(base, send, sid, "なるほど")
        assert acknowledged["workshop_action"] == "acknowledge_meaning" and len(acknowledged["llm_reports"]) == 1
        assert session(base, sid)["workshop_followup"] == "discussion"
        assert finish(base, send, sid, "うん")["workshop_action"] == "respond"
        assert len(calls) == 3 and hud(sid)["state"] == "open" and stored(sid) == original
        assert not revisions(folder, sid)
        closing = finish(base, send, sid, "この句の相談は終了でいいよ")
        assert closing["workshop_action"] == "close_workshop" and not closing["llm_reports"], closing
        assert len(calls) == 3 and hud(sid)["state"] == "closed"
        passed.append("failed_knowledge_preserves_meaning_stage_and_model_ack_keeps_workshop_open")
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

        sid = ready(base, send, rows)
        wait_for(lambda: stored(sid))
        # プレイヤーの明示編集は即保存されるため、採用待ちはAI修正案で作る。
        wire(control, stored, sid)
        proposed = finish(base, send, sid, "上五のさくらのはを別の表現に直して")
        assert proposed["workshop_outcome"] == "revision_proposed", proposed
        before_hud = hud(sid)
        assert before_hud["canonical_lines"] == LINES and before_hud["pending_lines"] == ["さくらいろ", *LINES[1:]]
        before_history = session(base, sid)["workshop_history"]
        for text in ["枕詞って何？", "川柳の決まりを教えて"]:
            assert finish(base, send, sid, text)["workshop_action"] == "knowledge"
        after_hud = hud(sid)
        for key in ["canonical_lines", "pending_lines", "editing", "selected_line"]:
            assert after_hud[key] == before_hud[key], key
        assert session(base, sid)["workshop_history"] == before_history
        assert not revisions(folder, sid)
        verse_calls = install(control, lambda text, prompt, n: step(text))
        for text in ["この川柳の意味は？", "さくらいろって何？"]:
            r = finish(base, send, sid, text)
            assert r["workshop_action"] != "knowledge", r
        passed.append("pending_edit_is_unchanged_and_current_verse_questions_stay_in_workshop")

        enter(send, rows, sid)
        a = enqueue(base, sid, "枕詞って何？")
        b = enqueue(base, sid, "ソネットの形式は？")
        for turn in [a, b]: wait_for(lambda: row(base, turn, {"waiting_for_warning"}))
        dup = request(base, "/api/v1/player-input", {"session_id":sid,"text":"枕詞って何？","source":"voice"})
        assert dup["deduplicated"] and dup["turn_id"] == a, dup
        # 戦闘中に通常分類で受けた質問は、川柳へ戻ると受付世代が変わる。
        # 同じ入力を川柳として読み替えず、元の分類結果のまま取消する。
        classified = {turn: row(base, turn, {"waiting_for_warning"})["request_classification"] for turn in [a, b]}
        model_count = len(verse_calls)
        end(send, rows, sid)
        returned(send, rows, sid)
        for turn in [a, b]:
            r = wait_for(lambda: row(base, turn, {"cancelled"}))
            assert r["resolution"] == "superseded_classifier_mode" and r["classifier_mode"] == "normal", r
            assert r["request_classification"] == classified[turn] and not r.get("llm_reports", []), r
            assert not any(h["turn_id"] == turn + ":reply" for h in session(base, sid)["history"])
        # 戦闘後の安堵leafは別用途。待機質問から相談モデルを呼ばない境界を確認する。
        assert len(verse_calls) == model_count
        assert session(base, sid)["workshop_followup"] == "discussion"
        assert hud(sid)["pending_lines"] == before_hud["pending_lines"]
        assert len([r for r in rows(sid) if r["turn_id"] in [a,b]]) == 2
        passed.append("combat_questions_deduplicate_and_cancel_at_senryu_boundary_while_preserving_pending")

    with fixture(enabled=False, combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, hud, rows, stored, folder = f
        def warning_started(sid):
            (folder / "player_mode").write_text("slow")
            send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
            return wait_for(lambda: next((r for r in rows(sid)
                if r["playback_status"] == "started" and r.get("combat_actions")), None))

        # workshopのないsessionなら警告前後で通常分類を維持し、FIFO配送できる。
        sid = register(base, preview=False, body=True); send(sid); warning_started(sid)
        first = enqueue(base, sid, "枕詞って何？")
        second = enqueue(base, sid, "ソネットの形式は？")
        for turn in [first, second]:
            waiting = wait_for(lambda: row(base, turn, {"waiting_for_warning"}))
            assert waiting["classifier_mode"] == "normal", waiting
        dup = request(base, "/api/v1/player-input", {"session_id":sid,"text":"枕詞って何？","source":"voice"})
        assert dup["deduplicated"] and dup["turn_id"] == first, dup
        (folder / "player_mode").write_text("ok")
        for turn in [first, second]:
            r = wait_for(lambda: row(base, turn, {"completed"}))
            assert r["source"] == "voice" and r["knowledge_status"] == "found" and r["reference_ids"], r
            assert not r["llm_reports"], r
            assert sum(h["turn_id"] == turn + ":reply" for h in session(base, sid)["history"]) == 1
        assert row(base, first, {"completed"})["completed_at"] < row(base, second, {"completed"})["completed_at"]
        assert len([r for r in rows(sid) if r["turn_id"] in [first, second]]) == 2
        passed.append("normal_mode_questions_fifo_deduplicate_reuse_ids_and_commit_once_after_warning")
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

        sid = register(base, preview=False, body=True); send(sid); warning_started(sid)
        questions = ["枕詞って何？", "ソネットの形式は？", "川柳の決まりを教えて", "ソネットとは？",
                     "川柳とは？", "まくらことばって何？", "Minecraftで石のIDは？",
                     "ダイヤモンドの剣のIDは？", "ダイヤモンドの剣の耐久値は？"]
        turns = [enqueue(base, sid, text) for text in questions]
        for turn in turns: wait_for(lambda: row(base, turn, {"waiting_for_warning"}))
        rejected = request(base, "/api/v1/player-input", {"session_id":sid,"text":"枕詞とは？","source":"voice"})
        assert rejected == {"accepted":False,"reason":"reply_queue_full"}, rejected
        assert not session(base, sid)["history"]
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id":sid})
        assert all(row(base, t, {"cancelled"}) for t in turns)
        (folder / "player_mode").write_text("ok")
        aftermath = end(send, rows, sid)
        for _ in range(5): send(sid); time.sleep(.1)
        assert all(row(base, t, {"cancelled"}) for t in turns)
        # 取消した質問は履歴に入れず、実再生済みの安堵だけを記録する。
        assert session(base, sid)["history"] == [{"turn_id": aftermath["turn_id"] + ":reply",
            "role": "assistant", "text": aftermath["text"]}]
        passed.append("bounded_queue_cancels_questions_and_keeps_only_completed_aftermath_history")
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

        sid = register(base, preview=False, body=True); send(sid)
        warning = warning_started(sid)
        spoken_before = [r for r in session(base, sid)["history"] if r["role"] == "assistant"]
        first = enqueue(base, sid, "枕詞って何？")
        second = enqueue(base, sid, "ソネットの形式は？")
        for turn in [first,second]: wait_for(lambda: row(base, turn, {"waiting_for_warning"}))
        (folder / "player_mode").write_text("fail")
        # 戦闘継続中も、先行警告が終端すれば失敗した回答の次へ進む。
        for turn in [first,second]: wait_for(lambda:row(base,turn,{"failed"}))
        history = session(base, sid)["history"]
        terminal_warning = row(base, warning["turn_id"], {"completed", "failed"})
        assert terminal_warning
        expected_spoken = list(spoken_before)
        if terminal_warning["playback_status"] == "completed":
            expected_spoken.append({"turn_id":warning["turn_id"] + ":reply",
                "role":"assistant", "text":terminal_warning["text"]})
        assert [r for r in history if r["role"] == "assistant"] == expected_spoken
        assert session(base, sid)["foreground"]["combat_active"]
        assert not any(r["turn_id"] in {first + ":reply", second + ":reply"} for r in history)
        (folder / "player_mode").write_text("ok")
        passed.append("failed_answer_does_not_block_next_question_or_commit_assistant_history")
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

        for change in ["death", "dimension", "close"]:
            other = register(base, preview=False, body=True); send(other); warning_started(other)
            turn = enqueue(base, other, "枕詞って何？")
            wait_for(lambda: row(base, turn, {"waiting_for_warning"}))
            if change == "death":
                send(other, event={"name":"player_died","source_kind":"system","priority_hint":"background","certainty":"high"},
                     player={"name":"試験","dimension":"minecraft:overworld","health":0})
            elif change == "dimension":
                send(other, player={"name":"試験","dimension":"minecraft:the_nether"})
            else:
                request(base,"/api/v1/adapter-sessions/"+other,method="DELETE")
            wait_for(lambda: row(base,turn,{"cancelled"}))
            assert not row(base, turn, {"cancelled"}).get("llm_reports", [])
            if change != "close":
                request(base, "/api/v1/adapter-sessions/" + other, method="DELETE")
        passed.append("death_dimension_and_session_close_cancel_classified_questions_waiting_for_warning")

    print(json.dumps({"passed":len(passed),"cases":passed,"all_owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
