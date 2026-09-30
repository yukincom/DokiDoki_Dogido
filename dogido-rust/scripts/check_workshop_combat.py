#!/usr/bin/env python3
"""戦闘後の句再掲・確認を、実HTTPと模擬モデル/再生で検査。所有プロセスは回収する。"""
from datetime import datetime, timedelta, timezone
import json
import time
from check_dialogue import register, request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import ready, session, install
from check_workshop_edits import edit, finish, revisions

SETTINGS = {"aftermath_time_ms": 150, "combat_clear_time_ms": 150}
MOB = {"type": "zombie", "entity_id": "z1", "distance": 6, "direction": {"horizontal": "front"}}


def event(name):
    return {"name": name, "source_kind": "system", "priority_hint": "background", "certainty": "high"}


def actions(rows, sid, kind):
    return [r for r in rows(sid) if any(a["kind"] == kind for a in r.get("combat_actions", []))]


def drive(send, sid, condition, timeout=8):
    last = 0
    def poll():
        nonlocal last
        if time.monotonic() - last >= .10:
            send(sid)
            last = time.monotonic()
        return condition()
    return wait_for(poll, timeout=timeout)


def enter(send, rows, sid):
    send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
    wait_for(lambda: any(r["playback_status"] == "completed" and r.get("combat_actions") for r in rows(sid)))


def end(send, rows, sid, outcome=None):
    payload = [] if outcome is None else [{"entity_id": "z1", "type": "zombie", "outcome": outcome, "evidence": "server_death_event"}]
    send(sid, event=event("combat_ended"), combat={"hostile_outcomes": payload, "hostiles_within_scan_ground": 0})
    return wait_for(lambda: next((r for r in actions(rows, sid, "aftermath") if r["playback_status"] == "completed"), None))


def returned(send, rows, sid, count=1):
    return drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume")[count-1:] if r["playback_status"] == "completed"), None))


