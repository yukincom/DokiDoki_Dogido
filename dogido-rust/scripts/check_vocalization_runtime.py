#!/usr/bin/env python3
"""叫声と川柳STT文脈の実HTTP試験。モデル・再生は模擬、所有プロセスは回収する。"""
from datetime import datetime, timedelta, timezone
import json

from check_dialogue import register, request, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture
from check_language_runtime import interpretation
from check_workshop_combat import SETTINGS, MOB, end, enter, returned
from check_workshop_runtime import ready, session, install
from check_workshop_edits import finish, edit, adoption
from check_workshop_provisional import classifier, intent, stable, done, threatening
from check_workshop_combat import actions, drive


def context(base):
    return request(base, "/api/v1/voice-input/context")


def scream(base, sid=None, text="うわああ！", source="voice"):
    return request(base, "/api/v1/player-input", {"text":text, "source":source, **({"session_id":sid} if sid else {})})


def calls(seen):
    return [r["body"] for r in seen if r["path"] == "/v1/chat/completions"]


def main():
    passed = []
    with fixture(enabled=False, combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder = f
        assert context(base) == {"prompt_mode":"normal", "session_id":None}
        assert not scream(base)["accepted"]
        sid = register(base, preview=False)
        send(sid)
        assert context(base) == {"prompt_mode":"normal", "session_id":sid}
        # typedの感嘆や引用・意味のある後続文を叫声として捨てない。
        for text, source in [("うわああ！", "text"), ("『うわああ』って言った", "voice"), ("うわああ、びっくりした", "voice")]:
            before = len(calls(seen))
            result = scream(base, sid, text, source)
            assert result.get("reason") != "situation_vocalization", result
            wait_for(lambda: row(base, result["turn_id"], {"completed"}))
            assert len(calls(seen)) == before + 2
            send(sid)
        passed.append("only_pure_voice_is_intercepted_typed_quote_and_sentence_still_reply")

        (folder / "player_mode").write_text("slow")
        active = submit(base, sid, "ドギド、ちょっと話そう")
        wait_for(lambda: row(base, active, {"started"}))
        (folder / "player_mode").write_text("ok")
        before = len(calls(seen))
        previous_rows = len(rows(sid))
        result = scream(base, sid)
        assert result["accepted"] and result["reason"] == "situation_vocalization" and "turn_id" not in result
        interrupted = wait_for(lambda: row(base, active, {"cancelled"}))
        assert interrupted["cancel_reason"] == "voice_vocalization", interrupted
        assert len(rows(sid)) == previous_rows
        assert not any(r["turn_id"] == active + ":reply" for r in session(base, sid)["history"])
        assert len(calls(seen)) == before
        assert session(base, sid)["history_retention"]["danger_active"]
        assert not session(base, sid)["foreground"]["combat_active"]
        passed.append("scream_cancels_unheard_reply_without_model_turn_or_fake_combat")

        enter(send, rows, sid)
        end(send, rows, sid)
        captures = []
        def model(incoming):
            if incoming["max_tokens"] != 850:
                return None
            background, current = incoming["messages"][-1]["content"].split("\n以上は背景。今回応答する最新の発話はこちら：\n")
            details, current = json.loads(background), json.loads(current)
            captures.append(details)
            return interpretation(current)
        control["structured_handler"] = model
        for i in range(4):
            send(sid)
            turn = submit(base, sid, f"きょうの音数を教えて。その{i}")
            wait_for(lambda: row(base, turn, {"completed"}))
            note = captures[-1]["situation_history"]
            assert ("近くの敵対モブを視認" in note) == (i < 3), note
            assert "うわああ" not in note
        assert "voice_situation" in log.read_text() and "player_vocalization" in log.read_text()
        passed.append("observed_situation_reaches_language_and_expires_after_three_inputs")

        # 5秒以上ずれた観測から、声の原因を推定しない。
        scream(base, sid)
        send(sid, observed_at=(datetime.now(timezone.utc)-timedelta(seconds=6)).isoformat(), visual_threats=[MOB])
        previous = {r["turn_id"] for r in rows(sid)}
        end(send, rows, sid)
        wait_for(lambda: any(r["turn_id"] not in previous and r["playback_status"] == "completed"
                            and any(a["kind"] == "aftermath" for a in r.get("combat_actions", []))
                            for r in rows(sid)))
        send(sid)
        turn = submit(base, sid, "きょうの音数を教えて。その後")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert "驚いた声を検出。原因は不明" in captures[-1]["situation_history"]
        assert "近くの敵対モブを視認" not in captures[-1]["situation_history"]
        passed.append("unrelated_old_observation_does_not_invent_scream_cause")

        other = register(base, preview=False)
        assert context(base) == {"prompt_mode":"normal", "session_id":None}
        assert scream(base)["reason"] == "select_one_session"
        assert session(base, other)["history"] == []
        request(base, "/api/v1/adapter-sessions/"+other, method="DELETE")
        request(base, "/api/v1/adapter-sessions/"+sid, method="DELETE")
        preview = register(base)
        assert scream(base, preview)["reason"] == "situation_vocalization"
        assert not session(base, preview)["history_retention"]["danger_active"]
        assert session(base, preview)["history_retention"]["remaining_player_turns"] == 3
        passed.append("ambiguous_session_rejected_and_preview_uses_unknown_without_waiting")

    with fixture(combat_settings=SETTINGS) as f:
        base, _, log, control, seen, _, _, _, send, hud, rows, stored, _ = f
        sid = ready(base, send, rows)
        assert context(base) == {"prompt_mode":"haiku_workshop", "session_id":sid}
        before_revision = snapshot(base)["revision"]
        for _ in range(3):
            context(base)
        assert snapshot(base)["revision"] == before_revision
        install(control, lambda text, prompt, n: edit(text))
        finish(base, send, sid, "上五を『さくらいろ』にして")
        before = hud(sid)
        before_dialogue = session(base, sid)["workshop_history"]
        before_calls = len(calls(seen))
        before_stored = stored(sid)
        assert scream(base, sid)["reason"] == "situation_vocalization"
        send(sid)
        after = hud(sid)
        for key in ["workshop_id", "canonical_lines", "pending_lines", "editing", "state"]:
            assert before[key] == after[key], key
        assert session(base, sid)["workshop_history"] == before_dialogue
        assert before_calls == len(calls(seen)) and stored(sid) == before_stored
        passed.append("open_workshop_stt_context_is_read_only_and_scream_preserves_pending")

        enter(send, rows, sid)
        assert context(base)["prompt_mode"] == "normal"
        before_calls = len(calls(seen))
        assert scream(base, sid)["reason"] == "situation_vocalization"
        assert len(calls(seen)) == before_calls
        end(send, rows, sid)
        returned(send, rows, sid)
        # 安全復帰の句再掲が完了すると相談を受け付ける状態に戻る。
        assert context(base)["prompt_mode"] == "haiku_workshop"
        finish(base, send, sid, "うん")
        assert context(base)["prompt_mode"] == "haiku_workshop"
        install(control, lambda text, prompt, n: adoption(text, action="reject_pending"))
        rejected = finish(base, send, sid, "その案を却下して")
        assert rejected.get("workshop_outcome") == "pending_rejected", rejected
        finish(base, send, sid, "終了でいいよ")
        assert context(base)["prompt_mode"] == "normal"
        passed.append("paused_and_closed_workshop_use_normal_stt_and_resume_restores_context")

    with fixture() as f:
        base, _, log, _, _, gate, drafting, _, send, hud, _, stored, _ = f
        gate.clear()
        try:
            sid = register(base, preview=False)
            send(sid)
            wait_for(drafting.is_set)
            assert context(base)["prompt_mode"] == "normal" and hud(sid)["state"] == "closed"
            assert scream(base, sid)["reason"] == "situation_vocalization"
            gate.set()
            wait_for(lambda: "python_worker_stopped" in log.read_text())
            assert not stored(sid) and hud(sid)["state"] == "closed"
        finally:
            gate.set()
        passed.append("preparing_poem_uses_normal_context_and_scream_cancels_unfinished_generation")

    with fixture(combat_settings=SETTINGS, low_threat_resume_delay_ms=1000) as f:
        base, _, _, control, _, _, _, _, send, hud, rows, _, _ = f
        classifier(control, lambda text: intent(text))
        sid = ready(base, send, rows)
        stable(send, rows, sid)
        assert context(base)["prompt_mode"] == "normal"
        turn = submit(base, sid, "句の続きを話そう")
        assert done(base, send, sid, turn)["combat_input_outcome"] == "provisional_resume"
        drive(lambda s: threatening(send, s), sid, lambda: any(
            r["playback_status"] == "completed" for r in actions(rows, sid, "workshop_direct_resume")))
        assert context(base)["prompt_mode"] == "haiku_workshop"
        send(sid, visual_threats=[{**MOB, "approaching":True}], combat={"combat_active_hint":True})
        assert hud(sid)["state"] == "danger" and context(base)["prompt_mode"] == "normal"
        passed.append("explicit_stable_enemy_resume_uses_workshop_context_until_new_danger")

    print(f"PASS {len(passed)} vocalization checks; all owned processes stopped")
    for case in passed:
        print(case)


if __name__ == "__main__":
    main()
