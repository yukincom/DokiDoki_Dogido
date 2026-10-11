#!/usr/bin/env python3
"""環境の発話／無言・取消・雷cue先行を実HTTPと模擬依存で確認する。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from check_dialogue import dependencies, register, request, row, running, snapshot, submit, wait_for

ROOT = Path(__file__).resolve().parents[1]


def reaction_context(incoming):
    for message in incoming.get("messages", []):
        try:
            value = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and {"topic", "observations", "properties", "recent"} <= value.keys():
            return value
    return None


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-environment-reaction-") as tmp, dependencies() as (dep, control, seen):
        folder = Path(tmp)
        player = folder / "player"
        # Record actual player invocation, including cached speech and cue files.
        # The mock endpoint records this POST before returning its expected 404.
        player.write_text(f"#!{sys.executable}\n"
                          "import time, urllib.request, urllib.error\n"
                          f"request = urllib.request.Request({(dep + '/player-started')!r}, data=b'{{}}', headers={{'Content-Type': 'application/json'}})\n"
                          "try: urllib.request.urlopen(request).close()\n"
                          "except urllib.error.HTTPError: pass\n"
                          "time.sleep(.05)\n")
        player.chmod(0o700)
        control["environment_action"] = "speak"
        spoken = {
            "surroundings": "周りの顔ぶれが変わったな。ちょっと気になるわ。",
            "smell": "ええ匂いやな。お腹がすいてきたわ。",
            "place": "昼でも木陰は気になるな。気ぃつけて進もな。",
            "thunder": "雷の音は何度聞いても落ち着かんな。",
        }

        def respond(incoming):
            context = reaction_context(incoming)
            if context is None:
                return None
            if "environment_payload" in control:
                return control["environment_payload"]
            action = control["environment_action"]
            return {"action": action, "speech": "" if action == "silent" else spoken[context["topic"]]}

        control["structured_handler"] = respond
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player, combat_settings={"surroundings_notice_interval_ms":100}, extra_args=("--max-tokens", "384")) as (base, _, _):
            sequence = 0
            normal = {"weather": "clear", "biome": "plains", "sky_visible": True,
                      "ceiling_height": 20, "local_light": 15, "time_phase": "day"}
            cow = {"type": "cow", "distance": 3, "temperament": "passive",
                   "direction": {"horizontal": "right"}}
            zombie = {"type": "zombie", "entity_id": "z", "distance": 2,
                      "direction": {"horizontal": "front", "cardinal": "north"}}
            bread = {"status":"present","components":[{"smell_id":"bread","category":"food","valence":"pleasant","source_kind":"hotbar","effective_strength":3}],"basis":"smell_policy_v2"}

            def send(sid, *, world=None, dimension="minecraft:overworld", **extra):
                nonlocal sequence
                sequence += 1
                event = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": sequence,
                         "observed_at": datetime.now(timezone.utc).isoformat(),
                         "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                         "player": {"name": "試験", "dimension": dimension},
                         "world": {**normal, **(world or {})},
                         "nearby_composition": {"mobs": [{"id":m["type"],"quantity":1} for m in extra.get("passive_mobs", [])],
                                                "blocks": [], "mobs_truncated": False, "blocks_truncated": False},
                         "smell_observation": {"status":"none","components":[],"basis":"smell_policy_v2"}, **extra}
                return request(base, "/api/v1/game-events", event, sid=sid)

            def close(sid):
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")

            def rows(sid):
                return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]

            def reactions(sid):
                return [r for r in rows(sid) if any(a.get("leaf", {}).get("kind") == "environment_reaction"
                         for a in r.get("combat_actions", []))]

            def calls():
                return [r for r in seen if r["path"] == "/v1/chat/completions" and reaction_context(r["body"]) is not None]

            def audio():
                return [parse_qs(urlsplit(r["path"]).query)["text"][0] for r in seen if r["path"].startswith("/audio_query?")]

            initialized = set()
            def trigger(sid, topic):
                if topic == "thunder":
                    return send(sid, world={"weather": "thunder", "thunder_sound_recent_ms": 0})
                if sid not in initialized:
                    send(sid)
                    initialized.add(sid)
                fields = ({"passive_mobs": [cow]} if topic in {"mob", "surroundings"}
                          else {"world": {"weather": "rain"}} if topic == "weather"
                          else {"smell_observation": bread} if topic == "smell"
                          else {"world": {"biome": "forest"}})
                send(sid, **fields)
                time.sleep(.12)
                return send(sid, **fields)

            def repeat(sid, topic):
                if topic == "weather":
                    return send(sid, world={"weather": "rain"})
                if topic == "smell":
                    return send(sid, smell_observation=bread)
                if topic == "place":
                    return send(sid, world={"biome": "forest"})
                if topic == "thunder":
                    return send(sid, world={"weather": "thunder", "thunder_sound_recent_ms": 0})
                return send(sid, passive_mobs=[cow])

            for decision in ("speak", "silent"):
                control["environment_action"] = decision
                for topic in ("surroundings", "thunder"):
                    sid = register(base, preview=False)
                    before_calls, before_audio = len(calls()), len(audio())
                    trigger(sid, topic)
                    status = "not_selected" if decision == "silent" and topic != "thunder" else "completed"
                    result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == status), None))
                    assert result["environment_reaction_outcome"] == decision, result
                    assert len(calls()) == before_calls + 1, calls()[before_calls:]
                    assert calls()[-1]["body"]["max_tokens"] == 384
                    delivered = audio()[before_audio:]
                    if decision == "speak":
                        assert result["text"].endswith(spoken[topic]), result
                        assert delivered, result
                    elif topic != "thunder":
                        assert not delivered and result["text"] == "", result
                        assert "started_at" not in result and "completed_at" not in result, result
                    else:
                        # A previously synthesized gasp can be served from cache.
                        assert len(delivered) <= 1 and spoken[topic] not in result["text"], delivered
                        assert result["text"] and "started_at" in result, result
                    for _ in range(3):
                        repeat(sid, topic)
                    time.sleep(.12)
                    assert len(calls()) == before_calls + 1, calls()[before_calls:]
                    close(sid)
                    passed.append(f"{topic}_{decision}_one_call_and_consideration_consumed")

            control["environment_action"] = "silent"
            sid = register(base, preview=False)
            send(sid)
            before_calls = len(calls())
            time.sleep(.12)
            # 新しい種類を含む部分通知だけでは周期比較を起動しない。
            request(base, "/api/v1/game-events", {
                "schema_version":"2026-05-24", "adapter":"fixture", "sequence":sequence+1,
                "observed_at":datetime.now(timezone.utc).isoformat(),
                "event":{"name":"hostile_audio_detected","source_kind":"auditory","priority_hint":"background","certainty":"high"},
                "nearby_composition":{"mobs":[{"id":"cow","quantity":1}],"blocks":[],"mobs_truncated":False,"blocks_truncated":False}}, sid=sid)
            sequence += 1
            time.sleep(.12)
            assert len(calls()) == before_calls
            send(sid, passive_mobs=[cow])
            wait_for(lambda: any(r["playback_status"] == "not_selected" for r in reactions(sid)))
            assert len(calls()) == before_calls+1
            time.sleep(.12)
            send(sid, passive_mobs=[cow])
            time.sleep(.12)
            assert len(calls()) == before_calls+1
            close(sid)
            passed.append("only_fresh_periodic_snapshot_triggers_and_unchanged_table_stays_quiet")

            # 4個体上限と独立した集計を実生成payloadまで運び、数量だけでは再発話しない。
            sid = register(base, preview=False)
            send(sid)
            time.sleep(.12)
            village = {"mobs":[{"id":"villager","quantity":"many","professions":[
                {"id":"librarian","quantity":"many"},{"id":"farmer","quantity":3}]}],
                "blocks":[],"mobs_truncated":False,"blocks_truncated":False}
            before_calls = len(calls())
            send(sid, nearby_composition=village)
            wait_for(lambda: any(r["playback_status"] == "not_selected" for r in reactions(sid)))
            context = reaction_context(calls()[-1]["body"])["world_context"]
            mob = context["observations"]["nearby_mob_types"][0]
            assert mob["quantity"] == "いっぱい", mob
            assert mob["profession_breakdown"]["groups"] == [
                {"id":"farmer","label":"農民","quantity":3}, {"id":"librarian","label":"司書","quantity":"いっぱい"}], mob
            reduced = {**village, "mobs":[{"id":"villager","quantity":4,
                "professions":[{"id":"farmer","quantity":4}]}]}
            time.sleep(.12)
            send(sid, nearby_composition=reduced)
            time.sleep(.12)
            assert len(calls()) == before_calls+1
            close(sid)
            passed.append("mob_quantities_and_villager_subsets_reach_model_without_count_only_reaction")

            # 発話中で使えない周期の変化は、後から同じ表で繰り返さない。
            sid = register(base, preview=False)
            before_calls = len(calls())
            control["delay"] = .5
            trigger(sid, "surroundings")
            wait_for(lambda: len(calls()) == before_calls+1)
            offered = reaction_context(calls()[-1]["body"])["world_context"]
            blocks = {"mobs":[{"id":"cow","quantity":1}],"blocks":["minecraft:oak_log"],"mobs_truncated":False,"blocks_truncated":False}
            time.sleep(.12)
            send(sid, passive_mobs=[cow], nearby_composition=blocks)
            wait_for(lambda: any(r["playback_status"] == "not_selected" for r in reactions(sid)))
            control["delay"] = 0
            time.sleep(.12)
            send(sid, passive_mobs=[cow], nearby_composition=blocks)
            time.sleep(.12)
            assert len(calls()) == before_calls+1
            assert offered["observations"]["nearby_block_types"] == []
            # 次の新しい変化は、消費済みの前周期とのブロック差分だけを渡す。
            newer = {**blocks, "blocks":["minecraft:birch_log"]}
            send(sid, passive_mobs=[cow], nearby_composition=newer)
            wait_for(lambda: len(calls()) == before_calls+2)
            context = reaction_context(calls()[-1]["body"])
            assert [c["kind"] for c in context["world_context"]["changes"]] == ["nearby_block_types"], context
            wait_for(lambda: len([r for r in reactions(sid) if r["playback_status"] == "not_selected"]) == 2)
            close(sid)
            passed.append("busy_period_is_consumed_without_replaying_its_change")

            # 当回の表を固定し、音声合成待ち中の普通の種類変化では取り消さない。
            sid = register(base, preview=False)
            control["environment_action"] = "speak"
            control["environment_payload"] = {"action":"speak", "speech":"さっき見えた木、気になるな。"}
            gate = threading.Event()
            control["tts_gates"]["さっき見えた木、気になるな。"] = gate
            before_calls = len(calls())
            before_seen = len(seen)
            trigger(sid, "surroundings")
            wait_for(lambda: any(r["path"].startswith("/synthesis?") for r in seen[before_seen:]))
            pending = reactions(sid)[-1]
            time.sleep(.12)
            send(sid, passive_mobs=[cow], nearby_composition=blocks)
            assert row(base,pending["turn_id"],{"cancelled"}) is None
            gate.set()
            completed = wait_for(lambda: row(base,pending["turn_id"],{"completed"}))
            assert completed["text"] == "さっき見えた木、気になるな。", completed
            assert len(calls()) == before_calls+1
            control.pop("environment_payload")
            close(sid)
            passed.append("ordinary_table_change_during_tts_keeps_the_sampled_utterance")

            control["environment_action"] = "speak"
            measured_smell = {"status":"present","components":[{"smell_id":"zombie","category":"decay","valence":"unpleasant","source_kind":"entity","effective_strength":5}],"basis":"smell_policy_v2"}
            sid = register(base, preview=False)
            before_calls = len(calls())
            control["environment_payload"] = {"action": "speak", "speech": "ゾンビ系の匂いがはっきりするで。"}
            send(sid, smell_observation=measured_smell)
            send(sid, smell_observation=measured_smell)
            result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
            assert len(calls()) == before_calls + 1, calls()[before_calls:]
            context = reaction_context(calls()[-1]["body"])["observations"]["smell"]
            assert "distance_estimate" not in context, context
            assert "direction_estimate" not in context["components"][0], context
            assert context["components"][0]["effective_strength"] == 5, context
            assert not {"source_id", "entity_id", "position"}.intersection(context["components"][0]), context
            query_calls = len(calls())
            before_query_rows = {r["turn_id"] for r in rows(sid)}
            query = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "匂いはどっちから？"})
            assert query["accepted"] and query["reason"] == "smell_query", query
            answer = wait_for(lambda: next((r for r in rows(sid) if r["turn_id"] not in before_query_rows and r["playback_status"] == "completed"), None))
            assert "匂いだけでは方向までは分からへん" in answer["text"] and "ブロック" not in answer["text"], answer
            send(sid, smell_observation={**measured_smell, "components": [{**measured_smell["components"][0], "effective_strength": 8}]})
            before_query_rows = {r["turn_id"] for r in rows(sid)}
            query = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "匂いはどっちから？"})
            assert query["accepted"] and query["reason"] == "smell_query", query
            answer = wait_for(lambda: next((r for r in rows(sid) if r["turn_id"] not in before_query_rows and r["playback_status"] == "completed"), None))
            assert "匂いだけでは方向までは分からへん" in answer["text"] and "強烈に" in answer["text"], answer
            assert len(calls()) == query_calls
            close(sid)
            control.pop("environment_payload")
            passed.append("smell_strength_reaches_model_and_fixed_questions_cannot_locate_source")

            # Unplayed words are refreshed when the same smell changes strength.
            sid = register(base, preview=False)
            before_calls = len(calls())
            control["delay"] = .6
            send(sid, smell_observation=measured_smell)
            send(sid, smell_observation=measured_smell)
            wait_for(lambda: len(calls()) > before_calls)
            pending = reactions(sid)[-1]
            stronger = {**measured_smell, "components": [{**measured_smell["components"][0], "effective_strength": 8}]}
            send(sid, smell_observation=stronger)
            cancelled = wait_for(lambda: row(base, pending["turn_id"], {"cancelled"}))
            assert "started_at" not in cancelled, cancelled
            control["delay"] = 0
            send(sid, smell_observation=stronger)
            wait_for(lambda: next((r for r in reactions(sid) if r["turn_id"] != pending["turn_id"]
                                   and r["playback_status"] == "completed"), None))
            assert len(calls()) == before_calls + 2, calls()[before_calls:]
            assert reaction_context(calls()[-1]["body"])["observations"]["smell"]["components"][0]["effective_strength"] == 8
            close(sid)
            control["delay"] = 0
            passed.append("changed_smell_strength_cancels_unplayed_words_and_retries_once")

            for retry_decision in ("speak", "silent"):
                sid = register(base, preview=False)
                before_calls = len(calls())
                control["delay"] = .6
                send(sid, smell_observation=measured_smell)
                send(sid, smell_observation=measured_smell)
                wait_for(lambda: len(calls()) > before_calls)
                pending = reactions(sid)[-1]
                update = {**measured_smell, "components": [{**measured_smell["components"][0], "effective_strength": 3}]}
                send(sid, smell_observation=update)
                cancelled = wait_for(lambda: row(base, pending["turn_id"], {"cancelled"}))
                assert "started_at" not in cancelled and not cancelled["text"], cancelled
                control["delay"] = 0
                control["environment_action"] = retry_decision
                send(sid, smell_observation=update)
                expected = "not_selected" if retry_decision == "silent" else "completed"
                wait_for(lambda: next((r for r in reactions(sid) if r["turn_id"] != pending["turn_id"]
                                       and r["playback_status"] == expected), None))
                assert len(calls()) == before_calls + 2, calls()[before_calls:]
                latest_context = reaction_context(calls()[-1]["body"])["observations"]["smell"]
                assert latest_context["components"][0]["effective_strength"] == 3, latest_context
                for _ in range(3):
                    send(sid, smell_observation=measured_smell)
                time.sleep(.12)
                assert len(calls()) == before_calls + 2, calls()[before_calls:]
                close(sid)
                control["environment_action"] = "speak"
                passed.append(f"changed_smell_strength_retries_once_and_consumes_{retry_decision}")

            for payload in ({"action": "silent", "speech": "勝手に話すで。"}, {"action": "move", "speech": "移動したで。"}):
                control["environment_payload"] = payload
                sid = register(base, preview=False)
                before_calls = len(calls())
                trigger(sid, "place")
                result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
                assert result["environment_reaction_outcome"] == "invalid_contract", result
                assert result["text"] == "周りの様子、ちょっと変わったな。", result
                assert len(calls()) == before_calls + 1
                close(sid)
            control.pop("environment_payload")
            passed.append("invalid_decision_uses_safe_code_fallback_without_retry")

            for speech, outcome in (("うん。", "speak"), ("そうやな。" * 5, "broken_output")):
                control["environment_payload"] = {"action": "speak", "speech": speech}
                sid = register(base, preview=False)
                trigger(sid, "place")
                result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
                assert result["environment_reaction_outcome"] == outcome, result
                assert result["text"] == (speech if outcome == "speak" else "周りの様子、ちょっと変わったな。"), result
                close(sid)
            control.pop("environment_payload")
            passed.append("short_acknowledgement_is_spoken_and_only_clear_loop_falls_back")

            def truncated(incoming):
                if reaction_context(incoming) is None:
                    return None
                return {"id": "truncated", "object": "chat.completion", "created": 0, "model": "mock-model",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"action":"silent","speech":""}'}, "finish_reason": "length"}]}

            control["completion_override"] = truncated
            sid = register(base, preview=False)
            trigger(sid, "place")
            result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
            assert result["environment_reaction_outcome"] == "truncated_output", result
            close(sid)
            control.pop("completion_override")
            passed.append("truncated_valid_child_is_not_a_silent_decision")

            for change in ("dimension_changed", "session_closed", "new_player_input", "hostile_arrived"):
                sid = register(base, preview=False)
                before_calls, before_audio = len(calls()), len(audio())
                control["delay"] = .6
                trigger(sid, "surroundings")
                wait_for(lambda: len(calls()) > before_calls)
                queued = reactions(sid)[-1]
                assert not queued["text"], queued
                if change == "dimension_changed":
                    send(sid, dimension="minecraft:the_nether", passive_mobs=[cow])
                elif change == "session_closed":
                    close(sid)
                elif change == "hostile_arrived":
                    send(sid, visual_threats=[zombie])
                else:
                    newer = submit(base, sid)
                    wait_for(lambda: row(base, newer, {"completed"}))
                cancelled = wait_for(lambda: row(base, queued["turn_id"], {"cancelled"}))
                assert "started_at" not in cancelled and not cancelled["text"], cancelled
                time.sleep(.7)
                assert spoken["surroundings"] not in audio()[before_audio:], audio()[before_audio:]
                control["delay"] = 0
                if change != "session_closed":
                    close(sid)
                passed.append(f"pending_reaction_cancelled_on_{change}")

            sid = register(base, preview=False)
            before = len(seen)
            control["delay"] = 1.5
            send(sid, world={"weather": "thunder", "thunder_sound_recent_ms": 0,
                             "nearby_lightning_strike_recent_ms": 0, "nearby_lightning_strike_distance": 4})
            wait_for(lambda: any(r["path"] == "/v1/chat/completions" for r in seen[before:]))
            entries = seen[before:]
            cue_index = next(i for i, r in enumerate(entries) if r["path"] == "/player-started")
            model_index = next(i for i, r in enumerate(entries) if r["path"] == "/v1/chat/completions")
            assert cue_index < model_index, entries
            pending = reactions(sid)[-1]
            started = time.monotonic()
            send(sid, visual_threats=[zombie])
            wait_for(lambda: next((r for r in rows(sid) if r["category"] == "callout" and r["playback_status"] in {"started", "completed"}), None), timeout=1)
            assert time.monotonic() - started < 1
            wait_for(lambda: row(base, pending["turn_id"], {"cancelled"}))
            close(sid)
            control["delay"] = 0
            passed.append("thunder_cue_precedes_model_and_combat_never_waits_for_reaction")

            sid = register(base, preview=False)
            before_calls = len(calls())
            for _ in range(2):
                send(sid, visual_threats=[zombie], passive_mobs=[cow], smell_observation=bread,
                     world={"weather": "thunder", "thunder_sound_recent_ms": 0, "biome": "forest"})
            wait_for(lambda: any(r["category"] == "callout" and r["playback_status"] == "completed" for r in rows(sid)))
            assert len(calls()) == before_calls, calls()[before_calls:]
            close(sid)
            passed.append("combat_does_not_start_environment_model_work")

            sid = register(base, preview=False)
            control["environment_action"] = "silent"
            trigger(sid, "mob")
            wait_for(lambda: any(r["playback_status"] == "not_selected" for r in reactions(sid)))
            control["environment_action"] = "speak"
            trigger(sid, "smell")
            result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
            context = reaction_context(calls()[-1]["body"])
            assert [r["reaction"] for r in context["recent"]["conversation"] if r["role"] == "event"] == ["silent"], context
            assert not [r for r in context["recent"]["conversation"] if r["role"] == "assistant"], context
            trigger(sid, "weather")
            wait_for(lambda: len([r for r in reactions(sid) if r["playback_status"] == "completed"]) == 2)
            context = reaction_context(calls()[-1]["body"])
            assert [r["text"] for r in context["recent"]["conversation"] if r["role"] == "assistant"] == [result["text"]], context
            close(sid)
            passed.append("completed_speech_and_chosen_silence_share_recent_context")

    report = {"passed": passed, "count": len(passed), "live_model": False, "real_microphone": False, "real_minecraft": False}
    output = ROOT / "reports/environment-reactions.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
