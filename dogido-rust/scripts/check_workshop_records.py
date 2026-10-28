#!/usr/bin/env python3
"""All workshop routing families write result facts; mock HTTP/audio only."""

import json
from check_dialogue import row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, step
from check_workshop_edits import edit, adoption
from check_workshop_provisional import done
from check_workshop_combat import SETTINGS, enter, end, returned, rejected_normal_input


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


def model(text, prompt, n):
    # 対象・具体変更・終了はBERTへ変更済み。本文モデルへ旧判定を戻さない。
    if text == "今の句":
        return step(text, "show_current")
    if text in {"採用して", "却下して"}:
        return adoption(text, "accept_pending" if text == "採用して" else "reject_pending")
    if text.startswith("上五を『"):
        return edit(text, replacement=text.split("『", 1)[1].split("』", 1)[0])
    if text in {"中七のくろいをしろいに変えて", "中七のしろいをくろいに変えて"}:
        old, new = text.removeprefix("中七の").removesuffix("に変えて").split("を")
        result = edit(text, index=1, reference="中七", replacement=new, fragment=old)
        return result
    if text == "検査方法を相談したい":
        return {"action": "inspect", "checks": ["unsupported"]}
    return step(text)


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
        calls = install(control, model)
        sid = ready(base, send, rows)
        for text, expected in [
            ("今の句", "show_current"),
            ("この句はどういう意味？", "explain"),
            ("上五を『さくら』にして", "stage_player_edit"),
            ("中七のくろいをしろいに変えて", "stage_player_edit"),
            ("中七のしろいをくろいに変えて", "stage_player_edit"),
            ("検査方法を相談したい", "fallback"),  # 不正な検査指定も理由とともに記録する。
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
                assert r["result"]["workshop_outcome"] == "player_edit_saved" and r["canonical_after"].splitlines()[0] == "さくら", r
            if text == "中七のくろいをしろいに変えて":
                assert r["result"]["workshop_outcome"] == "player_edit_saved", r
                assert r["base_verse"] == "\n".join(["さくら", *LINES[1:]]) and r["canonical_after"].splitlines()[1] == "しろいおのへと", r
                assert not r["pending_after"], r
            count += 1
        install(control, lambda text, prompt, n: step(text, speech=""))
        send(sid)
        turn = submit(base, sid, "意味を教えて")
        r = finished(folder, sid, turn)
        assert (
            r["result"]["workshop_action"] == "fallback"
            and r["result"]["workshop_reason"] == "invalid_speech"
        ), r
        count += 1
        install(control, model)
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
        install(control, model)
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
        earlier_admissions = {v["id"] for v in records(folder, sid) if v["event_kind"] == "input_admission"}
        old = submit(base, sid, "この句はどういう意味？")
        wait_for(lambda: row(base, old, {"started"}))
        duplicate = submit(base, sid, "この句はどういう意味？")
        assert duplicate == old
        (folder / "player_mode").write_text("ok")
        send(sid)
        close = submit(base, sid, "終了でいいよ")
        old_record = finished(folder, sid, old)
        assert old_record["result"]["playback_status"] == "completed", old_record
        r = finished(folder, sid, close)
        assert r["state_before"]["open"] and not r["state_after"]["open"], r
        # 最初の受付IDはBERT分類待ちの行。返答turnへの転送と重複抑止を別々に確認する。
        admissions = [v for v in records(folder, sid)
                      if v["event_kind"] == "input_admission" and v["id"] not in earlier_admissions
                      and v["player_text"] == "この句はどういう意味？"]
        assert len(admissions) == 1, admissions
        routing = next(v for v in rows(sid) if v["turn_id"] == admissions[0]["turn_id"])
        assert routing["forwarded_input"]["turn_id"] == old, routing
        assert len([v for v in records(folder, sid)
                    if v["event_kind"] == "forwarded_admission" and v["turn_id"] == old]) == 1
        assert len([v for v in records(folder, sid)
                    if v["event_kind"] == "turn_result" and v["turn_id"] == old]) == 1
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
        calls = install(control, model)
        sid = ready(base, send, rows)
        enter(send, rows, sid)
        # 中断後の新規入力は通常分類。川柳の保留・終了へ読み替えない。
        before = records(folder, sid)
        rejected = [rejected_normal_input(base, sid, text) for text in ["なんの話？", "終了"]]
        assert not calls and hud(sid)["state"] == "danger"
        # 通常分類の受付・拒否は記録するが、WSの操作・状態変更にはしない。
        for turn in rejected:
            result = finished(folder, sid, turn)
            assert result["state_before"] == result["state_after"], result
            assert result["state_after"]["combat_paused"] and result["state_after"]["open"], result
            assert result["result"]["playback_status"] == "not_selected", result
            assert result["result"]["forwarded_input"] == {"accepted": False, "reason": "fresh_safe_snapshot_required"}, result
            assert not result["result"].get("workshop_action"), result
        added = [v for v in records(folder, sid) if v["id"] not in {v["id"] for v in before}]
        assert not any(v["event_kind"] == "decision" for v in added), added
        end(send, rows, sid)
        returned(send, rows, sid)
        assert hud(sid)["state"] == "open" and not calls
        send(sid)
        turn = submit(base, sid, "この句はどういう意味？")
        resumed = finished(folder, sid, turn)
        assert resumed["result"]["workshop_action"] == "explain" and len(calls) == 1
        assert resumed["state_before"]["open"] and not resumed["state_before"]["combat_paused"]
        send(sid)
        closing = submit(base, sid, "終了")
        closed = finished(folder, sid, closing)
        assert closed["result"]["workshop_action"] == "close_workshop", closed
        assert closed["state_before"]["open"] and not closed["state_after"]["open"], closed
        assert len(calls) == 1
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
