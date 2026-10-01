#!/usr/bin/env python3
"""All workshop routing families write result facts; mock HTTP/audio only."""

import json
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, step
from check_workshop_edits import edit
from check_workshop_provisional import classifier, intent, threatening, done
from check_workshop_combat import SETTINGS


def records(folder, sid):
    path = folder / "memory/sessions" / sid / "long_term/haiku_workshop_turns.jsonl"
    if not path.exists():
        return []
    # Concurrent append may expose the final incomplete line; retry that line later.
    return [
        json.loads(line)
        for line in path.read_bytes().splitlines(keepends=True)
        if line.endswith(b"\n")
    ]


def finished(folder, sid, turn):
    return wait_for(
        lambda: next(
            (
                r
                for r in records(folder, sid)
                if r["event_kind"] == "turn_result" and r["turn_id"] == turn
            ),
            None,
        )
    )


def main():
    count = 0
    with fixture() as (
        base,
        p,
        log,
        control,
        seen,
        gate,
        drafting,
        checks,
        send,
        hud,
        rows,
        stored,
        folder,
    ):
        calls = install(
            control,
            lambda text, prompt, n: (
                edit(text, replacement="さくら") if text == "上五を『さくら』にして" else step(text)
            ),
        )
        sid = ready(base, send, rows)
        for text, expected in [
            ("今の句", "show_current"),
            ("この句はどういう意味？", "explain"),
            ("上五を『さくら』にして", "stage_player_edit"),
            ("くろいをしろいに変えて", "stage_player_edit"),
            ("しろいをくろいに変えて", "stage_player_edit"),
            ("読み: 草地=くさち", "fallback"),  # Reading registration is a separate typed form.
        ]:
            send(sid)
            turn = submit(base, sid, text)
            r = finished(folder, sid, turn)
            assert (
                r["result"].get("workshop_action", r["result"].get("memory_action")) == expected
            ), (text, r, log.read_text())
            assert r["result"]["playback_status"] == "completed", r
            assert r["player_text"] == text and r["workshop_id"], r
            assert not any(k in r["result"] for k in ["text", "spoken_text", "llm_reports"])
            assert (
                len(
                    [
                        v
                        for v in records(folder, sid)
                        if v["event_kind"] == "turn_result" and v["turn_id"] == turn
                    ]
                )
                == 1
            )
            if text == "上五を『さくら』にして":
                assert "meter_not_exact" in str(r["steps"]), r
            if text == "くろいをしろいに変えて":
                assert r["result"]["workshop_outcome"] == "player_edit_saved", r
                assert r["base_verse"] == "\n".join(LINES) and r["canonical_after"].splitlines()[1] == "しろいおのへと", r
                assert not r["pending_after"], r
            count += 1
        install(control, lambda text, prompt, n: step(text, speech="保存したで。"))
        send(sid)
        turn = submit(base, sid, "意味を教えて")
        r = finished(folder, sid, turn)
        assert (
            r["result"]["workshop_action"] == "fallback"
            and r["result"]["workshop_reason"] == "false_persistence_claim"
        ), r
        count += 1
        install(control, lambda text, prompt, n: edit(text))
        revision_path = folder / "memory/sessions" / sid / "long_term/haiku_revisions.jsonl"
        revision_path.rename(revision_path.with_suffix(".backup"))
        revision_path.mkdir()
        send(sid)
        turn = submit(base, sid, "上五を『さくらいろ』にして")
        r = finished(folder, sid, turn)
        assert r["result"]["workshop_outcome"] == "pending_save_failed", r
        assert r["base_verse"] == r["canonical_after"] and not r["pending_before"] and r["pending_after"],r
        revision_path.rmdir()
        revision_path.with_suffix(".backup").rename(revision_path)
        send(sid)
        turn = submit(base, sid, "却下して")
        r = finished(folder, sid, turn)
        assert r["result"]["workshop_action"] == "reject_pending" and not r["pending_after"],r
        # Retrying a failed explicit edit records adoption independently of speech.
        revision_path.rename(revision_path.with_suffix(".backup"))
        revision_path.mkdir()
        send(sid)
        turn=submit(base,sid,"上五を『さくらいろ』にして")
        assert finished(folder,sid,turn)["pending_after"]
        revision_path.rmdir()
        revision_path.with_suffix(".backup").rename(revision_path)
        send(sid)
        turn=submit(base,sid,"採用して")
        r=finished(folder,sid,turn)
        assert r["result"]["workshop_action"]=="accept_pending" and r["result"]["workshop_outcome"]=="pending_saved",r
        install(control, lambda text, prompt, n: step(text))
        count += 1
        # Failed speech is not a failed edit: decision and playback have separate records.
        (folder / "player_mode").write_text("fail")
        send(sid)
        turn = submit(base, sid, "今の句")
        r = finished(folder, sid, turn)
        assert r["result"]["playback_status"] == "failed", r
        assert any(
            v["event_kind"] == "decision" and v["turn_id"] == turn for v in records(folder, sid)
        )
        count += 1
        (folder / "player_mode").write_text("slow")
        send(sid)
        old = submit(base, sid, "この句はどういう意味？")
        wait_for(lambda: row(base, old, {"started"}))
        duplicate = submit(base, sid, "この句はどういう意味？")
        assert duplicate == old
        (folder / "player_mode").write_text("ok")
        send(sid)
        close = submit(base, sid, "終了でいいよ")
        old_record = finished(folder, sid, old)
        assert old_record["result"]["playback_status"] == "cancelled", old_record
        r = finished(folder, sid, close)
        assert r["state_before"]["open"] and not r["state_after"]["open"], r
        assert (
            len(
                [
                    v
                    for v in records(folder, sid)
                    if v["event_kind"] == "input_admission" and v["turn_id"] == old
                ]
            )
            == 1
        )
        count += 2

    with fixture(combat_settings=SETTINGS) as (
        base,
        p,
        log,
        control,
        seen,
        gate,
        drafting,
        checks,
        send,
        hud,
        rows,
        stored,
        folder,
    ):
        classifier(control, lambda text: intent(text, "uncertain", 0))
        sid = ready(base, send, rows)
        threatening(send, sid)
        turn = submit(base, sid, "うん")
        done(base, send, sid, turn)
        r = finished(folder, sid, turn)
        assert r["result"]["combat_action"] == "uncertain", r
        assert r["state_before"]["combat_paused"], r
        got = request(
            base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "終了"}
        )
        assert got["reason"] == "combat_workshop_closed", got
        wait_for(
            lambda: any(
                r["event_kind"] == "input_admission"
                and r["result"].get("reason") == "combat_workshop_closed"
                and not r["state_after"]["open"]
                for r in records(folder, sid)
            )
        )
        count += 2

    with fixture() as (
        base,
        p,
        log,
        control,
        seen,
        gate,
        drafting,
        checks,
        send,
        hud,
        rows,
        stored,
        folder,
    ):
        sid = ready(base, send, rows)
        # Filesystem failure is diagnostic, not a lost reply or changed canonical poem.
        path = folder / "memory/sessions" / sid / "long_term/haiku_workshop_turns.jsonl"
        wait_for(path.exists)
        path.rename(path.with_suffix(".backup"))
        path.mkdir()
        turn = submit(base, sid, "今の句")
        assert wait_for(lambda: row(base, turn, {"completed"}))
        assert hud(sid)["canonical_lines"] == LINES
        wait_for(lambda: "workshop_record_failed" in log.read_text())
        count += 1
    with fixture(memory_enabled=False) as (
        base,
        p,
        log,
        control,
        seen,
        gate,
        drafting,
        checks,
        send,
        hud,
        rows,
        stored,
        folder,
    ):
        sid = ready(base, send, rows)
        turn = submit(base, sid, "今の句")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert not records(folder, sid)
        count += 1
    print(f"workshop records: {count} route/outcome checks passed; owned processes stopped")


if __name__ == "__main__":
    main()
