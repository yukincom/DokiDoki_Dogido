#!/usr/bin/env python3
"""称賛と返答予定を、戦闘で譲った音声から分けて保存する模擬HTTP試験。"""
import json
import fcntl
import threading

from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_memory_runtime import records
from check_workshop_edits import finish, revisions
from check_workshop_revision import wire
from check_workshop_records import finished
from check_workshop_runtime import install, ready, step, session
from check_workshop_combat import SETTINGS, MOB, end, returned


PRAISE = "余韻が心に残る"
INPUT = PRAISE + "。この余韻の話を続けよう"
END_INPUT = PRAISE + "。今日はここまでにしよう"
VALID = {"kind": "praise", "evidence": PRAISE}


def feedback_reply(text, feedback=VALID, speech="気に入ってもらえてうれしいわ。"):
    result = step(text, "respond")
    result["speech"] = speech
    if feedback is not None:
        result["feedback"] = feedback
    return result


def main():
    passed = []
    story_record = None
    with fixture() as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        # 分類済み終了にはモデルを呼ばず、未取得のfeedbackを捏造しない。
        sid = ready(base, send, rows)
        before = stored(sid)
        critiques, lessons = records(folder, "critiques"), records(folder, "lessons")
        calls = install(control, lambda text, prompt, n: feedback_reply(text))
        result = finish(base, send, sid, END_INPUT)
        assert result["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", result
        assert result["text"] == "ほな、この句はここまでにしよか。", result
        assert not calls and not result["llm_reports"], result
        assert stored(sid) == before and not revisions(folder, sid)
        assert records(folder, "critiques") == critiques and records(folder, "lessons") == lessons
        assert not result.get("workshop_feedback_outcome"), result
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
        passed.append("bert_end_without_generation_does_not_invent_unreceived_feedback")

        sid = ready(base, send, rows)
        before = stored(sid)
        critiques, lessons = records(folder, "critiques"), records(folder, "lessons")
        for text, feedback in [(PRAISE, VALID), ("いい句ではない", None), ("『いい句だね』は友人の言葉", None)]:
            calls = install(control, lambda text, prompt, n: {"action": "respond", "speech": "その受け止め方も聞かせてな。", "feedback": feedback})
            result = finish(base, send, sid, text)
            assert result["workshop_action"] == "respond" and len(calls) == 1, result
            assert hud(sid)["state"] == "open" and hud(sid)["canonical_lines"] == LINES
            after = records(folder, "critiques")
            assert len(after) == len(critiques) + (1 if feedback else 0), result
            if feedback:
                assert after[-1]["kind"] == "praise" and after[-1]["player_text"] == text
                assert after[-1]["current_lines_at_input"] and after[-1]["workshop_id"]
            critiques = after
            assert records(folder, "lessons") == lessons
            assert stored(sid) == before and not revisions(folder, sid)
        passed.append("praise_is_saved_without_closing_or_changing_the_verse")

        # 称賛の解釈は任意のevidenceや原文一致で再審査しない。
        for feedback in [None, {"kind": "other"}, {"kind": "praise", "evidence": 7},
                         {"kind": "praise", "evidence": "原文にない感想"}, {"kind":"praise"}]:
            calls = install(control, lambda text, prompt, n: {"action": "respond", "speech": "気に入ってもらえてうれしいわ。", "feedback": feedback})
            result = finish(base, send, sid, INPUT)
            assert result["workshop_action"] == "respond" and len(calls) == 1 and len(result["llm_reports"]) == 1, result
            assert result["text"] == "気に入ってもらえてうれしいわ。", result
            expected = bool(feedback and feedback.get("kind") == "praise")
            after = records(folder, "critiques")
            assert len(after) == len(critiques) + int(expected), result
            critiques = after
            assert records(folder, "lessons") == lessons
            assert hud(sid)["state"] == "open" and hud(sid)["canonical_lines"] == LINES
            assert stored(sid) == before and not revisions(folder, sid)
        passed.append("interpreted_praise_survives_missing_or_nonverbatim_evidence_without_retry")

        # 会話の補助confidenceが不正でも、モデルが解釈した称賛は一件だけ残る。
        def unused_metadata(text, prompt, n):
            result = feedback_reply(text, VALID, "また一緒に詠もな。")
            result["confidence"] = "invalid"
            return result
        calls = install(control, unused_metadata)
        result = finish(base, send, sid, INPUT)
        assert result["workshop_action"] == "respond" and len(calls) == 1, result
        assert result["text"] == "また一緒に詠もな。" and hud(sid)["state"] == "open", result
        assert len(records(folder, "critiques")) == len(critiques) + 1
        assert records(folder, "critiques")[-1]["player_text"] == INPUT
        passed.append("praise_and_model_speech_survive_unused_metadata_without_retry")
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

        sid = ready(base, send, rows)
        wire(control, stored, sid)
        finish(base, send, sid, "上五のさくらのはを別の表現に直して")
        pending = hud(sid)["pending_lines"]
        assert pending
        critiques, lessons = records(folder, "critiques"), records(folder, "lessons")
        calls = install(control, lambda text, prompt, n: {**feedback_reply(text), "action":"ask", "speech":"この案を採用するか、破棄するか教えてな。"})
        send(sid)
        turn = submit(base, sid, END_INPUT)
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "ask" and hud(sid)["state"] == "open", result
        assert result["text"] == "この案を採用するか、破棄するか教えてな。" and len(calls) == 1, result
        assert hud(sid)["pending_lines"] == pending and not revisions(folder, sid)
        assert len(records(folder, "critiques")) == len(critiques) + 1
        assert records(folder, "lessons") == lessons
        assert records(folder, "critiques")[-1]["pending_at_input"]["lines"]
        passed.append("bert_end_asks_pending_adoption_and_saves_received_praise_once")
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

    # モデルの返事を待っていても戦闘音声はすぐ通し、返答が届いたら物語を補完する。
    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        before = stored(sid)
        critiques, lessons = records(folder, "critiques"), records(folder, "lessons")
        entered, release = threading.Event(), threading.Event()
        intended = "気に入ってもらえてうれしいわ。" + "この余韻も覚えとこな。" * 110
        def delayed(text, prompt, n):
            if n == 1:
                entered.set()
                assert release.wait(timeout=15)
                return feedback_reply(text, speech=intended)
            assert "そのときの出来事（返答は未発話）" in prompt, prompt
            assert "言葉を飲み込んで、戦いに挑んだ。" in prompt, prompt
            assert intended in prompt, prompt
            assert "会話段階: discussion" in prompt, prompt
            return {"action": "respond", "speech": "さっきは返事を飲み込んでもうたな。気に入ってもらえてうれしいわ。"}
        calls = install(control, delayed)
        try:
            turn = submit(base, sid, INPUT)
            assert submit(base, sid, INPUT) == turn  # 同じ受付の再送
            wait_for(entered.is_set)
            send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
            wait_for(lambda: row(base, turn, {"cancelled"}))
            warning = wait_for(lambda: next((r for r in rows(sid) if r.get("combat_actions") and r["playback_status"] == "completed"), None), timeout=5)
            assert not release.is_set() and warning["turn_id"] != turn
            assert records(folder, "critiques") == critiques
            release.set()
            recorded = finished(folder, sid, turn)
            result = recorded["result"]
            assert result["playback_status"] == "cancelled" and result["reply_delivery"] == "unspoken_combat", recorded
            assert result["unspoken_text"] == intended and result["workshop_feedback_outcome"] == "saved", recorded
            assert INPUT in result["narrative"] and intended in result["narrative"], recorded
            assert "言葉を飲み込んで、戦いに挑んだ。" in result["narrative"], recorded
            history = session(base, sid)["workshop_history"]
            assert len(history) == 1 and history[0]["unspoken_text"] == intended and history[0]["dogido_text"] == "", history
            assert len(records(folder, "critiques")) == len(critiques) + 1
            assert records(folder, "lessons") == lessons and stored(sid) == before and not revisions(folder, sid)
            assert len(calls) == 1 and hud(sid)["state"] == "danger"
            assert not any(r["role"] == "assistant" and r["text"] == intended for r in session(base, sid)["history"])
            story_record = {"event_kind": recorded["event_kind"], "reply_delivery": result["reply_delivery"], "narrative": result["narrative"][:100]}
            end(send, rows, sid)
            returned(send, rows, sid)
            common = session(base, sid)["history"]
            assert any(intended in r["text"] and r["role"] == "event" for r in common), common
            response = finish(base, send, sid, "うん、余韻の話を続けよう")
            assert response["workshop_action"] == "respond" and len(calls) == 2, response
            assert len(records(folder, "critiques")) == len(critiques) + 1
        finally:
            release.set()
        passed.append("combat_plays_before_model_returns_and_full_unspoken_story_reaches_next_consultation")

    # 音声再生中も、既に受け取った称賛と返答本文は同じturnへ一度だけ残す。
    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        install(control, lambda text, prompt, n: {"action": "respond", "speech": "気に入ってもらえてうれしいわ。", "feedback": VALID})
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, PRAISE)
        wait_for(lambda: row(base, turn, {"started"}))
        assert len(records(folder, "critiques")) == 1
        (folder / "player_mode").write_text("ok")
        send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        record = finished(folder, sid, turn)
        assert record["result"]["unspoken_text"] == "気に入ってもらえてうれしいわ。", record
        assert len(records(folder, "critiques")) == 1 and not records(folder, "lessons")
        assert not revisions(folder, sid) and hud(sid)["canonical_lines"] == LINES
        passed.append("combat_preempts_audio_but_keeps_the_praise_and_planned_reply")

    # 称賛の保存がディスクロックで待たされても、戦闘の警告を先に再生する。
    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        planned = "気に入ってもらえてうれしいわ。"
        install(control, lambda text, prompt, n: {"action": "respond", "speech": planned, "feedback": VALID})
        path = folder / "memory/long_term/haiku_critiques.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            try:
                turn = submit(base, sid, PRAISE)
                wait_for(lambda: "haiku_feedback_save_started" in log.read_text())
                send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
                wait_for(lambda: next((r for r in rows(sid) if r.get("combat_actions") and r["playback_status"] == "completed"), None), timeout=5)
                assert not records(folder, "critiques")
            finally:
                fcntl.flock(locked, fcntl.LOCK_UN)
        record = finished(folder, sid, turn)
        assert record["result"]["unspoken_text"] == planned, record
        assert len(records(folder, "critiques")) == 1
        assert hud(sid)["state"] == "danger" and not revisions(folder, sid)
        passed.append("feedback_disk_wait_does_not_delay_combat_audio")

    # 会話出力の契約補完は最大2回。編集案の生成前に止まり、戦闘前の返答予定は保持する。
    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        entered, release = threading.Event(), threading.Event()
        planned = "気に入ってもらえてうれしいわ。もう少しこの余韻を楽しもな。"
        def invalid_contract_retry(text, prompt, n):
            if n == 1:
                result = feedback_reply(text, speech=planned)
                result["action"] = "propose_revision"
                result["purpose"] = "improve_wording"
                result["confidence"] = "invalid"
                return result
            entered.set()
            release.wait(timeout=15)
            return "not a JSON object"
        calls = install(control, invalid_contract_retry)
        try:
            turn = submit(base, sid, INPUT)
            wait_for(entered.is_set)
            send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
            wait_for(lambda: row(base, turn, {"cancelled"}))
            release.set()
            record = finished(folder, sid, turn)
            assert record["result"]["unspoken_text"] == planned, record
            assert record["result"]["workshop_feedback_outcome"] == "saved", record
            assert len(calls) == 2 and len(records(folder, "critiques")) == 1
            editor_calls = [r for r in seen if r["path"] == "/v1/chat/completions" and r["body"].get("temperature") == .30]
            assert not editor_calls, editor_calls
            assert hud(sid)["state"] == "danger" and not revisions(folder, sid)
        finally:
            release.set()
        passed.append("broken_contract_retry_after_combat_keeps_planned_reply_without_editor_generation")

    # 接続を閉じた後の遅い返事は、新しい接続の履歴・句へ入れない。
    with fixture(combat_settings=SETTINGS) as context:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
        sid = ready(base, send, rows)
        entered, release = threading.Event(), threading.Event()
        def closing_connection(text, prompt, n):
            entered.set()
            release.wait(timeout=15)
            return feedback_reply(text, speech="気に入ってもらえてうれしいわ。")
        install(control, closing_connection)
        try:
            turn = submit(base, sid, INPUT)
            wait_for(entered.is_set)
            send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            new_sid = ready(base, send, rows)
            release.set()
            finished(folder, sid, turn)
            assert not session(base, new_sid)["workshop_history"]
            assert hud(new_sid)["state"] == "open" and not records(folder, "critiques")
        finally:
            release.set()
        passed.append("closed_connection_does_not_transfer_a_late_reply_to_new_workshop")

    for mode in ["disabled", "save_failed"]:
        with fixture(memory_enabled=mode != "disabled") as context:
            base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = context
            sid = ready(base, send, rows)
            if mode == "save_failed":
                (folder / "memory/long_term/haiku_critiques.jsonl").mkdir(parents=True)
            install(control, lambda text, prompt, n: feedback_reply(text))
            result = finish(base, send, sid, INPUT)
            assert result["workshop_action"] == "respond" and hud(sid)["state"] == "open", result
            assert result.get("workshop_feedback_outcome") == ("save_failed" if mode == "save_failed" else None), result
            assert not records(folder, "critiques") and not records(folder, "lessons")
            assert not revisions(folder, sid)
            passed.append(mode + "_does_not_prevent_conversation")
    print(json.dumps({"passed": passed, "count": len(passed), "story_record": story_record, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
