#!/usr/bin/env python3
"""Whole-poem archive/CAS/save failures with mock HTTP/audio and owned children."""
import fcntl
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.memory import MemoryStore
from check_dialogue import register, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import ready, install, step, session
from check_workshop_edits import edit, finish, revisions

TEXT = "はるのいろ/さくらのはみる/あさひかる"


def direct(base, sid, text):
    turn = submit(base, sid, text)
    return wait_for(lambda: row(base, turn, {"completed", "failed"}))


def main():
    passed = []
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        original = wait_for(lambda: stored(sid))
        calls = install(control, lambda text, prompt, n: edit(text))
        finish(base, send, sid, "上五を『さくらいろ』にして")
        pending = hud(sid)["pending_lines"]
        r = finish(base, send, sid, "今の句を保存して")
        assert r.get("memory_outcome") == "already_saved" and not r["llm_reports"], r
        assert stored(sid) == original and not revisions(folder, sid)
        assert hud(sid)["pending_lines"] == pending and hud(sid)["state"] == "open"
        passed.append("save_last_deduplicates_original_without_adopting_pending")
        r = finish(base, send, sid, "直し: " + TEXT)
        assert r.get("memory_outcome") == "saved" and not r["llm_reports"] and len(calls) == 1, (r, log.read_text())
        saved = revisions(folder, sid)
        assert len(saved) == 1 and saved[0]["source"] == "formal" and saved[0]["parent_revision_id"] is None
        assert saved[0]["revised_text"] == TEXT.replace("/", "\n") and saved[0]["base_text"] == "\n".join(LINES)
        assert all(line["provenance"] == "formal" and not line["source_atom_ids"] for line in saved[0]["lines"])
        assert stored(sid) == original and hud(sid)["state"] == "closed" and not session(base, sid)["history"]
        old = MemoryStore(folder / "memory/sessions" / sid)
        assert old._read_jsonl(old.haiku_revisions_path) == saved
        passed.append("formal_full_replacement_saves_literal_input_then_closes_and_python_reads_it")
        r = direct(base, sid, "直し: はる/さくら/あさ")
        latest = revisions(folder, sid)[-1]
        assert r.get("memory_outcome") == "saved" and latest["parent_revision_id"] == saved[0]["id"], r
        assert latest["base_text"] == saved[0]["revised_text"] and latest["revised_text"] == "はる\nさくら\nあさ"
        assert hud(sid)["state"] == "closed"
        passed.append("closed_original_remains_addressable_and_authored_meter_is_not_rewritten")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        install(control, lambda text, prompt, n: edit(text))
        finish(base, send, sid, "上五を『さくらいろ』にして")
        finish(base, send, sid, "採用して")
        first = revisions(folder, sid)[0]
        r = finish(base, send, sid, "こう直して: " + TEXT)
        saved = revisions(folder, sid)[-1]
        assert r.get("memory_outcome") == "saved" and not r["llm_reports"], r
        assert saved["source"] == "conversational" and saved["base_text"] == first["revised_text"] and saved["parent_revision_id"] == first["id"]
        assert hud(sid)["state"] == "closed"
        passed.append("conversational_full_verse_follows_last_accepted_revision")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        calls = install(control, lambda text, prompt, n: edit(text))
        finish(base, send, sid, "上五を『さくらいろ』にして")
        pending = hud(sid)["pending_lines"]
        path = folder / "memory/sessions" / sid / "long_term/haiku_revisions.jsonl"
        path.mkdir()
        r = finish(base, send, sid, "直し: " + TEXT)
        assert r.get("memory_outcome") == "save_failed" and not r["llm_reports"], r
        assert hud(sid)["pending_lines"] == pending and hud(sid)["canonical_lines"] == LINES and hud(sid)["state"] == "open"
        path.rmdir()
        r = finish(base, send, sid, "直し: 一/二/三/四")
        assert r.get("memory_outcome") == "invalid_input" and not revisions(folder, sid) and len(calls) == 1, r
        assert hud(sid)["pending_lines"] == pending
        passed.append("failed_or_malformed_save_preserves_canonical_and_pending")

    with fixture(memory_enabled=False) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        for text in ["直し: " + TEXT, "川柳保存: " + TEXT, "今の句を保存して"]:
            r = finish(base, send, sid, text)
            assert r.get("memory_outcome") == "memory_disabled" and not r["llm_reports"] and hud(sid)["state"] == "open", r
        assert not stored(sid) and not revisions(folder, sid)
        passed.append("memory_disabled_cannot_claim_save_or_close_workshop")

    with fixture(enabled=False) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = register(base, preview=False); send(sid)
        for text in ["直し: " + TEXT, "今の句を保存して"]:
            r = direct(base, sid, text)
            assert r.get("memory_outcome") == "no_original" and not r["llm_reports"], r
        for text in ["川柳保存: " + TEXT, "川柳: 私の二つめの句"]:
            r = direct(base, sid, text)
            assert r.get("memory_outcome") == "saved" and not r["llm_reports"], r
        entries = stored(sid)
        assert len(entries) == 2 and all(e["author"] == "player" for e in entries)
        assert entries[0]["trigger"] == entries[1]["trigger"] and entries[0]["id"] != entries[1]["id"]
        assert not session(base, sid)["history"] and hud(sid)["state"] == "closed"
        old = MemoryStore(folder / "memory/sessions" / sid)
        assert old._read_jsonl(old.haiku_entries_path) == entries
        r = direct(base, sid, "覚えてる句を教えて")
        assert "私の二つめの句" in r["text"] and "はるのいろ" in r["text"], r
        passed.append("player_poems_save_without_original_using_distinct_turn_ids_and_are_recalled")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        calls = install(control, lambda text, prompt, n: step(text, "respond", "句の話をしとるんやな。"))
        for text in ["今の句を保存しないで", "『直し:上/中/下』って言っただけ", "こう直してと言われた/中/下"]:
            r = finish(base, send, sid, text)
            assert not r.get("memory_action") and not revisions(folder, sid), r
        assert len(calls) == 3 and hud(sid)["state"] == "open"
        passed.append("quoted_reported_or_negative_requests_do_not_take_native_save_path")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        path = folder / "memory/sessions" / sid / "long_term/haiku_revisions.jsonl"
        with path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                turn = submit(base, sid, "直し: " + TEXT)
                wait_for(lambda: 'event="poem_input_save_started"' in log.read_text())
                assert hud(sid)["state"] == "open" and not revisions(folder, sid)
                newer = submit(base, sid, "直し: なつ/そら/くも")
                wait_for(lambda: row(base, turn, {"cancelled"}))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        wait_for(lambda: row(base, newer, {"failed", "completed", "cancelled"}))
        saved = revisions(folder, sid)
        assert len(saved) == 1 and saved[0]["revised_text"] == TEXT.replace("/", "\n")
        assert hud(sid)["state"] == "closed" and not any(h["turn_id"] == turn for h in session(base, sid)["workshop_history"])
        passed.append("authorized_save_survives_cancel_without_blocking_snapshot_or_applying_stale_next_edit")
    print(json.dumps({"passed": passed, "live_model": False, "owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
