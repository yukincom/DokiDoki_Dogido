#!/usr/bin/env python3
"""句の相談が環境の固定反応に奪われず、敵接近だけで中断する実HTTP検査。"""
import json
import threading

from check_dialogue import register, request, row, submit, wait_for
from check_workshop_fixture import fixture
from request_classifier_fixture import resolve_input_response
from check_workshop_runtime import install, ready, step
from check_workshop_combat import MOB, SETTINGS, enter, end, drive, event


def check_evening_priority(passed):
    """生成中・実再生中・相談中を夕方が中断せず、終了時の現在時刻で再判断する。"""
    evening = {"biome": "minecraft:plains", "time_phase": "evening", "weather": "clear",
               "sky_visible": True, "ceiling_height": 20, "local_light": 12}
    for still_evening in [True, False]:
        with fixture(workshop_idle_ms=60000) as f:
            base, _, _, control, _, gate, drafting, checks, send, hud, rows, stored, folder = f
            gate.clear()
            sid = register(base, preview=False, body=True)
            send(sid)
            wait_for(drafting.is_set)
            def warnings():
                return [r for r in rows(sid) if any(a["kind"].startswith("night_warning_")
                        for a in r.get("combat_actions", []))]
            for _ in range(3):
                send(sid, world=evening)
                assert not warnings() and not stored(sid), rows(sid)
            (folder / "player_mode").write_text("slow")
            gate.set()
            poem = wait_for(lambda: next((r for r in rows(sid) if r["turn_id"].endswith(":poem")
                                          and r["playback_status"] == "started"), None))
            send(sid, world=evening)
            assert row(base, poem["turn_id"], {"started"}) and not warnings(), rows(sid)
            (folder / "player_mode").write_text("ok")
            wait_for(lambda: row(base, poem["turn_id"], {"completed"}))
            send(sid, world=evening)
            assert hud(sid)["state"] == "open" and not warnings(), rows(sid)
            assert len(wait_for(lambda: stored(sid))) == 1 and checks["drafts"] == 1
            current = evening if still_evening else {**evening, "time_phase": "day"}
            send(sid, world=current)
            install(control, lambda text, prompt, n: step(text, "close_workshop"))
            turn = submit(base, sid, "終了でお願いします")
            wait_for(lambda: hud(sid)["state"] == "closed")
            if still_evening:
                drive(lambda s: send(s, world=current), sid,
                      lambda: any(r["playback_status"] == "completed" for r in warnings()))
                for _ in range(3):
                    send(sid, world=current)
                assert len(warnings()) == 1, warnings()
            else:
                wait_for(lambda: row(base, turn, {"completed"}))
                for _ in range(3):
                    send(sid, world=current)
                assert not warnings(), warnings()
            passed.append("evening_waits_for_poem_and_workshop_then_" +
                          ("warns_once" if still_evening else "drops_outdated_warning"))


