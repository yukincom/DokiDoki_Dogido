#!/usr/bin/env python3
"""Model-owned acknowledgements preserve conversation and completed playback boundaries."""
import json
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import finish, revisions


def followup(text, action, speech="うん、言葉の響きがつながったんやな。"):
    return {"action": action, "purpose": {"acknowledge_meaning": "understand_meaning",
        "confirm_close": "finish_workshop", "continue_workshop": "continue_discussion"}[action],
        "confidence": .95, "evidence": text, "speech": speech, "checks": []}


def main():
    passed = []
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def choose(text, prompt, n):
            if text == "なるほど":
                assert "会話段階: meaning_explained" in prompt
                return followup(text, "acknowledge_meaning")
            if "説明" in text:
                return step(text)
            return step(text, "respond", "うん、この句の話を続けよか。")
        calls = install(control, choose)
        sid = ready(base, send, rows)
        original = stored(sid)
        finish(base, send, sid, "この句を説明して")
        r = finish(base, send, sid, "なるほど")
        assert r["workshop_action"] == "acknowledge_meaning" and len(r["llm_reports"]) == 1, r
        assert r["text"] == "うん、言葉の響きがつながったんやな。"
        assert session(base, sid)["workshop_followup"] == "discussion"
        r = finish(base, send, sid, "うん")
        assert r["workshop_action"] == "respond" and len(calls) == 3
        assert hud(sid)["state"] == "open" and stored(sid) == original
        passed.append("meaning_ack_uses_model_reply_without_automatic_close_confirmation")
        for mode, status in [("fail", "failed"), ("slow", "cancelled")]:
            (folder / "player_mode").write_text(mode)
            send(sid)
            t = submit(base, sid, "句を説明して")
            if mode == "slow":
                wait_for(lambda: row(base, t, {"started"}))
                request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})
            wait_for(lambda: row(base, t, {status}))
            assert session(base, sid)["workshop_followup"] == "discussion"
            assert not any(h["turn_id"] == t for h in session(base, sid)["workshop_history"])
            (folder / "player_mode").write_text("ok")
        passed.append("failed_and_interrupted_speech_cannot_advance_followup_or_history")
        finish(base, send, sid, "句を説明して")
        send(sid, event={"name":"hostile_audio_detected", "source_kind":"auditory", "priority_hint":"background", "certainty":"high"},
             auditory_threats=[{"label":"zombie", "source_id":"sound-z", "spoken_name_allowed":True}])
        assert hud(sid)["state"] == "danger"
        assert session(base, sid)["workshop_followup"] == "discussion"
        assert hud(sid)["canonical_lines"] == LINES
        passed.append("combat_keeps_verse_and_clears_short_reply_stage")

    from check_workshop_revision import wire
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        wire(control, stored, sid)
        finish(base, send, sid, "上五のさくらのはを別の表現に直して")
        assert hud(sid)["pending_lines"]
        def mistaken(text, prompt, n):
            if n == 1:
                return followup(text, "confirm_close", "ここで区切ろか。")
            return step(text, "ask", "この案を使うか、まだ話すか聞かせてな。")
        install(control, mistaken)
        r = finish(base, send, sid, "それでいいよ")
        assert r["workshop_action"] == "ask" and len(r["llm_reports"]) == 2, r
        assert r["text"] == "この案を使うか、まだ話すか聞かせてな。"
        assert hud(sid)["state"] == "open" and hud(sid)["pending_lines"]
        assert hud(sid)["canonical_lines"] == LINES and not revisions(folder, sid)
        passed.append("inapplicable_followup_replans_without_adopting_or_discarding_pending")
    print(json.dumps({"passed":passed,"count":len(passed)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
