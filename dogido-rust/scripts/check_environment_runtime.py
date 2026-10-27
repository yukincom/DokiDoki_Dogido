#!/usr/bin/env python3
"""環境・持ち替えの実HTTP統合。実モデル・マイク・既存サービスは使わない。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from check_dialogue import dependencies, running, request, snapshot, wait_for, row

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.runtime_settings import COMBAT_DEFAULTS

from body_senses_fixture import attach_body, OWNER_ID
from request_classifier_fixture import mock_request_classifier, resolve_input_response

ROOT = Path(__file__).resolve().parents[1]


def main():
    passed = []
    labels = {"剣に持ち替えて":"equip_sword", "剣お願い":"equip_sword", "県に持ち替えて":"equip_sword",
              "そろそろ剣の方がよくない？":"equip_sword", "敵は何体？":"enemy_information",
              "ダイヤモンドの剣の耐久値は？":"not_target", "こんにちは":"not_target", "静かにして":"not_target"}
    with tempfile.TemporaryDirectory(prefix="dogido-environment-") as tmp, dependencies() as (dep, control, seen), mock_request_classifier(labels) as (classifier_args, _):
        folder = Path(tmp)
        player = folder / "player"
        player.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport time\ntime.sleep(float(Path(__file__).with_name('delay').read_text()))\n")
        player.chmod(0o700)
        (folder / "delay").write_text(".04")
        settings = {"aftermath_time_ms": 0, "player_input_priority_cooldown_ms": 100,
                    "surroundings_notice_interval_ms":100, "conversation_ambient_mute_ms": 300, "conversation_active_ttl_ms": 1200,
                    "dark_push_after_breath_protect_ms": 0}
        # 存在する設定だけを使い、所要時間の長いCDはこの模擬試験内に限り短縮。
        settings = {k: v for k, v in settings.items() if k in COMBAT_DEFAULTS}
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player, combat_settings=settings, extra_args=classifier_args,
                     haiku_settings={"memory_dir":str(folder / "memory")}) as (base, process, log):
            seq = 0
            def register(capable=True):
                return request(base, "/api/v1/adapter-sessions", {"adapter_name": "fabric-test", "adapter_version": "fixture",
                    "game": "minecraft-java", "schema_version": "2026-05-24", "player_name": "試験", "player_uuid": OWNER_ID,
                    "execution_capabilities": ["client.hotbar.select.v1"] if capable else []})["session_id"]
            def close(sid): request(base, "/api/v1/adapter-sessions/"+sid, method="DELETE")
            def event(*, hotbar=None, world=None, player_state=None, activity="holding", **extra):
                nonlocal seq
                seq += 1
                return attach_body({"schema_version": "2026-05-24", "adapter": "fixture", "sequence": seq,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                    "player": {"name": "試験", "hotbar": hotbar, **(player_state or {})},
                    "world": {"sky_visible": True, "ceiling_height": 20, "local_light": 15, "time_phase": "day", **(world or {})},
                    "nearby_composition": {"mobs":[{"id":m["type"],"quantity":1} for m in extra.get("passive_mobs", [])],
                        "blocks":[],"mobs_truncated":False,"blocks_truncated":False}, **extra}, activity=activity)
            def send(sid, **fields): return request(base, "/api/v1/game-events", event(**fields), sid=sid)
            def say(sid, text, source="voice"):
                got = request(base, "/api/v1/player-input", {"session_id": sid, "source": source, "text": text})
                assert got["accepted"], got
                return resolve_input_response(base, got)
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

            for source in ("voice", "text"):
                sid = register(); send(sid, hotbar=bar())
                before = calls(); got = say(sid, "県に持ち替えて", source)
                assert got["assist"]["intent"]["source"] == "modernbert" and calls() == before, got
                assert got["assist"]["intent"]["evidence"] == "県に持ち替えて", got
                assert got["assist"]["command"]["slot"] == 2, got
                close(sid)
            passed.append("classified_sword_uses_original_input_without_source_specific_word_gate")

            sid = register(); send(sid, hotbar=bar())
            text = "そろそろ剣の方がよくない？"
            send(sid, hotbar=bar(5))
            before = calls(); got = say(sid, text)
            command = got["assist"]["command"]
            assert got["reason"] == "assist_input" and command["slot"] == 5 and calls() == before, got
            duplicate = say(sid, text)
            assert duplicate.get("deduplicated") or duplicate.get("assist", {}).get("status") == "pending", duplicate
            assert send(sid, hotbar=bar(5))["commands"] == [command]
            close(sid)
            passed.append("bert_natural_sword_request_checks_current_slot_without_llm")

            sid = register(); send(sid, hotbar=bar())
            got = say(sid, text)
            command = got["assist"]["command"]
            wait_for(lambda: match(sid, "assist_feedback", "completed"))
            newer = say(sid, "こんにちは")
            wait_for(lambda: row(base, newer["turn_id"], {"completed"}))
            assert send(sid, hotbar=bar())["commands"] == [command]
            close(sid)
            passed.append("new_input_does_not_cancel_already_issued_sword_command")

            sid = register(); send(sid, hotbar=bar())
            before = calls(); got = say(sid, text)
            command = got["assist"]["command"]
            say(sid, "静かにして", "text")
            assert send(sid, hotbar=bar())["commands"] == [command]
            assert calls() == before
            close(sid)
            passed.append("quiet_input_does_not_reclassify_or_cancel_issued_command")

            sid = register(); send(sid, hotbar=bar()); before = calls()
            got = say(sid, "ダイヤモンドの剣の耐久値は？")
            # BERTのNotTargetは既存の知識問い合わせへ渡し、剣の生成判定を挟まない。
            answer = wait_for(lambda: next((r for r in rows(sid)
                if r.get("category") == "knowledge" and r["playback_status"] == "completed"), None))
            assert answer["knowledge_status"] in {"found", "unavailable", "not_found"}, answer
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
                "direction": {"horizontal": "right", "cardinal": "east"}, "environment": {"on_ground": True}}
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
            time.sleep(.12); send(sid, passive_mobs=[cat])
            wait_for(lambda: calls() > before)
            send(sid)
            comment = wait_for(lambda: match(sid, "surroundings_notice", "completed"))
            assert not match(sid, "surroundings_notice", "cancelled"), rows(sid)
            close(sid); control["delay"] = 0
            passed.append("periodic_mob_comment_keeps_its_sample_when_the_mob_disappears")

            sid = register(); send(sid); (folder / "delay").write_text(".5")
            time.sleep(.12); send(sid, passive_mobs=[cat])
            comment = wait_for(lambda: match(sid, "surroundings_notice", "started"))
            send(sid)
            wait_for(lambda: row(base, comment["turn_id"], {"completed"}))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("started_periodic_comment_finishes_after_mob_disappears")

            sid = register(); send(sid); (folder / "delay").write_text(".5")
            time.sleep(.12); send(sid, passive_mobs=[cat])
            comment = wait_for(lambda: match(sid, "surroundings_notice", "started"))
            send(sid, visual_threats=[zombie])
            wait_for(lambda: row(base, comment["turn_id"], {"cancelled"}))
            close(sid); (folder / "delay").write_text(".04")
            passed.append("hostile_still_interrupts_started_periodic_comment")

            sid = register(); send(sid)
            control["delay"] = .3
            turn = say(sid, "こんにちは")["turn_id"]
            time.sleep(.12); send(sid, passive_mobs=[cat])
            wait_for(lambda: row(base, turn, {"completed"}))
            assert not match(sid, "surroundings_notice", "completed")
            control["delay"] = 0; time.sleep(.31); send(sid, passive_mobs=[cat])
            time.sleep(.12)
            assert not match(sid, "surroundings_notice", "completed")
            send(sid, passive_mobs=[cat, {**cat, "type":"cow"}])
            wait_for(lambda: match(sid, "surroundings_notice", "completed")); close(sid)
            passed.append("chat_priority_drops_busy_period_and_only_new_changes_speak_later")

            sid = register(); send(sid)
            control["structured"] = {160: {"action": "acknowledge_supply_gain", "speech": "備えが増えたな。"}}
            saved = request(base, "/api/v1/catalog/readings", {"surface":"備え", "reading":"そなえ"}, method="PUT")["correction"]
            before = calls(); audio_before = len(seen)
            send(sid, inventory={"torch": 1})
            reply = wait_for(lambda: match(sid, "light_source_gain", "completed"))
            assert reply["text"] == "備えが増えたな。" and calls() == before + 1, reply
            assert len(reply["llm_reports"]) == 1, reply
            assert any(r["path"].startswith("/synthesis?") and r["body"]["test_text"].startswith("そなえ") for r in seen[audio_before:]), seen[audio_before:]
            request(base, "/api/v1/catalog/readings", {"surface":"備え", "expected_id":saved["id"]}, method="DELETE")
            close(sid); passed.append("light_action_and_speech_finish_in_one_call_with_reading_overlay")

            for payload in [{"action":"stay_silent", "speech":""},
                            {"action":"acknowledge_supply_gain", "speech":""}]:
                sid = register(); send(sid)
                control["structured"] = {160:payload}; before = calls()
                send(sid, inventory={"torch":1})
                wait_for(lambda: calls() == before + 1); time.sleep(.15)
                assert calls() == before + 1 and not match(sid,"light_source_gain","completed")
                close(sid)
            passed.append("silent_or_invalid_light_response_never_retries")
            control["structured"] = {160:{"action":"acknowledge_supply_gain", "speech":"備えが増えたな。"}}

            sid = register(); send(sid); (folder / "delay").write_text("1.5")
            send(sid, inventory={"torch":1})
            comment = wait_for(lambda: match(sid, "light_source_gain", "started"))
            queued = say(sid, "うん")
            assert queued["queued"], queued
            send(sid, inventory={"torch":1}, world={"biome":"desert", "local_light":13})
            assert row(base, comment["turn_id"], {"started"})
            (folder / "delay").write_text(".04")
            wait_for(lambda: row(base, comment["turn_id"], {"completed"}))
            wait_for(lambda: row(base, queued["turn_id"], {"completed"}))
            close(sid); passed.append("started_light_comment_finishes_before_acknowledgement_reply")

            sid = register(); send(sid); (folder / "delay").write_text("1.5")
            turn = say(sid, "こんにちは")["turn_id"]
            wait_for(lambda: row(base, turn, {"started"}))
            send(sid, world={"time_phase":"evening", "local_light":12})
            assert not any(any(a["kind"]=="night_warning_surface" for a in r.get("combat_actions", [])) for r in rows(sid))
            assert row(base, turn, {"started"})
            (folder / "delay").write_text(".04")
            wait_for(lambda: row(base, turn, {"completed"}))
            send(sid, world={"time_phase":"evening", "local_light":12})
            wait_for(lambda: match(sid, "night_warning_surface", "completed"))
            close(sid); passed.append("evening_waits_for_started_normal_reply_then_rechecks_current_world")

            sid = register(); send(sid)
            control["delay"] = .3; before = calls()
            send(sid, inventory={"torch": 1})
            wait_for(lambda: calls() >= before + 1)
            send(sid, world=dark, inventory={"torch": 1})
            time.sleep(.45)
            assert not match(sid, "light_source_gain", "completed")
            close(sid); control["delay"] = 0
            passed.append("light_response_invalidated_if_darkness_returns_during_generation")

            sid = register(); send(sid)
            control["leaf"] = "こんにちは。話しかけてくれてうれしいわ。"
            turn = say(sid, "こんにちは")["turn_id"]
            wait_for(lambda: row(base, turn, {"completed"}))
            time.sleep(1.21)
            send(sid, inventory={"torch": 1})
            wait_for(lambda: match(sid, "light_source_gain", "completed")); close(sid)
            passed.append("casual_foreground_expires_and_light_comments_resume")

            sid = register()
            scent = {"status":"present","components":[{"smell_id":"bread","category":"food","valence":"pleasant","source_kind":"hotbar","effective_strength":3}],"basis":"smell_policy_v2"}
            send(sid, smell_observation=scent)
            (folder / "delay").write_text(".5")
            say(sid, "今何の匂い？")
            old = wait_for(lambda: match(sid, "smell", "started"))
            send(sid, smell_observation={"status":"none","components":[],"basis":"smell_policy_v2"})
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
            original = wait_for(lambda: row(base, old, {"generating"}))
            send(sid, world={"thunder_sound_recent_ms": 0, "weather": "thunder"})
            send(sid)
            new = wait_for(lambda: next((r for r in rows(sid) if r.get("player_input_text") == "続きの話をしよう"
                and r["playback_status"] == "completed"), None))
            assert old == new["turn_id"], rows(sid)
            assert new["created_at"] == original["created_at"], (original, new)
            assert new["input_at_ms"] == original["input_at_ms"], (original, new)
            assert new["request_classification"] == original["request_classification"], (original, new)
            assert new["epoch"] > original["epoch"] and new["resumed_at"], (original, new)
            assert new["resolution"] == "reply_resumed", new
            assert len([r for r in rows(sid) if r.get("category") == "speech" and r.get("player_input_text") == new["player_input_text"]]) == 1, rows(sid)
            session = next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)
            # 再生済みの雷反応も共有履歴へ入る。再開した会話だけをturn IDで検査する。
            history = session["history"]
            resumed_id = new["turn_id"]
            users = [r for r in history if r["role"] == "user"]
            assert len(users) == 1 and users[0]["turn_id"] == resumed_id, session
            assert users[0]["text"] == "続きの話をしよう", session
            replies = [r for r in history if r["turn_id"] == resumed_id + ":reply"]
            assert len(replies) == 1 and replies[0]["role"] == "assistant", session
            completed_thunder = {r["turn_id"] + ":reply" for r in rows(sid)
                if r["playback_status"] == "completed" and
                any(a["kind"] == "thunder_reaction" for a in r.get("combat_actions", []))}
            reactions = [r for r in history if r["turn_id"] not in {resumed_id, resumed_id + ":reply"}]
            assert all(r["role"] == "assistant" and r["turn_id"] in completed_thunder for r in reactions), session
            assert len(reactions) == len({r["turn_id"] for r in reactions}), session
            close(sid); control["delay"] = 0
            passed.append("thunder_resumes_accepted_chat_without_duplicate_history")

            # 隣接かつ停止中だけが会話へ割り込む。移動中・距離2以上では候補とCDを残さない。
            heat = {"nearby_damaging_light_source_count": 1, "nearest_damaging_light_source_distance": 1.73}
            sid = register(); send(sid)
            control["delay"] = .3
            old = say(sid, "火のそばでも話を続けよう")["turn_id"]
            original = wait_for(lambda: row(base, old, {"generating"}))
            send(sid, world=heat, activity="calling")
            assert row(base, old, {"generating"}) and not match(sid,"damaging_light","generating")
            send(sid, world={**heat,"nearest_damaging_light_source_distance":2.24})
            assert row(base, old, {"generating"}) and not match(sid,"damaging_light","generating")
            send(sid, world=heat, player_state={"health": 20}, combat={"recent_damage_ms": 60000})
            warning = wait_for(lambda: match(sid, "damaging_light", "completed"))
            assert warning["text"] == control["leaf"] and warning["environment_reaction_outcome"] == "speak", warning
            assert warning["combat_actions"][0]["leaf"]["kind"] == "environment_reaction", warning
            new = wait_for(lambda: next((r for r in rows(sid) if r.get("player_input_text") == "火のそばでも話を続けよう"
                and r["playback_status"] == "completed"), None))
            assert old == new["turn_id"], rows(sid)
            assert new["created_at"] == original["created_at"], (original, new)
            assert new["input_at_ms"] == original["input_at_ms"], (original, new)
            assert new["request_classification"] == original["request_classification"], (original, new)
            assert new["epoch"] > original["epoch"] and new["resumed_at"], (original, new)
            assert new["resolution"] == "reply_resumed", new
            assert len([r for r in rows(sid) if r.get("category") == "speech" and r.get("player_input_text") == new["player_input_text"]]) == 1, rows(sid)
            send(sid, world=heat)
            assert len([r for r in rows(sid) if any(a["kind"] == "damaging_light" for a in r.get("combat_actions", []))]) == 1
            close(sid); control["delay"] = 0
            passed.append("heat_warns_only_when_adjacent_and_stationary_then_resumes_casual_chat")

            sid = register(); send(sid)
            (folder / "delay").write_text(".4")
            send(sid, world={"thunder_sound_recent_ms": 0, "weather": "thunder"})
            thunder = wait_for(lambda: next((r for r in rows(sid) if r["playback_status"] == "started"), None))
            send(sid, world=heat)
            assert not any(any(a["kind"] == "damaging_light" for a in r.get("combat_actions", [])) for r in rows(sid))
            wait_for(lambda: row(base, thunder["turn_id"], {"completed"}))
            send(sid, world=heat)
            wait_for(lambda: match(sid, "damaging_light", "completed"))
            assert not row(base, thunder["turn_id"], {"cancelled"})
            close(sid); (folder / "delay").write_text(".04")
            passed.append("heat_waits_for_thunder_without_consuming_its_cooldown")

            sid = register(); send(sid)
            turn = say(sid, "こんにちは")["turn_id"]
            wait_for(lambda: row(base, turn, {"completed"}))
            time.sleep(.32)
            send(sid, world={"biome": "taiga"})
            send(sid, world={"biome": "taiga"})
            wait_for(lambda: match(sid, "surroundings_notice", "completed"))
            close(sid)
            passed.append("taiga_entry_is_delivered_during_casual_foreground")
        report = {"passed": len(passed), "scenarios": passed, "all_owned_processes_stopped": True}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/environment-runtime.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
