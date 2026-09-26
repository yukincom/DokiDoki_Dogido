#!/usr/bin/env python3
"""環境・持ち替えの実HTTP統合。実モデル・マイク・既存サービスは使わない。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from check_dialogue import dependencies, running, request, snapshot, wait_for, row

ROOT = Path(__file__).resolve().parents[1]


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-environment-") as tmp, dependencies() as (dep, control, seen):
        folder = Path(tmp)
        player = folder / "player"
        player.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport time\ntime.sleep(float(Path(__file__).with_name('delay').read_text()))\n")
        player.chmod(0o700)
        (folder / "delay").write_text(".04")
        settings = {"aftermath_time_ms": 0, "player_input_priority_cooldown_ms": 100,
                    "conversation_ambient_mute_ms": 300, "conversation_active_ttl_ms": 1200,
                    "dark_push_after_breath_protect_ms": 0}
        # 存在する設定だけを使い、所要時間の長いCDはこの模擬試験内に限り短縮。
        defaults = {}
        for file in [ROOT / "src/combat/defaults.json", *sorted((ROOT / "src/environment").glob("*_defaults.json"))]:
            defaults.update(json.loads(file.read_text()))
        settings = {k: v for k, v in settings.items() if k in defaults}
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player, combat_settings=settings) as (base, process, log):
            seq = 0
            def register(capable=True):
                return request(base, "/api/v1/adapter-sessions", {"adapter_name": "fabric-test", "adapter_version": "fixture",
                    "game": "minecraft-java", "schema_version": "2026-05-24", "player_name": "試験",
                    "execution_capabilities": ["client.hotbar.select.v1"] if capable else []})["session_id"]
            def close(sid): request(base, "/api/v1/adapter-sessions/"+sid, method="DELETE")
            def event(*, hotbar=None, world=None, player_state=None, **extra):
                nonlocal seq
                seq += 1
                return {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": seq,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                    "player": {"name": "試験", "hotbar": hotbar, **(player_state or {})},
                    "world": {"sky_visible": True, "ceiling_height": 20, "local_light": 15, "time_phase": "day", **(world or {})}, **extra}
            def send(sid, **fields): return request(base, "/api/v1/game-events", event(**fields), sid=sid)
            def say(sid, text, source="voice"):
                got = request(base, "/api/v1/player-input", {"session_id": sid, "source": source, "text": text})
                assert got["accepted"], got
                return got
            def rows(sid): return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]
            def match(sid, kind, status):
                return next((r for r in reversed(rows(sid)) if r["playback_status"] == status
                    and any(a["kind"] == kind for a in r.get("combat_actions", []))), None)
            def calls(): return len([r for r in seen if r["path"] == "/v1/chat/completions"])
            def bar(slot=2): return {"selected_slot": 0, "slots": [{"slot": slot, "item_id": "minecraft:iron_sword",
                "count": 1, "weapon_kind": "sword", "attack_damage": 6, "damage": 10, "max_damage": 250}]}
            def command_result(command, **change):
                return {"command_id": command["command_id"], "command_type": "select_hotbar", "status": "succeeded",
                    "executed_at": datetime.now(timezone.utc).isoformat(), "selected_slot": command["slot"],
                    "selected_item_id": command["expected_item_id"], "detail_code": "ok", **change}

            sid = register(); send(sid, hotbar=bar())
            before = calls(); got = say(sid, "剣に持ち替えて")
            command = got["assist"]["command"]
            assert command["slot"] == 2 and calls() == before, got
            wait_for(lambda: match(sid, "assist_feedback", "completed"))
            repeat = send(sid, hotbar=bar())
            assert repeat["commands"] == [command], repeat
            result = command_result(command)
            acknowledged = send(sid, hotbar=bar(), command_results=[result])
            assert acknowledged["commands"] == [] and acknowledged["acknowledged_command_ids"] == [command["command_id"]], acknowledged
            passed.append("explicit_request_no_llm_command_redelivery_and_result_ack")
            batch = request(base, "/api/v1/game-events/batch", {"events": [event(command_results=[result]), event()]}, sid=sid)
            assert batch["commands"] == [] and batch["acknowledged_command_ids"] == [command["command_id"]], batch
            passed.append("batch_ack_keeps_results_from_earlier_event")
            close(sid)

            sid = register(False); send(sid, hotbar=bar())
            got = say(sid, "剣お願い")
            assert got["assist"]["detail_code"] == "capability_missing" and got["assist"]["command"] is None, got
            wait_for(lambda: match(sid, "assist_feedback", "completed")); close(sid)
            passed.append("execution_capability_required")

            sid = register(); send(sid, hotbar=bar())
            before = calls(); got = say(sid, "県に持ち替えて")
            assert got["assist"]["intent"]["source"] == "code_voice_asr" and calls() == before, got
            close(sid)
            sid = register(); send(sid, hotbar=bar())
            got = say(sid, "県に持ち替えて", "text")
            assert got.get("reason") != "assist_input", got
            wait_for(lambda: row(base, got["turn_id"], {"completed", "unsupported"})); assert not send(sid)["commands"]
            close(sid); passed.append("sword_homophone_correction_voice_only")

            sid = register(); send(sid, hotbar=bar())
            text = "そろそろ剣の方がよくない？"
            control["structured"] = {96: {"intent": "select_weapon", "weapon_kind": "sword", "is_request": True, "evidence": text, "confidence": .98}}
            control["delay"] = .35
            before = calls(); got = say(sid, text)
            assert got["reason"] == "assist_intent", got
            duplicate = say(sid, text); assert duplicate.get("deduplicated"), duplicate
            send(sid, hotbar=bar(5))
            wait_for(lambda: row(base, got["turn_id"], {"quiet"}))
            command = send(sid, hotbar=bar(5))["commands"][0]
            assert command["slot"] == 5 and calls() == before + 1, command
            close(sid); control["delay"] = 0
            passed.append("natural_request_one_bounded_call_rechecks_current_slot")

            sid = register(); send(sid, hotbar=bar())
            control["delay"] = .4
            got = say(sid, text); wait_for(lambda: calls() > before + 1)
            newer = say(sid, "こんにちは")
            wait_for(lambda: row(base, newer["turn_id"], {"completed"}))
            assert row(base, got["turn_id"], {"cancelled"}) and not send(sid, hotbar=bar())["commands"]
            close(sid); control["delay"] = 0
            passed.append("new_input_cancels_pending_world_intent")

            sid = register(); send(sid, hotbar=bar())
            control["delay"] = .4; before = calls()
            got = say(sid, text); wait_for(lambda: calls() > before)
            send(sid, hotbar=bar(), meta={"user_text": "静かにして"})
            time.sleep(.5)
            assert row(base, got["turn_id"], {"cancelled"}) and not send(sid, hotbar=bar())["commands"]
            close(sid); control["delay"] = 0
            passed.append("piggyback_combat_input_cancels_pending_world_intent")

            sid = register(); send(sid, hotbar=bar()); before = calls()
            got = say(sid, "ダイヤモンドの剣の耐久値は？")
            wait_for(lambda: next((r for r in rows(sid) if r["playback_status"] == "unsupported"), None))
            assert calls() == before and not send(sid)["commands"], rows(sid)
            close(sid); passed.append("knowledge_question_does_not_call_assist_model")

            sid = register(); send(sid)
            unknown = {"command_id": "unknown-command", "command_type": "select_hotbar", "status": "failed",
                "executed_at": datetime.now(timezone.utc).isoformat(), "detail_code": "failed"}
            got = send(sid, command_results=[unknown])
            assert got["acknowledged_command_ids"] == ["unknown-command"] and not rows(sid), got
            close(sid); passed.append("unknown_result_ack_is_not_our_operation")

            dark = {"sky_visible": False, "ceiling_height": 3, "enclosure_score": .7,
                "local_light": 0, "danger_darkness_score": .9}
            sid = register(); send(sid)
            control["delay"] = .4
            send(sid, world=dark)
            wait_for(lambda: match(sid, "dark_push_no_light", "queued"))
            send(sid)
            wait_for(lambda: match(sid, "dark_push_no_light", "cancelled"))
            assert not match(sid, "dark_push_no_light", "completed"), rows(sid)
            close(sid); control["delay"] = 0
            passed.append("dark_warning_invalidated_after_light_recovery")

            sid = register(); send(sid); (folder / "delay").write_text(".5")
            send(sid, world=dark)
            warning = wait_for(lambda: match(sid, "dark_push_no_light", "started"))
            send(sid)
            wait_for(lambda: row(base, warning["turn_id"], {"completed"}))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("started_darkness_comment_finishes_after_light_recovery")

            zombie = {"type": "zombie", "entity_id": "z", "distance": 3,
                "direction": {"horizontal": "right", "cardinal": "east"}}
            sid = register(); send(sid, world=dark)
            wait_for(lambda: match(sid, "dark_push_no_light", "completed"))
            (folder / "delay").write_text(".4")
            send(sid, world=dark, visual_threats=[zombie])
            warning = wait_for(lambda: next((r for r in rows(sid)
                if r["category"] == "callout" and r["playback_status"] == "started"), None))
            for _ in range(4):
                send(sid, world=dark, visual_threats=[zombie]); time.sleep(.1)
            wait_for(lambda: row(base, warning["turn_id"], {"completed"}))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("darkness_does_not_repeatedly_cancel_same_combat_warning")

            sid = register(); send(sid, world=dark)
            wait_for(lambda: match(sid, "dark_push_no_light", "completed"))
            recovery = {**dark, "local_light": 4}
            send(sid, world=recovery, visual_threats=[zombie])
            wait_for(lambda: next((r for r in rows(sid) if r["category"] == "callout"
                and r["playback_status"] == "completed"), None))
            send(sid, world=recovery)
            for _ in range(8):
                time.sleep(.15); send(sid, world=recovery)
                if match(sid, "dark_push_after_breath", "completed"): break
            wait_for(lambda: match(sid, "dark_push_after_breath", "completed"))
            close(sid); passed.append("darkness_recovery_relief_survives_combat_deferral")

            sid = register(); far = {**zombie, "distance": 12}
            send(sid, visual_threats=[far])
            wait_for(lambda: next((r for r in rows(sid) if r["playback_status"] == "completed"), None))
            send(sid, world=dark, visual_threats=[far])
            wait_for(lambda: match(sid, "dark_push_no_light", "completed"))
            send(sid, world={**dark, "thunder_sound_recent_ms": 0, "weather": "thunder"}, visual_threats=[far])
            wait_for(lambda: match(sid, "thunder_cue", "completed"))
            close(sid); passed.append("distant_hostile_does_not_reject_darkness_or_thunder")

            sid = register(); send(sid)
            charged = {**zombie, "type": "charged_creeper", "distance": 8}
            (folder / "delay").write_text(".4")
            send(sid, visual_threats=[charged])
            wait_for(lambda: next((r for r in rows(sid) if r["playback_status"] == "started"), None))
            question = say(sid, "敵は何体？")
            assert question.get("queued"), question
            send(sid, visual_threats=[charged], world={"thunder_sound_recent_ms": 0, "weather": "thunder"})
            for _ in range(14):
                time.sleep(.2); send(sid, visual_threats=[charged])
                if any(r.get("player_input_text") == "敵は何体？" and r["playback_status"] == "completed" for r in rows(sid)):
                    break
            wait_for(lambda: next((r for r in rows(sid) if r.get("player_input_text") == "敵は何体？"
                and r["playback_status"] == "completed"), None))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("thunder_does_not_overwrite_pending_combat_question")

            cat = {"type": "cat", "entity_id": "cat-1", "distance": 3, "direction": {"horizontal": "front"}}
            sid = register(); send(sid)
            control["delay"] = .4; before = calls()
            send(sid, passive_mobs=[cat])
            wait_for(lambda: calls() > before)
            send(sid)
            wait_for(lambda: match(sid, "ambient", "cancelled"))
            close(sid); control["delay"] = 0
            passed.append("ambient_mob_disappearance_cancels_pending_leaf")

            sid = register(); send(sid); (folder / "delay").write_text(".5")
            send(sid, passive_mobs=[cat])
            comment = wait_for(lambda: match(sid, "ambient", "started"))
            send(sid)
            wait_for(lambda: row(base, comment["turn_id"], {"completed"}))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("started_mob_comment_finishes_after_mob_disappears")

            sid = register(); send(sid); (folder / "delay").write_text(".5")
            send(sid, passive_mobs=[cat])
            comment = wait_for(lambda: match(sid, "ambient", "started"))
            send(sid, visual_threats=[zombie])
            wait_for(lambda: row(base, comment["turn_id"], {"cancelled"}))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("hostile_still_interrupts_started_mob_comment")

            sid = register(); send(sid)
            control["delay"] = .3
            turn = say(sid, "こんにちは")["turn_id"]
            send(sid, passive_mobs=[cat])
            wait_for(lambda: row(base, turn, {"completed"}))
            assert not any(a["kind"] == "ambient" for r in rows(sid) for a in r.get("combat_actions", []))
            control["delay"] = 0; time.sleep(.31); send(sid, passive_mobs=[cat])
            wait_for(lambda: match(sid, "ambient", "completed")); close(sid)
            passed.append("chat_priority_then_mob_reaction_after_configured_mute")

            sid = register(); send(sid)
            control["structured"] = {160: {"action": "acknowledge_supply_gain", "basis_ids": ["first_light_supply"], "confidence": .95}}
            control["leaf"] = "松明を十本クラフトしたんやな。"
            send(sid, inventory={"torch": 1})
            reply = wait_for(lambda: match(sid, "light_source_gain", "completed"))
            assert "クラフト" not in reply["text"] and "十本" not in reply["text"], reply
            close(sid); passed.append("light_plan_and_leaf_preserve_observed_supply_only")

            sid = register(); send(sid)
            control["delay"] = .25; before = calls()
            send(sid, inventory={"torch": 1})
            wait_for(lambda: calls() >= before + 2)
            send(sid, world=dark, inventory={"torch": 1})
            wait_for(lambda: match(sid, "light_source_gain", "cancelled"))
            assert not match(sid, "light_source_gain", "completed")
            close(sid); control["delay"] = 0
            passed.append("light_leaf_invalidated_if_darkness_returns_after_plan")

            sid = register(); send(sid)
            control["leaf"] = "こんにちは。話しかけてくれてうれしいわ。"
            turn = say(sid, "こんにちは")["turn_id"]
            wait_for(lambda: row(base, turn, {"completed"}))
            time.sleep(1.21)
            send(sid, inventory={"torch": 1})
            wait_for(lambda: match(sid, "light_source_gain", "completed")); close(sid)
            passed.append("casual_foreground_expires_and_light_comments_resume")

            sid = register()
            scent = {"status": "present", "smell_id": "bread", "category": "food", "valence": "pleasant",
                "source_kind": "hotbar", "specificity": "source", "effective_strength": 3}
            send(sid, smell_observation=scent)
            (folder / "delay").write_text(".5")
            say(sid, "今何の匂い？")
            old = wait_for(lambda: match(sid, "smell", "started"))
            send(sid, smell_observation={"status": "none"})
            new = wait_for(lambda: match(sid, "smell", "completed"))
            assert old["turn_id"] == new["turn_id"] and old["text"] == new["text"], (old, new)
            say(sid, "今何の匂い？")
            current = wait_for(lambda: next((r for r in rows(sid) if r["turn_id"] != old["turn_id"] and r.get("player_input_text") == "今何の匂い？" and r["playback_status"] == "completed"),None))
            assert current["text"] != old["text"]
            close(sid); (folder / "delay").write_text(".04")
            passed.append("started_smell_answer_finishes_and_next_question_rechecks_observation")

            sid = register(); send(sid)
            control["leaf"] = "こんにちは。話しかけてくれてうれしいわ。"
            (folder / "delay").write_text(".4")
            send(sid, world={"thunder_sound_recent_ms": 0, "weather": "thunder"})
            wait_for(lambda: next((r for r in rows(sid) if r["playback_status"] == "started"), None))
            got = say(sid, "こんにちは")
            assert got.get("queued"), got
            send(sid)
            wait_for(lambda: next((r for r in rows(sid) if r.get("player_input_text") == "こんにちは" and r["playback_status"] == "completed"), None))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("thunder_preserves_new_player_input_until_warning_finishes")

            sid = register(); send(sid)
            control["delay"] = .3
            old = say(sid, "続きの話をしよう")["turn_id"]
            wait_for(lambda: row(base, old, {"generating"}))
            send(sid, world={"thunder_sound_recent_ms": 0, "weather": "thunder"})
            send(sid)
            new = wait_for(lambda: next((r for r in rows(sid) if r.get("player_input_text") == "続きの話をしよう"
                and r["playback_status"] == "completed"), None))
            assert old != new["turn_id"] and row(base, old, {"cancelled"}), rows(sid)
            session = next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)
            assert [r["role"] for r in session["history"]] == ["user", "assistant"], session
            close(sid); control["delay"] = 0
            passed.append("thunder_resumes_accepted_chat_without_duplicate_history")
        report = {"passed": len(passed), "scenarios": passed, "all_owned_processes_stopped": True}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/environment-runtime.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
