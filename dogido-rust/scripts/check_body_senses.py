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
            assert "self" not in current and "player" not in current, current
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
            assert "previous" not in body and "coordinate_system" not in body, body
            changes = {c["kind"]: c for c in context["changes"]}
            assert changes["body_player_visibility"]["before"]["player_visibility"] == "visible", changes
            assert changes["body_player_visibility"]["now"]["player_visibility"] == "not_visible", changes
            assert changes["body_self_orientation"]["before"] is None, changes
            assert changes["body_self_orientation"]["now"] == {"changed": True}, changes
            passed.append("current_visibility_switch_and_orientation_changes_reach_normal_reply")

            # 一回の入力に現在値を固定し、生成中の更新は次の判断へ渡す。
            send(event())
            position_calls = []
            def position_handler(incoming):
                position_calls.append(incoming)
                assert not incoming.get("tools"), incoming
                assert "get_body_position" not in json.dumps(incoming), incoming
                context = world_context(incoming)
                current = context["observations"]["body_observation"]["current"]["position_relation"]
                assert current["distance_blocks"] == (3.0 if len(position_calls) == 1 else 9.0), context
                if len(position_calls) == 1:
                    newer = event()
                    newer["body_observation"]["sample"]["player_pose"]["position"]["z"] = 29
                    send(newer)
                return {"action": "speak", "speech": f"オレから見て前、{current['distance_blocks']:.1f}ブロックやで。"}
            control["structured_handler"] = position_handler
            first, reply = ask("今はどのくらい離れてる？")
            assert reply["body_observation_input"]["current"]["position_relation"]["distance_blocks"] == 3.0, reply
            second, reply = ask("今はどのくらい離れてる？")
            relation_changes = [c for c in second["changes"] if c["kind"] == "body_position_relation"]
            assert len(relation_changes) == 1 and relation_changes[0]["now"]["distance_blocks"] == 9.0, second
            assert reply["body_position_changes_input"] == relation_changes, reply
            third, reply = ask("今はどのくらい離れてる？")
            assert not [c for c in third["changes"] if c["kind"] == "body_position_relation"], third
            assert len(position_calls) == 3, position_calls
            assert "body_position_requested" not in reply, reply
            del control["structured_handler"]
            passed.append("position_is_in_every_first_request_changes_ack_only_offered_revision")

            # 二者の遮蔽を入れ替えても、既存の一回生成へそれぞれの接触と整理済み材料が届く。
            for sheltered in ("player", "dogido"):
                value = event()
                body_senses = value["body_observation"]["sample"]["senses"]
                for who, world, holder in (("player", value["world"], value["player"]),
                                           ("dogido", body_senses["world"], body_senses)):
                    under_leaves = who == sheltered
                    world.update(biome="forest", weather="rain", overhead_cover_type="foliage" if under_leaves else "none",
                                 sky_visible=not under_leaves, depth_below_surface=0)
                    holder["environment"] = {"touching_water": False, "submerged_in_water": False,
                                             "touching_rain": not under_leaves, "touching_water_or_rain": not under_leaves}
                send(value)
                context, _ = ask("今のそれぞれの様子を話して")
                obs = context["observations"]
                assert "player_environment" not in obs, obs
                for who, name in (("player", "プレイヤー"), ("dogido", "ドギド")):
                    state = obs["subject_states"][who]
                    assert state["subject"] == name and state["precipitation_kind"] == "rain", state
                    assert state["contact"]["touching_rain"] == (who != sheltered), state
                    assert "木の葉の下" in state["relations"]["rain_and_cover"] if who == sheltered else "雨が当たっている" in state["relations"]["rain_and_cover"], state
                    assert state["observed_at"], state
                passed.append(f"{sheltered}_shelter_and_other_subject_rain_reach_existing_single_call")

            # 保護中でも接触の実測は残る。危険発話を抑え、通常会話へ渡る二者の分離を確かめる。
            for touching in ("player", "dogido"):
                value = event()
                value["player"]["invulnerable"] = True
                value["player"]["environment"] = {"touching_lava": touching == "player", "on_fire": True}
                value["body_observation"]["sample"]["senses"]["environment"] = {"touching_lava": touching == "dogido"}
                send(value)
                context, _ = ask("今のそれぞれの様子を話して")
                states = context["observations"]["subject_states"]
                for who, name in (("player", "プレイヤー"), ("dogido", "ドギド")):
                    state = states[who]
                    assert state["contact"]["touching_lava"] == (who == touching), state
                    if who == touching:
                        assert name + "は溶岩に触れ" in state["relations"]["heat"], state
                assert states["player"]["contact"]["on_fire"] is True, states
                assert states["dogido"]["contact"]["on_fire"] is None, states
                assert states["dogido"]["vitals"] is None, states
                passed.append(f"{touching}_lava_contact_is_separate_from_other_subject_and_fire")

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
                if label == "senses_unavailable":
                    assert obs["body_observation"]["current"]["position_relation"]["distance_blocks"] == 3.0, obs
                else:
                    assert obs["body_observation"]["status"] == "unavailable", obs
                    assert "current" not in obs["body_observation"], obs
                assert context.get("changes", []) == [], (label, context)
                assert "location_biome" not in obs and "smell" not in obs and "nearby_mob_types" not in obs, (label, obs)
                assert "subject_states" not in obs, (label, obs)
                assert "cactus" not in json.dumps(obs) and "pig" not in json.dumps(obs), (label, obs)
                passed.append(label + "_never_falls_back_to_player_senses")

            assert not any(r["path"].startswith(("/audio_query", "/synthesis")) for r in seen)
    print(json.dumps({"passed": passed, "model": "mock", "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
