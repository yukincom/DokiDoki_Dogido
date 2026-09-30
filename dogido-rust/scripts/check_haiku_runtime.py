#!/usr/bin/env python3
"""自動川柳のHTTP/音声/保存境界を模擬モデルで検証。実サービス・マイクは使わない。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import fcntl
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading
import time

from check_dialogue import dependencies, running, register, request, snapshot, wait_for

ROOT = Path(__file__).resolve().parents[1]
LINES = ["さくらのは", "くろいおのへと", "あさのいろ"]
DESCRIPTION = "サクラの葉と黒い斧の対比。"


@contextmanager
def fixture(*, combat_settings=None, extra_args=(), **settings):
    with tempfile.TemporaryDirectory(prefix="dogido-haiku-runtime-") as temp, dependencies() as (dep, control, seen):
        folder = Path(temp)
        gate = threading.Event(); gate.set()
        drafting = threading.Event()
        checks = {"fail": False, "drafts": 0}
        def structured(incoming):
            temperature = incoming.get("temperature")
            if temperature == .15:
                return {"found": True, "kind": "juxtaposition", "description": DESCRIPTION,
                        "elements": ["サクラの葉", "ネザライトの斧"], "focus": ["サクラの葉"], "confidence": .95}
            if temperature == .2:
                return {"found": False}
            if temperature == .6:
                checks["drafts"] += 1
                drafting.set()
                gate.wait(timeout=15)
                return {"lines": LINES}
            if incoming["max_tokens"] == 512:
                if checks["fail"]:
                    return {"verdicts": {}}
                text = "\n".join(m["content"] for m in incoming["messages"])
                indices = [int(i) for i in re.findall(r"^- ([012]):", text, re.M)]
                numbers = list(dict.fromkeys(int(i) for i in re.findall(r"^- \[(\d+)\]", text, re.M)))
                assert indices and len(numbers) >= len(indices), text
                return {"verdicts": {str(i): "pass" for i in indices},
                        "assessments": [{"line_index": i, "atom_ids": [numbers[n]]} for n, i in enumerate(indices)],
                        "failure_reasons": {}}
            return None  # existing casual planner/leaf mock
        control["structured_handler"] = structured
        h = {"interval_ms": 0, "quiet_time_ms": 0, "memory_dir": str(folder / "memory"), "platform_ai": {"provider": "chat"}, **settings}
        try:
            binary = Path(os.environ.get("DOGIDO_RUST_BINARY", ROOT / "target/debug/dogido-rust"))
            with running(binary, folder, dep, haiku_settings=h, combat_settings=combat_settings, extra_args=extra_args) as (base, process, log):
                state = {"sequence": 0}
                def send(sid, **extra):
                    state["sequence"] += 1
                    e = {"schema_version": "2026-05-24", "adapter": "fixture", "sequence": state["sequence"],
                         "observed_at": datetime.now(timezone.utc).isoformat(),
                         "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                         "player": {"name": "試験", "dimension": "minecraft:overworld", "held_item": "minecraft:cherry_leaves"},
                         "world": {"biome": "minecraft:plains", "time_phase": "morning", "weather": "clear", "sky_visible": True, "ceiling_height": 20},
                         "inventory": {"minecraft:cherry_leaves": 1, "minecraft:netherite_axe": 1}, **extra}
                    return request(base, "/api/v1/game-events", e, sid=sid)
                def hud(sid): return request(base, "/api/v1/haiku-workshop/snapshot?session_id=" + sid)
                def rows(sid): return [r for r in snapshot(base)["utterances"] if r["session_id"] == sid]
                def stored(sid):
                    p = folder / "memory/sessions" / sid / "long_term/haiku_entries.jsonl"
                    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
                yield base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder
        finally:
            gate.set()


def main():
    passed = []
    with fixture(interval_ms=1800) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = register(base, preview=False); send(sid)
        time.sleep(.45)
        send(sid, world={"game_paused": True})
        def clock():
            return next(s["foreground"] for s in snapshot(base)["sessions"] if s["session_id"] == sid)
        paused = clock()["haiku_elapsed_ms"]
        assert 350 <= paused < 1000, paused
        for _ in range(8):
            time.sleep(.25); send(sid, world={"game_paused": True})
            assert clock()["haiku_elapsed_ms"] == paused and clock()["blocks_new_haiku"]
            assert not rows(sid) and not stored(sid) and checks["drafts"] == 0
        send(sid)
        time.sleep(.45); send(sid)
        assert checks["drafts"] == 0, "pause time counted toward the interval"
        wait_for(lambda: (send(sid), stored(sid))[1], timeout=4)
        assert len(stored(sid)) == 1
        passed.append("actual_pause_freezes_clock_across_snapshots_and_resumes_remaining_interval")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        gate.clear(); sid = register(base, preview=False); send(sid); wait_for(drafting.is_set)
        send(sid, world={"game_paused": True})
        wait_for(lambda: "haiku_helper_stopped" in log.read_text())
        gate.set()
        for _ in range(3):
            time.sleep(.2); send(sid, world={"game_paused": True})
        assert not stored(sid) and hud(sid)["state"] == "closed" and checks["drafts"] == 1
        send(sid)
        wait_for(lambda: stored(sid))
        assert len(stored(sid)) == 1 and checks["drafts"] == 2
        passed.append("pause_cancels_unfinished_generation_and_resume_can_retry_without_saved_partial")

    with fixture(workshop_idle_ms=1800) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        gate.clear()
        sid = register(base, preview=False); send(sid)
        wait_for(drafting.is_set)
        inspiration = next(r for r in rows(sid) if r["turn_id"].endswith(":inspiration"))
        assert inspiration["playback_status"] == "completed", inspiration
        assert DESCRIPTION.rstrip("。") in inspiration["text"] and "ちょっと待って" not in inspiration["text"]
        time.sleep(2.1)  # workshop idle lifetimeより長く生成を止めても句はまだ開かない
        first = hud(sid); assert first["state"] == "closed" and first["character_state"] == "thinking", first
        assert first == hud(sid), "GET changed HUD revision"
        assert not stored(sid)
        completed_after = datetime.now(timezone.utc)
        gate.set()
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
        shown = hud(sid); assert shown["state"] == "open" and shown["character_state"] == "normal", shown
        assert shown["canonical_lines"] == LINES, shown
        entry = wait_for(lambda: stored(sid))[0]
        assert datetime.fromisoformat(entry["created_at"]) >= completed_after
        assert entry["materials_snapshot"]["preface_spoken"] == inspiration["text"]
        assert len(stored(sid)) == 1
        assert len((folder / "memory/sessions" / sid / "short_term/current_session.jsonl").read_text().splitlines()) == 1
        assert snapshot(base)["sessions"][0]["history"] == [], "automatic poem entered casual history"
        wait_for(lambda: hud(sid)["state"] == "closed", timeout=4)
        assert checks["drafts"] == 1
        passed.extend(["generated_inspiration_played_before_draft", "completion_clock_and_read_only_hud", "autosave_once_and_no_fake_chat_history"])

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        gate.clear(); sid = register(base, preview=False); send(sid); wait_for(drafting.is_set)
        got = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "こんにちは"})
        assert got["accepted"], got
        wait_for(lambda: any(r["turn_id"] == got["turn_id"] and r["playback_status"] == "completed" for r in rows(sid)))
        gate.set()
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
        wait_for(lambda: "haiku_helper_stopped" in log.read_text())
        assert not stored(sid)
        passed.append("new_input_cancels_generation_without_losing_reply")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        gate.clear(); sid = register(base, preview=False); send(sid); wait_for(drafting.is_set)
        send(sid, event={"name": "hostile_audio_detected", "source_kind": "auditory", "priority_hint": "background", "certainty": "high"},
             auditory_threats=[{"label": "zombie", "source_id": "sound-z", "spoken_name_allowed": True}])
        wait_for(lambda: any(r["playback_status"] == "completed" and r.get("combat_actions") for r in rows(sid)))
        gate.set(); wait_for(lambda: "haiku_helper_stopped" in log.read_text())
        assert not stored(sid) and hud(sid)["state"] == "closed"
        passed.append("partial_hostile_audio_cancels_and_warning_precedes_late_model")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        checks["fail"] = True; sid = register(base, preview=False); send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":failure") and r["playback_status"] == "completed" for r in rows(sid)))
        assert not stored(sid) and hud(sid)["state"] == "closed" and checks["drafts"] == 1
        passed.append("unavailable_checker_never_saves_or_rewrites_poem")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        control["fail_sentence"] = "ここで一句。"
        sid = register(base, preview=False); send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "failed" for r in rows(sid)))
        assert len(wait_for(lambda: stored(sid))) == 1 and hud(sid)["state"] == "open"
        assert snapshot(base)["sessions"][0]["history"] == []
        got = request(base, "/api/v1/player-input", {"session_id": sid, "text": "終了", "source": "voice"})
        assert got["accepted"] and hud(sid)["state"] == "closed", got
        passed.append("completed_poem_survives_tts_failure_and_explicit_close")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        gate.clear(); sid = register(base, preview=False); send(sid); wait_for(drafting.is_set)
        # 新しい全観測が来ないまま10秒を超えた生成は採用しない。
        wait_for(lambda: "haiku_helper_stopped" in log.read_text(), timeout=12)
        assert not stored(sid) and hud(sid)["state"] == "closed"
        gate.set()
        passed.append("stale_observation_cancels_unfinished_poem")

    with fixture(job_timeout_ms=1400) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        gate.clear(); sid = register(base, preview=False); send(sid); wait_for(drafting.is_set)
        wait_for(lambda: "haiku_helper_stopped" in log.read_text())
        assert not stored(sid) and hud(sid)["state"] == "closed"
        gate.set()
        passed.append("job_deadline_reaps_helper_without_inference_completion")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = register(base, preview=False)
        path = folder / "memory/sessions" / sid / "long_term/haiku_entries.jsonl"
        path.parent.mkdir(parents=True)
        with path.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            try:
                send(sid)
                wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
                assert not stored(sid), "fixture must actually block the append"
                assert hud(sid)["state"] == "open"
                send(sid, visual_threats=[{"type": "zombie", "entity_id": "z", "distance": 6,
                      "direction": {"horizontal": "front", "cardinal": "north"}}])
                wait_for(lambda: any(r.get("combat_actions") and r["playback_status"] == "completed" for r in rows(sid)))
                assert hud(sid)["state"] == "danger"
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)
        assert len(wait_for(lambda: stored(sid))) == 1
        passed.append("blocked_storage_never_holds_observation_or_warning_audio")

    for stop in (signal.SIGINT, signal.SIGTERM):
        with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
            gate.clear(); sid = register(base, preview=False); send(sid); wait_for(drafting.is_set)
            process.send_signal(stop); process.wait(timeout=5); gate.set()
            assert process.returncode == 0 and not stored(sid)
            assert "haiku_helper_stopped" in log.read_text()
            passed.append(stop.name + "_reaps_helper_without_save")

    report = ROOT / "reports/haiku-runtime-check.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({"passed": len(passed), "checks": passed}, ensure_ascii=False, indent=2) + "\n")
    print(f"haiku runtime: {len(passed)} passed; own helpers, players and mock listeners stopped")


if __name__ == "__main__":
    main()