def main():
    passed = []
    check_evening_priority(passed)
    with fixture(combat_settings=SETTINGS) as f:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = f
        calls = install(control, lambda text, prompt, n: step(text, speech="この句の言い方を一緒に考えよか。"))
        sid = ready(base, send, rows, body=True)
        original = stored(sid)
        for text in ["夫婦の香りをくんくん匂うに変えませんか", "今の匂いは何？", "ゾンビはどこ？", "剣に変えるという表現はどう？"]:
            send(sid, smell_observation={"status":"none","components":[],"basis":"smell_policy_v2"})
            turn = submit(base, sid, text)
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_action"] == "explain" and calls[-1][0] == text, result
            assert hud(sid)["state"] == "open" and not result.get("combat_actions"), result
        assert stored(sid) == original
        passed.append("smell_combat_and_assist_words_reach_workshop_without_fixed_reply")
        for text in ["ゾンビどこ？", "敵何体？", "夫婦の香りをくんくん匂うに変えませんか"]:
            reply = resolve_input_response(base, send(sid, meta={"user_text": text})["player_input"])
            assert reply["accepted"] and reply.get("reason") != "combat_input", reply
            result = wait_for(lambda: row(base, reply["turn_id"], {"completed"}))
            assert result["workshop_action"] == "explain" and calls[-1][0] == text, result
        passed.append("game_event_text_is_not_silently_consumed_by_combat_queries")

        entered, release = threading.Event(), threading.Event()
        def blocked(text, prompt, n):
            entered.set()
            release.wait(8)
            return step(text, speech="この句の言い方を一緒に考えよか。")
        install(control, blocked)
        turn = submit(base, sid, "この句の表現を考えよう")
        wait_for(entered.is_set)
        before = len(rows(sid))
        try:
            for world, extra in [
                ({"weather": "thunder", "thunder_sound_recent_ms": 0}, {}),
                ({"time_phase": "evening", "sky_visible": True, "local_light": 12}, {}),
                ({"nearby_damaging_light_source_count": 1, "nearest_damaging_light_source_distance": 4.12}, {}),
                ({"local_light": 0, "sky_visible": False, "ceiling_height": 3}, {}),
                ({}, {"combat": {"recent_damage_ms": 0}, "player": {"name": "試験", "dimension": "minecraft:overworld", "health": 19}}),
                ({}, {"smell_observation": {"status":"present","components":[{"smell_id":"bread","category":"food","valence":"pleasant","source_kind":"hotbar","effective_strength":3}],"basis":"smell_policy_v2"}}),
            ]:
                for _ in range(2):
                    send(sid, world=world, **extra)
                    assert hud(sid)["state"] == "open", (world, hud(sid))
                    assert row(base, turn, {"generating"}), rows(sid)
                    assert len(rows(sid)) == before, rows(sid)
            release.set()
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_action"] == "explain" and stored(sid) == original
        finally:
            release.set()
        passed.append("thunder_evening_heat_darkness_damage_and_smell_do_not_interrupt_or_pause")

        send(sid)
        enter(send, rows, sid)
        assert hud(sid)["state"] == "danger"
        end(send, rows, sid)
        def omen(sid):
            return send(sid, world={"boss_omen_kind": "wither_assembly", "local_light": 0, "sky_visible": False, "ceiling_height": 3})
        drive(omen, sid, lambda: any(a["kind"] == "workshop_resume" and r["playback_status"] == "completed" for r in rows(sid) for a in r.get("combat_actions", [])))
        assert not any(a["kind"] == "boss_omen" for r in rows(sid) for a in r.get("combat_actions", [])), rows(sid)
        assert hud(sid)["state"] == "open" and stored(sid) == original
        passed.append("hostile_approach_pauses_and_recovers_in_darkness_without_omen_interruption")

        send(sid)
        # BERTの終了後に、環境反応が再開することを確認する。
        install(control, lambda text, prompt, n: step(text, "close_workshop"))
        turn = submit(base, sid, "終了でお願いします")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert hud(sid)["state"] == "closed"
        def thunder(sid):
            return send(sid, world={"weather": "thunder", "thunder_sound_recent_ms": 0}, combat={"recent_damage_ms": 60000})
        drive(thunder, sid, lambda: any(a["kind"] == "thunder_cue" for r in rows(sid) for a in r.get("combat_actions", [])))
        reply = resolve_input_response(base, request(base, "/api/v1/player-input", {"session_id": sid, "text": "今の匂いは何？", "source": "voice"}))
        assert reply["accepted"], reply
        if reply.get("queued"):
            routed = wait_for(lambda: row(base, reply["turn_id"], {"not_selected"}))
            assert resolve_input_response(base, routed["forwarded_input"])["reason"] == "smell_query", routed
        else:
            assert reply["reason"] == "smell_query", reply
        passed.append("closing_workshop_restores_environment_warning_and_smell_query")

    with fixture(combat_settings=SETTINGS) as f:
        base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = f
        install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows, body=True)
        (folder / "player_mode").write_text("slow")
        send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        warning = wait_for(lambda: next((r for r in rows(sid) if r.get("combat_actions") and r["playback_status"] == "started"), None))
        send(sid)
        assert row(base, warning["turn_id"], {"started"}), rows(sid)
        (folder / "player_mode").write_text("ok")
        drive(send, sid, lambda: row(base, warning["turn_id"], {"completed"}))
        passed.append("started_hostile_warning_finishes_after_enemy_leaves")

    for source in ["voice", "event"]:
        with fixture(combat_settings=SETTINGS) as f:
            base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder = f
            calls = install(control, lambda text, prompt, n: step(text))
            sid = ready(base, send, rows, body=True)
            enter(send, rows, sid)
            send(sid, event=event("combat_ended"), combat={"hostiles_within_scan_ground": 0})
            assert hud(sid)["state"] == "danger"
            text = "敵何体？"
            if source == "voice":
                reply = request(base, "/api/v1/player-input", {"session_id": sid, "text": text, "source": "voice"})
            else:
                reply = send(sid, meta={"user_text": text})["player_input"]
            assert reply.get("reason") in {"request_classification", "combat_workshop_input"} or reply.get("queued"), reply
            result = drive(send, sid, lambda: next((r for r in rows(sid) if r.get("player_input_text") == text and r["playback_status"] == "completed" and r.get("workshop_action") == "explain"), None))
            assert len(calls) == 1 and hud(sid)["state"] == "open", result
            passed.append(f"{source}_input_during_quiet_pause_reaches_workshop_once")
    print(json.dumps({"passed": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
