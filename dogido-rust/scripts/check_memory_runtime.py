#!/usr/bin/env python3
"""実HTTPで講評・soft lesson・想起・保存取消を検証する。分類・生成・音声は模擬し、記憶は一時領域だけへ保存する。"""
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import read_jsonl
from check_dialogue import request, register, submit, row, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import ready, install, step, session
from check_workshop_edits import adoption, finish, revisions
from check_workshop_followup import meaning_ack
from check_workshop_revision import wire


def records(folder, kind):
    path = folder / f"memory/long_term/haiku_{kind}.jsonl"
    return [json.loads(s) for s in path.read_text().splitlines()] if path.is_file() else []


def main():
    passed = []
    close_text = "今日はこの句の相談をここでおしまいにしよう"
    with fixture(bert_labels={close_text: "senryu_end"}) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def choose(text, prompt, n):
            if text == "そうなんだ":
                assert "会話段階: meaning_explained" in prompt
                return meaning_ack(text, "うん、もう少し話してもええで。")
            if text == "余韻が心に残る":
                result = step(text, "respond", "その余韻、うちも大事にしたいわ。")
                result["feedback"] = {"kind": "praise", "evidence": "余韻が心に残る"}
                return result
            if text == "今日の句を思い出して":
                return step(text, "respond", "いま開いてる句の話を続けよか。")
            assert text == "この句はどういう意味？", text
            return step(text)
        calls = install(control, choose)
        sid = ready(base, send, rows)
        original = stored(sid)
        r = finish(base, send, sid, "この句はどういう意味？")
        assert r.get("workshop_feedback_outcome") == "saved", (r, log.read_text())
        critiques, lessons = records(folder, "critiques"), records(folder, "lessons")
        assert critiques[0]["kind"] == "ask_meaning" and critiques[0]["entry_id"] == original[0]["id"]
        assert lessons[0]["lesson_type"] == "readability" and lessons[0]["from_critique_id"] == critiques[0]["id"]
        assert len(calls) == 1 and stored(sid) == original
        assert read_jsonl(folder / "memory/long_term/haiku_lessons.jsonl") == lessons
        assert read_jsonl(folder / "memory/long_term/haiku_critiques.jsonl") == critiques
        passed.append("validated_feedback_persists_as_jsonl_without_extra_inference")
        assert session(base, sid)["workshop_followup"] == "meaning_explained"
        r = finish(base, send, sid, "そうなんだ")
        assert r["workshop_action"] == "acknowledge_meaning" and len(r["llm_reports"]) == 1, r
        assert r["text"] == "うん、もう少し話してもええで。" and len(calls) == 2, r
        assert not r.get("workshop_feedback_outcome"), r
        assert records(folder, "critiques") == critiques and records(folder, "lessons") == lessons
        assert hud(sid)["state"] == "open" and hud(sid)["canonical_lines"] == LINES
        assert not hud(sid)["pending_lines"] and stored(sid) == original
        assert session(base, sid)["workshop_followup"] == "discussion"
        # 肯定的講評は会話モデルの返答から保存する。BERT終了とは別の入力で確認する。
        r = finish(base, send, sid, "余韻が心に残る")
        assert r["workshop_action"] == "respond" and len(calls) == 3, r
        assert hud(sid)["state"] == "open" and r.get("workshop_feedback_outcome") == "saved", r
        after_praise = records(folder, "critiques")
        assert len(after_praise) == 2 and after_praise[:-1] == critiques
        assert after_praise[-1]["kind"] == "praise" and records(folder, "lessons") == lessons
        passed.append("meaning_ack_adds_no_feedback_and_praise_does_not_remove_lessons")
        r = finish(base, send, sid, "余韻が心に残る。今日はここまでにしよう")
        assert r["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", r
        assert len(calls) == 3 and not r["llm_reports"] and not r.get("workshop_feedback_outcome"), r
        assert records(folder, "critiques") == after_praise and records(folder, "lessons") == lessons
        r = finish(base, send, sid, "覚えてる句を教えて")
        assert r.get("memory_outcome") == "read" and not r["llm_reports"], r
        assert " / ".join(LINES) in r["text"] and hud(sid)["state"] == "closed"
        common = session(base, sid)["history"]
        assert sum(p["turn_id"] == "memory:" + r["turn_id"] and r["text"] in p["text"] for p in common) == 1, common
        assert not any(p["turn_id"] == r["turn_id"] + ":reply" for p in common), common
        passed.append("completed_recall_is_shared_once_without_new_poem_material_or_model_call")

        request(base, "/api/v1/adapter-sessions/" + sid, method="DELETE")
        next_sid = ready(base, send, rows)
        entry = stored(next_sid)[0]
        constraints = entry["materials_snapshot"]["haiku_constraints"]
        assert lessons[0]["note"] in "\n".join(constraints["player_lessons"]), constraints
        assert not set(constraints.get("forbidden_terms", [])) & {lessons[0]["note"]}
        before = hud(next_sid)
        r = finish(base, send, next_sid, "今日の句を思い出して")
        assert r["workshop_action"] == "respond" and len(r["llm_reports"]) == 1, r
        assert not r.get("memory_action") and not r.get("memory_outcome"), r
        assert len(calls) == 4 and stored(next_sid) == [entry]
        assert hud(next_sid)["canonical_lines"] == before["canonical_lines"] and hud(next_sid)["state"] == "open"
        passed.append("reconnect_loads_soft_lesson_and_open_workshop_does_not_recall")
        r = finish(base, send, next_sid, close_text)
        assert r["workshop_action"] == "close_workshop" and hud(next_sid)["state"] == "closed", r
        assert len(calls) == 4 and not r["llm_reports"] and not r.get("memory_action") and stored(next_sid) == [entry]
        passed.append("today_and_poem_in_bert_close_request_do_not_recall_or_call_model")
        r = finish(base, send, next_sid, "前の注意はもういらん")
        assert r.get("memory_outcome") == "saved" and not r["llm_reports"], r
        cleared = read_jsonl(folder / "memory/long_term/haiku_lessons.jsonl")
        assert cleared[:-1] == lessons and cleared[-1]["polarity"] == "loosen", cleared
        assert cleared[-1]["lesson_type"] == "*", cleared
        request(base, "/api/v1/adapter-sessions/" + next_sid, method="DELETE")
        last_sid = ready(base, send, rows)
        assert not stored(last_sid)[0]["materials_snapshot"].get("haiku_constraints", {}).get("player_lessons")
        passed.append("explicit_loosen_survives_reconnect_and_removes_only_soft_advice")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        original = wait_for(lambda: stored(sid))
        # Player-authored edits now save immediately. A generated proposal is
        # the current path whose pending text must remain absent from recall.
        editor_calls, proposal_steps = wire(control, stored, sid)
        r = finish(base, send, sid, "上五のさくらのはを別の表現に直して")
        assert r.get("workshop_outcome") == "revision_proposed", r
        pending = hud(sid)["pending_lines"]
        assert pending == ["さくらいろ", *LINES[1:]] and hud(sid)["canonical_lines"] == LINES
        assert len(editor_calls) == 1 and len(proposal_steps) == 1
        assert stored(sid) == original and not revisions(folder, sid)
        def review_pending(text, prompt, n):
            if text == "採用して":
                return adoption(text)
            if text == "保存した句を思い出して":
                return step(text, "respond", "いまの句と案の話を続けよか。")
            assert text == "どういう意味？", text
            return step(text)
        review_calls = install(control, review_pending)
        previous_critiques = records(folder, "critiques")
        r = finish(base, send, sid, "どういう意味？")
        assert r.get("workshop_feedback_outcome") == "saved", r
        pending_critiques = records(folder, "critiques")
        assert len(pending_critiques) == len(previous_critiques) + 1
        assert pending_critiques[:-1] == previous_critiques
        assert pending_critiques[-1]["surface_at_time"] == "\n".join(pending)
        assert pending_critiques[-1]["kind"] == "ask_meaning"
        assert pending_critiques[-1]["entry_id"] == original[0]["id"]
        r = finish(base, send, sid, "保存した句を思い出して")
        assert r["workshop_action"] == "respond" and len(r["llm_reports"]) == 1, r
        assert not r.get("memory_action") and not r.get("memory_outcome"), r
        assert hud(sid)["pending_lines"] == pending and not revisions(folder, sid)
        assert hud(sid)["canonical_lines"] == LINES and stored(sid) == original and len(review_calls) == 2
        r = finish(base, send, sid, "採用して")
        saved = revisions(folder, sid)
        assert r.get("workshop_outcome") == "pending_saved" and len(saved) == 1, r
        assert saved[0]["source"] == "generated_confirmed" and saved[0]["revised_text"] == "\n".join(pending)
        assert saved[0]["base_text"] == "\n".join(LINES) and stored(sid) == original
        assert hud(sid)["canonical_lines"] == pending and not hud(sid)["pending_lines"]
        r = finish(base, send, sid, "終了")
        assert r["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", r
        assert len(review_calls) == 3 and not r["llm_reports"] and len(editor_calls) == 1 and len(proposal_steps) == 1
        r = finish(base, send, sid, "覚えてる句を教えて")
        assert r.get("memory_action") == "haiku_recall" and not r["llm_reports"], r
        assert "元「" in r["text"] and "直し「さくらいろ" in r["text"], r
        assert revisions(folder, sid) == saved and stored(sid) == original
        # A foreign session file within the configured root must also be searched.
        path = folder / "memory/sessions/earlier/long_term/haiku_entries.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"id":"older","text":"保存済みの雪原の句","created_at":datetime.now(timezone.utc).isoformat(),"world":{"biome":"snowy_plains"}},ensure_ascii=False)+"\n")
        r = finish(base, send, sid, "雪原の句を思い出して")
        assert "保存済みの雪原の句" in r["text"] and "さくらいろ" not in r["text"], r
        passed.append("pending_is_preserved_without_recall_and_closed_workshop_recalls_saved_entries_and_revisions")

    # Even when the workshop model hands the input to casual chat, its open
    # poem reference must keep the normal recall router disabled for this turn.
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        original = stored(sid)
        calls = install(control, lambda text, prompt, n: step(text, "unrelated"))
        r = finish(base, send, sid, "今日の句を思い出して")
        assert r["workshop_action"] == "unrelated" and r["text"] == control["leaf"], r
        assert len(calls) == 1 and not r.get("memory_action") and not r.get("memory_outcome"), r
        assert hud(sid)["state"] == "open" and hud(sid)["canonical_lines"] == LINES
        assert stored(sid) == original and not revisions(folder, sid)
        passed.append("casual_handoff_from_open_workshop_does_not_recall")

    with fixture(memory_enabled=False) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        install(control, lambda text, prompt, n: step(text))
        finish(base, send, sid, "どういう意味？")
        closed = finish(base, send, sid, "終了")
        assert closed["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", closed
        for text in ["気にせんで", "覚えてる句を教えて"]:
            r = finish(base, send, sid, text)
            assert r.get("memory_outcome") == "memory_disabled" and not r["llm_reports"], r
        assert not records(folder,"critiques") and not records(folder,"lessons")
        passed.append("disabled_memory_never_claims_to_save_or_recall")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        path = folder / "memory/long_term/haiku_lessons.jsonl"
        path.mkdir(parents=True)
        r = finish(base, send, sid, "緩めて")
        assert r.get("memory_outcome") == "save_failed" and hud(sid)["state"] == "open", r
        install(control, lambda text, prompt, n: step(text))
        r = finish(base, send, sid, "どういう意味？")
        assert r.get("workshop_feedback_outcome") == "save_failed" and r["workshop_action"] == "explain", r
        path.rmdir()
        entry_path = folder / "memory/long_term/haiku_entries.jsonl"
        entry_path.mkdir()
        closed = finish(base, send, sid, "終了")
        assert closed["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", closed
        r = finish(base, send, sid, "覚えてる句を教えて")
        assert r.get("memory_outcome") == "read_failed" and "読み出せん" in r["text"], r
        passed.append("io_failure_is_honest_and_auxiliary_feedback_failure_does_not_break_reply")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        calls = install(control, lambda text, prompt, n: step(text, "show_current"))
        path = folder / "memory/long_term/haiku_lessons.jsonl"
        path.parent.mkdir(parents=True)
        with path.open("a+") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                turn = submit(base,sid,"前の注意やめて")
                wait_for(lambda:'event="haiku_lessons_clear_started"' in log.read_text())
                assert hud(sid)["state"] == "open"
                newer = submit(base,sid,"今の句")
                wait_for(lambda:row(base,turn,{"cancelled"}))
            finally:
                fcntl.flock(lock,fcntl.LOCK_UN)
        result = wait_for(lambda:row(base,newer,{"completed"}))
        assert result["workshop_action"] == "show_current" and len(calls) == 1, result
        assert len(records(folder,"lessons")) == 1 and records(folder,"lessons")[0]["polarity"] == "loosen"
        assert not any(p["turn_id"] == turn for p in session(base,sid)["workshop_history"])
        passed.append("authorized_clear_finishes_after_cancellation_without_blocking_snapshot")
    print(json.dumps({"passed":passed,"count":len(passed),"live_model":False,"owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
