#!/usr/bin/env python3
"""身体基準の観測が実HTTPから一回の会話入力へ届くことを模擬LLMで検査する。

自前の一時serverだけを起動し、常駐service・Minecraft・実モデル・音声は使わない。
二者が異なる入力、視認切替、未取得、owner不一致、失効、判断時の固定を確認する。
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from body_senses_fixture import attach_body
from check_dialogue import (acknowledge, dependencies, display_row, register, request,
                            row, running, submit_text, wait_for)

ROOT = Path(__file__).resolve().parents[1]


def world_context(incoming):
    decoder = json.JSONDecoder()
    for message in incoming["messages"]:
        content = message.get("content", "")
        for offset, char in enumerate(content):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(content[offset:])
            except ValueError:
                continue
            if isinstance(value, dict) and "observations" in value:
                return value
    raise AssertionError("world_context missing from actual model request")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "target/debug/dogido-rust")
    args = parser.parse_args()
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-body-senses-") as tmp, dependencies() as (dep, control, seen):
        with running(args.binary.resolve(), Path(tmp), dep,
                     haiku_settings={"enabled": False, "memory_enabled": False}) as (base, _, _):
            sid = register(base, preview=False, voice_enabled=False, body=True)
            sequence = 0

            def event(visibility="visible"):
                nonlocal sequence
                sequence += 1
                raw = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": sequence,
                       "observed_at": datetime.now(timezone.utc).isoformat(),
                       "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                       "player": {"name": "試験", "dimension": "minecraft:overworld", "health": 20,
                                  "position": {"x": 10, "y": 80, "z": 23}},
                       "combat": {"combat_active_hint": False},
                       "world": {"biome": "desert", "weather": "clear", "time_phase": "day", "local_light": 15, "sky_visible": True},
                       "passive_mobs": [{"type": "pig", "distance": 2}],
                       "nearby_composition": {"mobs": [{"id": "pig", "quantity": 1}], "blocks": ["minecraft:cactus"],
                                              "mobs_truncated": False, "blocks_truncated": False},
                       "smell_observation": {"status": "none", "components": [], "basis": "smell_policy_v2"}}
                senses = {"world": {"biome": "plains", "weather": "clear", "time_phase": "day", "local_light": 15, "sky_visible": True},
                          "passive_mobs": [{"type": "cow", "distance": 3}],
                          "nearby_composition": {"mobs": [{"id": "cow", "quantity": 1}], "blocks": ["minecraft:oak_log"],
                                                 "mobs_truncated": False, "blocks_truncated": False},
                          "smell_observation": {"status": "present", "basis": "smell_policy_v2", "components": [
                              {"smell_id": "bread", "category": "food", "valence": "pleasant", "source_kind": "hotbar", "effective_strength": 2}]}}
                value = attach_body(raw, senses=senses, body_position={"x": 10, "y": 80, "z": 20}, visibility=visibility)
                if visibility == "not_visible":
                    value["body_observation"]["sample"]["self_pose"].update(yaw=-180, view_yaw=-180)
                return value

            def send(value):
                return request(base, "/api/v1/game-events", value, sid=sid)

            def ask(text):
                count = len([r for r in seen if r["path"] == "/v1/chat/completions"])
                turn = submit_text(base, sid, text)
                reply = wait_for(lambda: display_row(base, turn))
                requests = [r["body"] for r in seen if r["path"] == "/v1/chat/completions"][count:]
                assert len(requests) == 1, requests
                context = world_context(requests[0])
                acknowledge(base, sid, reply)
                wait_for(lambda: row(base, turn, {"audio_disabled"}))
                return context, reply

            send(event())
            context, reply = ask("今日はどんな感じかな")
            obs = context["observations"]
            assert list(obs)[0] == "body_observation" and obs["observer"] == "dogido", obs
            current = obs["body_observation"]["current"]
            assert list(current)[0] == "vision", current
            assert current["vision"]["player_visibility"] == "visible", current
            assert current["self"]["position"]["y"] == 80, current
            assert obs["location_biome"]["id"] == "plains", obs
            assert "cow" in json.dumps(obs) and "pig" not in json.dumps(obs), obs
            assert "oak_log" in json.dumps(obs) and "cactus" not in json.dumps(obs), obs
            assert "bread" in json.dumps(obs["smell"]), obs
            assert "world" not in current["senses"], current
            assert reply["body_observation_input"] == obs["body_observation"], reply
            passed.append("distinct_body_facts_reach_one_model_call_and_diagnostic_copy")

            send(event("not_visible"))
            context, _ = ask("もう少し話そう")
            body = context["observations"]["body_observation"]
            assert body["current"]["vision"]["player_visibility"] == "not_visible", body
            assert body["previous"]["vision"]["player_visibility"] == "visible", body
            passed.append("current_visibility_switch_preserves_previous_and_history")

            for label in ("unavailable", "owner_mismatch", "stale", "senses_unavailable"):
                value = event()
                if label == "unavailable":
                    value["body_observation"] = {"version": 3, "status": "unavailable", "reason": "body_absent"}
                elif label == "owner_mismatch":
                    value["body_observation"]["sample"]["context"]["owner_id"] = "44444444-4444-4444-8444-444444444444"
                elif label == "stale":
                    value["body_observation"]["age_ms"] = 6000
                else:
                    sample = value["body_observation"]["sample"]
                    sample["senses"] = {"version": 1, "status": "unavailable", "basis": "body_pose_client_world",
                                        "source_server_tick": sample["server_tick"], "reason": "area_unloaded"}
                send(value)
                context, _ = ask("今の様子を教えて")
                obs = context["observations"]
                assert obs["sensory_status"] == "unavailable", (label, obs)
                assert "location_biome" not in obs and "smell" not in obs and "nearby_mob_types" not in obs, (label, obs)
                assert "cactus" not in json.dumps(obs) and "pig" not in json.dumps(obs), (label, obs)
                passed.append(label + "_never_falls_back_to_player_senses")

            assert not any(r["path"].startswith(("/audio_query", "/synthesis")) for r in seen)
    print(json.dumps({"passed": passed, "model": "mock", "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
