#!/usr/bin/env python3
"""既存HTTP snapshotの発話注視を、模擬LLM/TTS/playerで確認する。常駐serviceは操作しない。"""
from datetime import datetime, timezone
from pathlib import Path
import itertools
import json
import tempfile
import threading

from body_senses_fixture import attach_body
from check_dialogue import dependencies, register, request, row, running, snapshot, submit_text, wait_for

ROOT = Path(__file__).resolve().parents[1]
ENTITY = "44444444-4444-4444-8444-444444444444"


def main():
    passed = []
    with tempfile.TemporaryDirectory(prefix="dogido-gaze-") as tmp, dependencies() as (dep, control, _):
        directory = Path(tmp)
        (directory / "player_mode").write_text("slow")
        with running(ROOT / "target/debug/dogido-rust", directory, dep,
                     haiku_settings={"enabled": False, "memory_enabled": False}) as (base, _, _):
            sid = register(base, preview=False, voice_enabled=True, body=True)
            counter = itertools.count(1)
            senses = {"passive_mobs": [{"type": "cow", "distance": 3, "identity": {"entity_id": ENTITY}}]}

            def send():
                e = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": next(counter),
                     "observed_at": datetime.now(timezone.utc).isoformat(),
                     "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                     "player": {"dimension": "minecraft:overworld", "health": 20},
                     "world": {"time_phase": "day", "biome": "plains", "sky_visible": True, "danger_darkness_score": 0},
                     "combat": {"combat_active_hint": False}}
                return request(base, "/api/v1/game-events", attach_body(e, senses=senses), sid=sid)

            stop = threading.Event()
            failures = []

            def heartbeat():
                try:
                    while not stop.wait(.4):
                        send()
                except BaseException as error:
                    failures.append(error)

            def gaze():
                sessions = snapshot(base)["sessions"]
                return next(s for s in sessions if s["session_id"] == sid)["body_gaze"]

            send()
            worker = threading.Thread(target=heartbeat)
            worker.start()
            try:
                for text, expected in [("ウシがおるな。", {"kind": "entity", "id": ENTITY, "observer": "body"}),
                                       ("ウシの声がするな。", {"kind": "sound", "id": "heard-cow", "observer": "body"})]:
                    control["leaf"] = text
                    turn = submit_text(base, sid, "今日の調子はどう")
                    wait_for(lambda: row(base, turn, {"started"}))
                    found = wait_for(gaze)
                    assert found["targets"][0] == expected, found
                    assert found["context"]["owner_id"] == "11111111-1111-4111-8111-111111111111", found
                    wait_for(lambda: row(base, turn, {"completed"}))
                    assert gaze() is None
                    passed.append("started_and_completed_" + expected["kind"])
                    senses = {"passive_mobs": [{"type": "cow", "distance": 3, "identity": {"entity_id": ENTITY}}], "ambient_sounds": [{"type": "cow", "source_id": "heard-cow",
                                                  "sound_event": "entity.cow.ambient", "heard_ago_ms": 0}]}
                    send()
                control["leaf"] = "ウシの声がするな。"
                turn = submit_text(base, sid, "もう一度お話しして")
                wait_for(lambda: row(base, turn, {"started"}))
                assert wait_for(gaze)["targets"][0]["kind"] == "sound"
                request(base, "/api/v1/rust-dialogue/voice", {"session_id": sid, "enabled": False})
                wait_for(lambda: row(base, turn, {"cancelled"}))
                assert gaze() is None
                passed.append("voice_off_cancellation_releases_gaze")
            finally:
                stop.set()
                worker.join(timeout=3)
                assert not worker.is_alive()
                assert not failures, failures
    print(json.dumps({"phase": "body_speech_gaze", "passed": passed, "count": len(passed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
