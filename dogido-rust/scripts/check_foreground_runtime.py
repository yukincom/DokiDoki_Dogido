#!/usr/bin/env python3
"""会話所有権・発句時計・戦闘退避を実HTTPと模擬音声で確認する。"""
import time
from check_dialogue import register, request, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture


def foreground(base, sid):
    return next(s["foreground"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)


def main():
    passed = []
    with fixture(interval_ms=4000, quiet_time_ms=0,
                 combat_settings={"conversation_active_ttl_ms":2500}) as f:
        base, _, log, control, seen, _, _, checks, send, _, rows, _, _ = f
        sid = register(base, preview=False)
        send(sid)
        turn = submit(base, sid, "枕詞って何？")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert foreground(base, sid)["route"] == "learning", log.read_text()
        frozen = foreground(base, sid)["haiku_elapsed_ms"]
        for _ in range(3):
            send(sid); time.sleep(.12)
            assert foreground(base, sid)["haiku_elapsed_ms"] == frozen
        assert checks["drafts"] == 0
        assert not any(r["path"] == "/v1/chat/completions" for r in seen)
        passed.append("knowledge_freezes_clock_without_extra_model_calls")

        def expired():
            send(sid)
            return foreground(base, sid)["route"] == "none"
        wait_for(expired, timeout=4)
        at_expiry = foreground(base, sid)["haiku_elapsed_ms"]
        assert frozen <= at_expiry < frozen + 300, (frozen, at_expiry)
        assert checks["drafts"] == 0
        def started():
            send(sid)
            return any(r.get("haiku_job_id") for r in rows(sid))
        wait_for(started, timeout=7)
        assert foreground(base, sid)["haiku_elapsed_ms"] >= 4000
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
        assert foreground(base, sid)["route"] == "haiku_workshop"
        assert checks["drafts"] == 1
        passed.append("expired_learning_resumes_remaining_interval_then_opens_workshop")

    with fixture(enabled=False) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, _ = f
        sid = register(base, preview=False); send(sid)
        turn = submit(base, sid, "枕詞って何？")
        wait_for(lambda: row(base, turn, {"completed"}))
        send(sid, visual_threats=[{"type":"zombie", "entity_id":"z", "distance":5,
                                  "direction":{"horizontal":"front"}}])
        state = foreground(base, sid)
        assert state["combat_active"] and state["route"] == "none", state
        assert state["suspended"]["source_turn_ids"] == [turn], state
        for _ in range(12): send(sid)
        assert foreground(base, sid)["suspended"]["remaining_player_turns"] == 10
        assert foreground(base, sid)["combat_active"]
        send(sid, event={"name":"combat_ended", "source_kind":"system", "priority_hint":"background", "certainty":"high"})
        assert not foreground(base, sid)["combat_active"]
        wait_for(lambda: all(r["playback_status"] in {"completed", "cancelled", "failed"} for r in rows(sid)))
        send(sid)
        resume = submit(base, sid, "さっきの話の続き")
        wait_for(lambda: row(base, resume, {"completed"}))
        assert foreground(base, sid)["route"] == "learning"
        assert foreground(base, sid)["suspended"] is None
        prompts = "\n".join(m["content"] for r in seen if r["path"] == "/v1/chat/completions" for m in r["body"]["messages"])
        assert "プレイヤーが明示的に再開した保留話題:" in prompts
        passed.append("combat_bookmark_survives_ticks_and_resumes_only_on_player_turn")

        turn = submit(base, sid, "こんにちは")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert foreground(base, sid)["route"] == "casual"
        send(sid, visual_threats=[{"type":"zombie", "entity_id":"z2", "distance":5}])
        assert foreground(base, sid)["combat_active"]
        send(sid, event={"name":"player_died", "source_kind":"system", "priority_hint":"background", "certainty":"high"})
        assert not foreground(base, sid)["combat_active"]
        passed.append("ordinary_route_switch_and_death_release")

    with fixture(enabled=False) as f:
        base, _, log, _, _, _, _, _, send, _, rows, _, _ = f
        sid = register(base, preview=False); send(sid)
        send(sid, world={"sky_visible": False, "ceiling_height": 3,
                        "enclosure_score": .7, "local_light": 0, "danger_darkness_score": .9})
        assert not foreground(base, sid)["combat_active"], log.read_text()
        send(sid)
        assert not foreground(base, sid)["combat_active"]
        send(sid, visual_threats=[{"type":"zombie", "entity_id":"portal-z", "distance":5}])
        assert foreground(base, sid)["combat_active"]
        send(sid, player={"name":"試験", "dimension":"minecraft:the_nether"})
        assert not foreground(base, sid)["combat_active"], log.read_text()
        passed.append("darkness_does_not_latch_combat_and_dimension_change_releases_it")
    print(f"PASS {len(passed)} foreground checks; all owned processes stopped")
    for name in passed: print(name)


if __name__ == "__main__":
    main()
