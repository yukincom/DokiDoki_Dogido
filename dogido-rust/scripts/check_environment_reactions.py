#!/usr/bin/env python3
"""環境の発話／無言・取消・雷cue先行を実HTTPと模擬依存で確認する。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
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
            "mob": "ウシを見ると、なんや気持ちが和むな。",
            "weather": "天気が変わってきたな。ちょっと気になるわ。",
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
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player, extra_args=("--max-tokens", "384")) as (base, _, _):
            sequence = 0
            normal = {"weather": "clear", "biome": "plains", "sky_visible": True,
                      "ceiling_height": 20, "local_light": 15, "time_phase": "day"}
            cow = {"type": "cow", "distance": 3, "temperament": "passive",
                   "direction": {"horizontal": "right"}}
            zombie = {"type": "zombie", "entity_id": "z", "distance": 2,
                      "direction": {"horizontal": "front", "cardinal": "north"}}
            bread = {"status": "present", "smell_id": "bread", "category": "food", "valence": "pleasant",
                     "specificity": "source", "source_kind": "hotbar", "effective_strength": 3}

            def send(sid, *, world=None, dimension="minecraft:overworld", **extra):
                nonlocal sequence
                sequence += 1
                event = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": sequence,
                         "observed_at": datetime.now(timezone.utc).isoformat(),
                         "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                         "player": {"name": "試験", "dimension": dimension},
                         "world": {**normal, **(world or {})}, **extra}
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

            def trigger(sid, topic):
                if topic == "mob":
                    return send(sid, passive_mobs=[cow])
                if topic == "weather":
                    send(sid)
                    return send(sid, world={"weather": "rain"})
                if topic == "smell":
                    send(sid, smell_observation=bread)
                    return send(sid, smell_observation=bread)
                if topic == "place":
                    return send(sid, world={"biome": "forest"})
                return send(sid, world={"weather": "thunder", "thunder_sound_recent_ms": 0})

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
                for topic in spoken:
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

            control["environment_action"] = "speak"
            spatial_smell = {"status": "present", "smell_id": "zombie", "category": "decay", "valence": "unpleasant",
                             "specificity": "source", "source_kind": "entity", "effective_strength": 5,
                             "direction_estimate": {"cardinal": "east"}}
            sid = register(base, preview=False)
            before_calls = len(calls())
            control["environment_payload"] = {"action": "speak", "speech": "東のほうからゾンビの匂いがするで。"}
            send(sid, smell_observation=spatial_smell)
            send(sid, smell_observation=spatial_smell)
            result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
            assert len(calls()) == before_calls + 1, calls()[before_calls:]
            context = reaction_context(calls()[-1]["body"])["observations"]["smell"]
            assert "distance_estimate" not in context, context
            assert context["direction_estimate"]["cardinal"] == "east", context
            assert not {"source_id", "entity_id", "position", "effective_strength"}.intersection(context), context
            query_calls = len(calls())
            before_query_rows = {r["turn_id"] for r in rows(sid)}
            query = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "匂いはどっちから？"})
            assert query["accepted"] and query["reason"] == "smell_query", query
            answer = wait_for(lambda: next((r for r in rows(sid) if r["turn_id"] not in before_query_rows and r["playback_status"] == "completed"), None))
            assert "東のほうから来とるみたいや" in answer["text"] and "ブロック" not in answer["text"], answer
            send(sid, smell_observation={**spatial_smell, "direction_estimate": None})
            before_query_rows = {r["turn_id"] for r in rows(sid)}
            query = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "匂いはどっちから？"})
            assert query["accepted"] and query["reason"] == "smell_query", query
            answer = wait_for(lambda: next((r for r in rows(sid) if r["turn_id"] not in before_query_rows and r["playback_status"] == "completed"), None))
            assert "方向はまだ絞れてへん" in answer["text"] and "東" not in answer["text"], answer
            assert len(calls()) == query_calls
            close(sid)
            control.pop("environment_payload")
            passed.append("smell_estimates_reach_model_and_fixed_questions_use_current_direction")

            # Strength changes during approach do not cancel the same bearing.
            sid = register(base, preview=False)
            before_calls = len(calls())
            control["delay"] = .6
            send(sid, smell_observation=spatial_smell)
            send(sid, smell_observation=spatial_smell)
            wait_for(lambda: len(calls()) > before_calls)
            pending = reactions(sid)[-1]
            send(sid, smell_observation={**spatial_smell, "effective_strength": 7})
            completed = wait_for(lambda: row(base, pending["turn_id"], {"completed"}))
            assert len(calls()) == before_calls + 1 and "cancel_reason" not in completed, completed
            close(sid)
            control["delay"] = 0
            passed.append("approaching_smell_strength_does_not_cancel_current_bearing")

            for retry_decision in ("speak", "silent"):
                sid = register(base, preview=False)
                before_calls = len(calls())
                control["delay"] = .6
                send(sid, smell_observation=spatial_smell)
                send(sid, smell_observation=spatial_smell)
                wait_for(lambda: len(calls()) > before_calls)
                pending = reactions(sid)[-1]
                update = {**spatial_smell, "direction_estimate": {"cardinal": "west"}}
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
                assert latest_context["direction_estimate"]["cardinal"] == "west", latest_context
                for _ in range(3):
                    send(sid, smell_observation=spatial_smell)
                time.sleep(.12)
                assert len(calls()) == before_calls + 2, calls()[before_calls:]
                close(sid)
                control["environment_action"] = "speak"
                passed.append(f"changed_smell_bearing_retries_once_and_consumes_{retry_decision}")

            for payload in ({"action": "silent", "speech": "勝手に話すで。"}, {"action": "move", "speech": "移動したで。"}):
                control["environment_payload"] = payload
                sid = register(base, preview=False)
                before_calls = len(calls())
                trigger(sid, "place")
                result = wait_for(lambda: next((r for r in reactions(sid) if r["playback_status"] == "completed"), None))
                assert result["environment_reaction_outcome"] == "invalid_contract", result
                assert result["text"] == "ここ、雰囲気が変わったな。ちょっと気になるわ。", result
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
                assert result["text"] == (speech if outcome == "speak" else "ここ、雰囲気が変わったな。ちょっと気になるわ。"), result
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

            for change in ("mob_gone", "smell_gone", "weather_changed", "place_covered", "dimension_changed", "session_closed", "new_player_input"):
                sid = register(base, preview=False)
                before_calls, before_audio = len(calls()), len(audio())
                control["delay"] = .6
                topic = "smell" if change == "smell_gone" else "weather" if change == "weather_changed" else "place" if change == "place_covered" else "mob"
                trigger(sid, topic)
                wait_for(lambda: len(calls()) > before_calls)
                queued = reactions(sid)[-1]
                assert not queued["text"], queued
                if change == "mob_gone":
                    send(sid)
                elif change == "smell_gone":
                    send(sid, smell_observation={"status": "none"})
                elif change == "weather_changed":
                    send(sid)
                elif change == "place_covered":
                    send(sid, world={"biome": "forest", "sky_visible": False, "overhead_cover_type": "stone"})
                elif change == "dimension_changed":
                    send(sid, dimension="minecraft:the_nether", passive_mobs=[cow])
                elif change == "session_closed":
                    close(sid)
                else:
                    newer = submit(base, sid)
                    wait_for(lambda: row(base, newer, {"completed"}))
                cancelled = wait_for(lambda: row(base, queued["turn_id"], {"cancelled"}))
                assert "started_at" not in cancelled and not cancelled["text"], cancelled
                time.sleep(.7)
                # A fresh weather/place observation may legitimately start a
                # new reaction; only the invalidated turn must stay unplayed.
                if change not in {"weather_changed", "place_covered"}:
                    assert spoken[topic] not in audio()[before_audio:], audio()[before_audio:]
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
