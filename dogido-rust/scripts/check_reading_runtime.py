#!/usr/bin/env python3
"""Reading corrections: real Rust HTTP/storage, mocked inference/audio, no live services."""
import fcntl
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.catalog_readings import configure_corrections_path, resolve_reading, overlay_forbidden_readings
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import ready, install, step, session
from check_workshop_edits import edit, finish, revisions
from check_dialogue import request, register, submit, row, wait_for


def corrections(folder):
    path = folder / "memory/long_term/catalog_corrections.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def main():
    passed = []
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        original = stored(sid)
        calls = install(control, lambda text, prompt, n: edit(text))
        finish(base, send, sid, "上五を『さくらいろ』にして")
        before = hud(sid)
        for text in ["読み: 草地=くさち", "サクラの葉の読みはさくらのは", "サクラの葉はさくらのは"]:
            r = finish(base, send, sid, text)
            assert r.get("memory_outcome") == "saved" and not r["llm_reports"], (text, r)
        after = hud(sid)
        for key in ["canonical_lines", "pending_lines", "editing", "state"]:
            assert after[key] == before[key], (key, before, after)
        assert stored(sid) == original and not revisions(folder, sid) and len(calls) == 1
        passed.append("explicit_and_current_label_readings_save_without_model_or_verse_changes")
        install(control, lambda text, prompt, n: step(text, "ask", "どの行を変えるか、教えてな。"))
        for text in ["上五はさくらいろ", "草地はくさち", "『そうちじゃなくてくさち』と言った", "草地の読みはくさちではない"]:
            r = finish(base, send, sid, text)
            assert not r.get("memory_action") and len(corrections(folder)) == 3, (text, r)
        passed.append("unrelated_shorthand_quoted_and_negative_input_do_not_write")
        # Existing Python must be able to load the exact persisted format.
        configure_corrections_path(folder / "memory/long_term/catalog_corrections.jsonl")
        try:
            assert resolve_reading("草地") == "くさち"
        finally:
            configure_corrections_path(None)
        passed.append("native_jsonl_is_readable_by_existing_python")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        send(sid, world={"biome": "minecraft:meadow", "time_phase": "morning", "weather": "clear", "sky_visible": True})
        turn = submit(base, sid, "そうちじゃなくてくさち")
        r = wait_for(lambda: row(base, turn, {"completed"}))
        assert r["memory_outcome"] == "saved" and not r["llm_reports"], r
        c = corrections(folder)[0]
        assert c["surface"] == "草地" and c["reading"] == "くさち" and c["forbidden_readings"] == ["そうち"]
        configure_corrections_path(folder / "memory/long_term/catalog_corrections.jsonl")
        try:
            assert overlay_forbidden_readings("草地") == ("そうち",)
        finally:
            configure_corrections_path(None)
        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
        next_sid = register(base, preview=False)
        send(next_sid, world={"biome": "minecraft:meadow", "time_phase": "morning", "weather": "clear", "sky_visible": True})
        entry = wait_for(lambda: stored(next_sid))[0]
        constraints = entry["materials_snapshot"]["haiku_constraints"]
        assert "くさち" in constraints["allowed_terms"] and "そうち" in constraints["forbidden_terms"], constraints
        assert not constraints.get("player_lessons"), constraints
        passed.append("reconnect_loads_saved_reading_and_forbidden_misreading_into_next_poem")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        finish(base, send, sid, "終了")
        r = finish(base, send, sid, "草地はくさち")
        assert r.get("memory_outcome") == "saved" and not r["llm_reports"] and hud(sid)["state"] == "closed", r
        assert session(base, sid)["history"] == [], session(base, sid)
        passed.append("reading_outside_workshop_does_not_reopen_pin")

    with fixture(memory_enabled=False) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        r = finish(base, send, sid, "読み: 草地=くさち")
        assert r.get("memory_outcome") == "memory_disabled" and not corrections(folder) and "覚え直した" not in r["text"], r
        passed.append("disabled_memory_never_claims_to_remember")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        path = folder / "memory/long_term/catalog_corrections.jsonl"
        path.mkdir(parents=True)
        r = finish(base, send, sid, "読み: 草地=くさち")
        assert r.get("memory_outcome") == "save_failed" and "覚え直した" not in r["text"], r
        assert hud(sid)["canonical_lines"] == LINES and hud(sid)["state"] == "open"
        r = finish(base, send, sid, "今の句")
        assert r["text"] == "\n".join(LINES), r
        path.rmdir()
        r = finish(base, send, sid, "読み: 草地=くさち")
        assert r.get("memory_outcome") == "saved" and len(corrections(folder)) == 1, r
        passed.append("write_failure_keeps_workshop_and_retry_saves_once")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        path = folder / "memory/long_term/catalog_corrections.jsonl"
        path.parent.mkdir(parents=True)
        with path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                turn = submit(base, sid, "読み: 草地=くさち")
                wait_for(lambda: 'event="reading_correction_save_started"' in log.read_text())
                assert hud(sid)["state"] == "open"
                newer = submit(base, sid, "今の句")
                wait_for(lambda: row(base, turn, {"cancelled"}))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        r = wait_for(lambda: row(base, newer, {"completed"}))
        assert r["text"] == "\n".join(LINES) and len(corrections(folder)) == 1, r
        assert not any(p["turn_id"] == turn for p in session(base, sid)["workshop_history"])
        passed.append("authorized_save_finishes_after_audio_cancel_without_blocking_snapshot")
    print(json.dumps({"passed": passed, "live_model": False, "owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
