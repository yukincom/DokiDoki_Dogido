#!/usr/bin/env python3
"""本人の危険実測から既存の環境LLM一回生成・取消までを模擬HTTPで検査する。

自前の一時serverと模擬LLM/TTSだけを使い、常駐サービス・Minecraft・実音声を操作しない。
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

from check_dialogue import dependencies, register, request, row, running, snapshot, wait_for

ROOT = Path(__file__).resolve().parents[1]


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-hazards-") as tmp, dependencies() as (dep, control, seen):
        folder = Path(tmp)
        player = folder / "player"
        player.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(.02)\n")
        player.chmod(0o700)
        requests = []
        entered, release = threading.Event(), threading.Event()
        control["hazard_gate"] = False

        def respond(incoming):
            for message in incoming["messages"]:
                try:
                    context = json.loads(message["content"])
                except (ValueError, TypeError):
                    continue
                if not isinstance(context, dict) or context.get("topic") != "danger":
                    continue
                requests.append(context)
                if control["hazard_gate"]:
                    entered.set()
                    assert release.wait(10), "test gate expired"
                if control.get("hazard_silent"):
                    return {"action": "silent", "speech": ""}
                return {"action": "speak", "speech": "今の足元、気になるで。"}
            return None

        control["structured_handler"] = respond
        with running(ROOT / "target/debug/dogido-rust", folder, dep, player=player,
                     haiku_settings={"enabled": False, "memory_enabled": False}) as (base, _, _):
            sequence = 0

            def send(sid, contact, *, motion=None, air=None, hostile=False, invulnerable=False, paused=False):
                nonlocal sequence
                sequence += 1
                event = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": sequence,
                         "observed_at": datetime.now(timezone.utc).isoformat(),
                         "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                         "player": {"name": "試験", "dimension": "minecraft:overworld", "health": 19,
                                    "environment": contact, "motion": motion, "invulnerable": invulnerable},
                         "world": {"biome": "plains", "weather": "clear", "sky_visible": True,
                                   "local_light": 15, "time_phase": "day", "air_supply": air, "game_paused": paused},
                         "combat": {"combat_active_hint": True, "recent_damage_ms": 0,
                                    "hostiles_within_10": 1 if hostile else 0},
                         "visual_threats": ([{"type": "zombie", "entity_id": "z", "distance": 3,
                                              "direction": {"horizontal": "front"}}] if hostile else [])}
                return request(base, "/api/v1/game-events", event, sid=sid)

            def rows(sid):
                return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid and
                        any(a["kind"] == "player_hazard" for a in r.get("combat_actions", []))]

            def complete(sid, after=0):
                return wait_for(lambda: next((r for r in rows(sid)[after:] if r["playback_status"] == "completed"), None))

            lava = {"touching_lava": True, "on_fire": True}
            fire = {"touching_lava": False, "on_fire": True}
            safe = {"touching_lava": False, "on_fire": False, "on_ground": True}
            sid = register(base, preview=False)
            send(sid, lava)
            first = complete(sid)
            assert first["text"] == "今の足元、気になるで。" and first["environment_reaction_outcome"] == "speak", first
            assert len(requests) == 1 and requests[-1]["observations"]["hazard"] == "lava", requests
            assert requests[-1]["observations"]["subject_states"]["player"]["contact"]["touching_lava"] is True
            for _ in range(3):
                send(sid, lava)
            time.sleep(.1)
            assert len(requests) == 1 and len(rows(sid)) == 1, rows(sid)
            send(sid, fire)
            complete(sid, 1)
            assert requests[-1]["observations"]["hazard"] == "fire"
            send(sid, safe)
            assert len(rows(sid)) == 2
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            passed.append("player_lava_then_fire_without_body_survive_damage_mode_once_per_episode")

            for name, contact, motion, air in [
                ("falling", {"on_ground": False, "touching_water": False, "touching_lava": False},
                 {"fall_distance": 5, "vertical_velocity": -.5, "climbing": False, "gliding": False, "flying": False}, None),
                ("low_air", {"submerged_in_water": True}, None, 40),
            ]:
                sid = register(base, preview=False)
                count = len(requests)
                send(sid, contact, motion=motion, air=air)
                complete(sid)
                assert len(requests) == count + 1 and requests[-1]["observations"]["hazard"] == name
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
                passed.append(name + "_uses_existing_single_generation")

            sid = register(base, preview=False)
            count = len(requests)
            send(sid, lava, invulnerable=True)
            send(sid, {"on_fire": False, "touching_water_or_rain": True})
            time.sleep(.15)
            assert len(requests) == count and not rows(sid)
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            passed.append("immunity_and_unknown_lava_do_not_create_hazard")

            for reason in ("contact_ended", "hostile_arrived"):
                sid = register(base, preview=False)
                entered.clear(); release.clear(); control["hazard_gate"] = True
                try:
                    send(sid, lava)
                    assert entered.wait(5), reason
                    pending = wait_for(lambda: next(iter(rows(sid)), None))
                    send(sid, safe if reason == "contact_ended" else lava, hostile=reason == "hostile_arrived")
                    wait_for(lambda: row(base, pending["turn_id"], {"cancelled"}))
                finally:
                    release.set(); control["hazard_gate"] = False
                assert not any(r["playback_status"] == "completed" for r in rows(sid)), rows(sid)
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
                passed.append("unplayed_hazard_cancelled_when_" + reason)
            # 別sessionで共有生成枠を保持し、対象の警告がモデル未実行であることを保証する。
            # 取消を二度繰り返しても、再開時の現在観測だけで再予約できる。
            for persists in (True, False):
                blocker = register(base, preview=False)
                sid = register(base, preview=False)
                entered.clear(); release.clear(); control["hazard_gate"] = True
                try:
                    send(blocker, lava)
                    assert entered.wait(5), "blocker did not hold generation"
                    count = len(requests)
                    for attempt in range(2):
                        send(sid, lava)
                        pending = wait_for(lambda: next(iter(rows(sid)[attempt:]), None), timeout=3)
                        assert len(requests) == count, "queued hazard reached the model"
                        send(sid, lava, paused=True)
                        wait_for(lambda: row(base, pending["turn_id"], {"cancelled"}))
                        assert len(requests) == count, "cancelled hazard reached the model"
                        assert rows(sid)[attempt].get("environment_reaction_outcome") is None
                finally:
                    release.set(); control["hazard_gate"] = False
                complete(blocker)
                request(base, "/api/v1/adapter-sessions/" + blocker, method="DELETE")
                send(sid, lava if persists else safe)
                if persists:
                    resumed = complete(sid, 2)
                    assert resumed["environment_reaction_outcome"] == "speak", resumed
                    assert len(requests) == count + 1 and len(rows(sid)) == 3
                    for _ in range(3):
                        send(sid, lava)
                    time.sleep(.15)
                    assert len(requests) == count + 1 and len(rows(sid)) == 3
                else:
                    time.sleep(.15)
                    assert len(requests) == count and len(rows(sid)) == 2
                    send(sid, lava)
                    complete(sid, 2)
                    assert len(requests) == count + 1
                request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
                passed.append("generation_wait_cancel_then_resume_" + ("continuing_hazard_once" if persists else "resolved_hazard_stays_quiet"))

            # 確定した沈黙は考慮済み。ポーズ／再開しても同じ危険を再生成しない。
            sid = register(base, preview=False)
            count = len(requests)
            control["hazard_silent"] = True
            try:
                send(sid, lava)
                silent = wait_for(lambda: next((r for r in rows(sid) if r["playback_status"] == "not_selected"), None))
                assert silent["environment_reaction_outcome"] == "silent", silent
                send(sid, lava, paused=True)
                for _ in range(3):
                    send(sid, lava)
                time.sleep(.15)
                assert len(requests) == count + 1 and len(rows(sid)) == 1
            finally:
                control["hazard_silent"] = False
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            passed.append("chosen_silence_is_not_regenerated_after_resume")

            # 生成を開始した機会は、途中取消でも従来どおり同じ継続中に再生成しない。
            sid = register(base, preview=False)
            count = len(requests)
            entered.clear(); release.clear(); control["hazard_gate"] = True
            try:
                send(sid, lava)
                assert entered.wait(5)
                pending = wait_for(lambda: next(iter(rows(sid)), None))
                send(sid, lava, paused=True)
                wait_for(lambda: row(base, pending["turn_id"], {"cancelled"}))
            finally:
                release.set(); control["hazard_gate"] = False
            for _ in range(3):
                send(sid, lava)
            time.sleep(.15)
            assert len(requests) == count + 1 and len(rows(sid)) == 1
            request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
            passed.append("generation_started_cancel_is_not_regenerated_after_resume")
    print(json.dumps({"passed": passed, "model": "mock", "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