def main():
    passed = []
    # 称賛/明示終了は返事の再生より先に閉じる。返事を戦闘が消しても復活しない。
    for text in ["いい句だね", "終了でいいよ"]:
        with fixture(combat_settings=SETTINGS, interval_ms=5000) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
            sid = register(base, preview=False)
            drive(send, sid, lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
            original = stored(sid)
            calls = install(control, lambda *args: (_ for _ in ()).throw(AssertionError("fixed close called model")))
            (folder / "player_mode").write_text("slow")
            turn = submit(base, sid, text)
            assert hud(sid)["state"] == "closed"
            wait_for(lambda: row(base, turn, {"started"}))
            (folder / "player_mode").write_text("ok")
            send(sid, visual_threats=[{**MOB, "type": "enderman"}], combat={"combat_active_hint": True})
            wait_for(lambda: row(base, turn, {"cancelled"}))
            end(send, rows, sid)
            for _ in range(5):
                send(sid); time.sleep(.1)
            assert hud(sid)["state"] == "closed" and not actions(rows, sid, "workshop_resume")
            assert not calls and stored(sid) == original and not revisions(folder, sid)
            passed.append("praise_closes_before_interrupted_ack" if text == "いい句だね" else "explicit_close_survives_interrupted_ack")
    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows); original = stored(sid)
        enter(send, rows, sid)
        assert hud(sid)["state"] == "danger"
        aftermath = end(send, rows, sid, "player_kill")
        llm_before = len([x for x in seen if x["path"] == "/v1/chat/completions"])
        r = returned(send, rows, sid)
        assert "倒せた" in r["text"] and r["text"].endswith("\n".join(LINES)) and "続ける？" not in r["text"], r
        assert datetime.fromisoformat(r["created_at"]) >= datetime.fromisoformat(aftermath["completed_at"])
        assert session(base, sid)["workshop_followup"] == "combat_resume_confirmation"
        assert not session(base, sid)["history"] and not session(base, sid)["workshop_history"]
        for _ in range(3): send(sid)
        assert len(actions(rows, sid, "workshop_resume")) == 1
        r = finish(base, send, sid, "うん")
        assert r["workshop_action"] == "resume_workshop" and r["llm_reports"] == [], r
        assert stored(sid) == original and not revisions(folder, sid)
        assert len([x for x in seen if x["path"] == "/v1/chat/completions"]) == llm_before
        passed.append("aftermath_then_one_resume_notice_and_yes_without_llm_or_fake_history")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        install(control, lambda text, prompt, n: edit(text))
        sid = ready(base, send, rows)
        # 明示編集は現在は即保存。保存失敗で保持された未採用案を戦闘へ持ち越す。
        revision_path = folder / "memory/sessions" / sid / "long_term/haiku_revisions.jsonl"
        revision_path.mkdir()
        r = finish(base, send, sid, "上五を『さくらいろ』にして")
        assert r["workshop_outcome"] == "pending_save_failed", r
        revision_path.rmdir()
        enter(send, rows, sid); end(send, rows, sid)
        r = returned(send, rows, sid)
        assert "離れられた" in r["text"] and "さくらいろ" in r["text"] and "倒" not in r["text"], r
        assert hud(sid)["canonical_lines"] == LINES and hud(sid)["pending_lines"][0] == "さくらいろ"
        r = finish(base, send, sid, "もういい")
        assert r["workshop_action"] == "ask" and hud(sid)["state"] == "open", r
        assert hud(sid)["pending_lines"] and not revisions(folder, sid)
        r = finish(base, send, sid, "採用して")
        assert r["workshop_outcome"] == "pending_saved" and len(revisions(folder, sid)) == 1
        passed.append("escape_reposts_pending_without_claiming_kill_or_discarding_it_on_decline")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid)
        returned(send, rows, sid)
        r = finish(base, send, sid, "続けない")
        assert r["workshop_action"] == "decline_resume" and hud(sid)["state"] == "closed", r
        passed.append("decline_completed_resume_confirmation_closes_without_save")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def choose(text, prompt, n):
            assert "会話段階: combat_resume_confirmation" in prompt
            action = "resume_workshop" if n == 1 else "decline_resume"
            return {"action": action, "purpose": "continue_discussion" if n == 1 else "finish_workshop",
                    "confidence": .95, "evidence": text, "speech": "", "checks": []}
        calls = install(control, choose)
        sid = ready(base, send, rows)
        # 「さっきの句」は6hの想起経路が所有するため、現在句の再開を明示する。
        for text, expected in [("この句についてまた話そうよ", "resume_workshop"),
                               ("この句の相談はここで切り上げよう", "decline_resume")]:
            enter(send, rows, sid); end(send, rows, sid)
            returned(send, rows, sid, count=len(calls) + 1)
            r = finish(base, send, sid, text)
            assert r["workshop_action"] == expected and len(r["llm_reports"]) == 1, r
        assert len(calls) == 2 and hud(sid)["state"] == "closed" and not revisions(folder, sid)
        passed.append("natural_resume_and_decline_use_one_existing_step_each")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid, "player_kill")
        (folder / "player_mode").write_text("fail")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "failed"), None))
        assert session(base, sid)["workshop_followup"] == "discussion" and hud(sid)["state"] == "danger"
        for _ in range(4): send(sid)
        assert len(actions(rows, sid, "workshop_resume")) == 1
        (folder / "player_mode").write_text("ok")
        r = returned(send, rows, sid, count=2)
        assert "倒せた" in r["text"] and session(base, sid)["workshop_followup"] == "combat_resume_confirmation"
        assert not revisions(folder, sid)
        passed.append("failed_resume_keeps_pin_and_reason_and_retries_after_backoff")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid, "player_kill")
        (folder / "player_mode").write_text("slow")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "started"), None))
        assert session(base, sid)["workshop_followup"] == "discussion"
        (folder / "player_mode").write_text("ok")
        send(sid, visual_threats=[{**MOB, "entity_id": "z2"}], combat={"combat_active_hint": True})
        wait_for(lambda: row(base, r["turn_id"], {"cancelled"}))
        assert hud(sid)["state"] == "danger" and hud(sid)["canonical_lines"] == LINES
        send(sid, event=event("combat_ended"), combat={"hostile_outcomes": [], "hostiles_within_scan_ground": 0})
        r = returned(send, rows, sid, count=2)
        assert "倒せた" not in r["text"] and "離れられた" in r["text"], r
        passed.append("new_threat_cancels_notice_and_discards_previous_victory_reason")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid)
        (folder / "player_mode").write_text("slow")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "started"), None))
        (folder / "player_mode").write_text("ok")
        shown = finish(base, send, sid, "今の句")
        assert shown["text"] == "\n".join(LINES)
        assert row(base, r["turn_id"], {"cancelled"})
        assert session(base, sid)["workshop_followup"] == "discussion"
        for _ in range(3): send(sid)
        assert len(actions(rows, sid, "workshop_resume")) == 1
        passed.append("new_question_interrupts_notice_without_arming_or_repeating_it")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        warning = wait_for(lambda: next((r for r in rows(sid) if r.get("combat_actions") and r["playback_status"] == "started"), None))
        reply = request(base, "/api/v1/player-input", {"session_id": sid, "text": "終了", "source": "voice"})
        assert reply["accepted"] and reply["reason"] == "combat_workshop_closed", reply
        assert hud(sid)["state"] == "closed"
        assert row(base, warning["turn_id"], {"started"})
        assert not actions(rows, sid, "workshop_resume")
        passed.append("explicit_close_during_combat_keeps_warning_running")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid)
        reply = request(base, "/api/v1/player-input", {"session_id": sid, "text": "終了", "source": "voice"})
        assert reply["accepted"] and hud(sid)["state"] == "closed", reply
        r = wait_for(lambda: next((r for r in actions(rows, sid, "workshop_close_feedback") if r["playback_status"] == "completed"), None))
        assert r["player_input_text"] == "終了" and not revisions(folder, sid)
        passed.append("combat_close_acknowledged_after_warning_without_reopening_or_saving")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid)
        (folder / "player_mode").write_text("slow")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "started"), None))
        send(sid, observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat())
        wait_for(lambda: row(base, r["turn_id"], {"cancelled"}))
        assert session(base, sid)["workshop_followup"] == "discussion"
        assert not any(r["playback_status"] == "completed" for r in actions(rows, sid, "workshop_resume"))
        passed.append("stale_snapshot_cancels_return_prompt_and_cannot_confirm_resume")

    print(json.dumps({"passed": len(passed), "cases": passed, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
