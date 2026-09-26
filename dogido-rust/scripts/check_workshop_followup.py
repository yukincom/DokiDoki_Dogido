#!/usr/bin/env python3
"""説明→納得→終了確認の状態・実再生境界を模擬HTTPで検査する。"""
import json
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, finish, revisions


def followup(text, action):
    return {"action": action, "purpose": {"acknowledge_meaning": "understand_meaning",
        "confirm_close": "finish_workshop", "continue_workshop": "continue_discussion"}[action],
        "confidence": .95, "evidence": text, "speech": "", "checks": []}


def main():
    passed = []
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        original = stored(sid)
        finish(base, send, sid, "この句の意味を教えて")
        assert session(base, sid)["workshop_followup"] == "meaning_explained"
        r = finish(base, send, sid, "ああ、なるほどね！")
        assert r["workshop_action"] == "acknowledge_meaning" and r["llm_reports"] == [], r
        assert session(base, sid)["workshop_followup"] == "close_confirmation"
        r = finish(base, send, sid, "まだ続けたい")
        assert r["workshop_action"] == "continue_workshop" and len(calls) == 1, r
        assert session(base, sid)["workshop_followup"] == "discussion" and hud(sid)["state"] == "open"
        passed.append("fixed_ack_and_continue_without_model")
        finish(base, send, sid, "もう一度意味を教えて")
        finish(base, send, sid, "そういうことか")
        r = finish(base, send, sid, "うん")
        assert r["workshop_action"] == "confirm_close" and len(calls) == 2, r
        assert hud(sid)["state"] == "closed" and stored(sid) == original and not revisions(folder, sid)
        passed.append("completed_confirmation_yes_closes_without_saving_or_model")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def choose(text, prompt, n):
            if text == "やっと言葉のつながりが腑に落ちたよ":
                assert "会話段階: meaning_explained" in prompt
                return followup(text, "acknowledge_meaning")
            if text == "うん、その区切りで大丈夫だよ":
                assert "会話段階: close_confirmation" in prompt
                return followup(text, "confirm_close")
            return step(text)
        calls = install(control, choose)
        sid = ready(base, send, rows)
        finish(base, send, sid, "この句の意味を教えて")
        r = finish(base, send, sid, "やっと言葉のつながりが腑に落ちたよ")
        assert r["workshop_action"] == "acknowledge_meaning" and len(r["llm_reports"]) == 1, r
        r = finish(base, send, sid, "うん、その区切りで大丈夫だよ")
        assert r["workshop_action"] == "confirm_close" and len(r["llm_reports"]) == 1, r
        assert hud(sid)["state"] == "closed" and len(calls) == 3
        passed.append("natural_ack_and_confirmation_share_existing_single_step")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        for mode, target in [("fail", "failed"), ("slow", "cancelled")]:
            (folder / "player_mode").write_text(mode)
            send(sid)
            t = submit(base, sid, "句を説明して")
            if mode == "slow":
                wait_for(lambda: row(base, t, {"started"}))
                assert session(base, sid)["workshop_followup"] == "discussion"
                request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
            wait_for(lambda: row(base, t, {target}))
            assert session(base, sid)["workshop_followup"] == "discussion"
            (folder / "player_mode").write_text("ok")
            r = finish(base, send, sid, "なるほど")
            assert r["workshop_action"] == "explain" and r["llm_reports"], r
        passed.append("failed_or_interrupted_explanation_does_not_arm_ack")

        for mode, target in [("fail", "failed"), ("slow", "cancelled")]:
            finish(base, send, sid, "句を説明して")
            (folder / "player_mode").write_text(mode)
            send(sid)
            t = submit(base, sid, "なるほど")
            if mode == "slow":
                wait_for(lambda: row(base, t, {"started"}))
                assert session(base, sid)["workshop_followup"] == "discussion"
                request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
            wait_for(lambda: row(base, t, {target}))
            (folder / "player_mode").write_text("ok")
            r = finish(base, send, sid, "うん")
            assert r["workshop_action"] != "confirm_close" and hud(sid)["state"] == "open", r
        passed.append("unheard_confirmation_cannot_close_on_yes")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        for text in ["『いいよ』と聞いた", "いいよとは言ってない", "いいよ？", "いいよと言ったらどうなる？", "いいよ、でも上五を直したい"]:
            install(control, lambda text, prompt, n: step(text))
            finish(base, send, sid, "句を説明して")
            finish(base, send, sid, "なるほど")
            install(control, lambda text, prompt, n: followup("いいよ", "confirm_close"))
            r = finish(base, send, sid, text)
            assert r["workshop_action"] == "fallback" and hud(sid)["state"] == "open", (text, r)
        passed.append("quoted_negated_conditional_question_or_continuation_does_not_close")
        # A new question consumes the old confirmation; later assent has no old target.
        install(control, lambda text, prompt, n: step(text) if "説明" in text else step(text, "respond", "そうやな。"))
        finish(base, send, sid, "句を説明して")
        finish(base, send, sid, "なるほど")
        finish(base, send, sid, "もう少し斧のことを話したい")
        r = finish(base, send, sid, "うん")
        assert r["workshop_action"] == "respond" and hud(sid)["state"] == "open", r
        passed.append("new_discussion_consumes_old_close_confirmation")
        finish(base, send, sid, "句を説明して")
        finish(base, send, sid, "なるほど")
        request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
        assert session(base, sid)["workshop_followup"] == "discussion"
        r = finish(base, send, sid, "うん")
        assert r["workshop_action"] == "respond" and hud(sid)["state"] == "open", r
        passed.append("manual_stop_clears_completed_confirmation_target")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        install(control, lambda text, prompt, n: edit(text))
        sid = ready(base, send, rows)
        finish(base, send, sid, "上五を『さくらいろ』にして")
        install(control, lambda text, prompt, n: step(text))
        finish(base, send, sid, "案の意味を説明して")
        assert session(base, sid)["workshop_followup"] == "discussion"
        install(control, lambda text, prompt, n: followup(text, "confirm_close"))
        r = finish(base, send, sid, "それでいいよ")
        assert r["workshop_action"] == "fallback" and hud(sid)["state"] == "open", r
        assert hud(sid)["canonical_lines"] == LINES and hud(sid)["pending_lines"]
        assert not revisions(folder, sid)
        passed.append("pending_not_closed_discarded_or_adopted_by_followup")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        finish(base, send, sid, "句を説明して")
        finish(base, send, sid, "なるほど")
        send(sid, event={"name": "hostile_audio_detected", "source_kind": "auditory", "priority_hint": "background", "certainty": "high"},
             auditory_threats=[{"label": "zombie", "source_id": "sound-z", "spoken_name_allowed": True}])
        assert hud(sid)["state"] == "danger" and session(base, sid)["workshop_followup"] == "discussion"
        assert hud(sid)["canonical_lines"] == LINES
        passed.append("combat_clears_short_reply_target_but_keeps_poem")

    print(json.dumps({"passed": len(passed), "cases": passed, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
