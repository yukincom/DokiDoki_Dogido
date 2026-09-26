#!/usr/bin/env python3
"""実HTTPと模擬モデル/TTSで一句の相談・検査・取消を確認する。所有プロセスは全回収。"""
import json
import re
import threading

from check_dialogue import request, register, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture, LINES


def step(text, action="explain", speech="葉の明るさと斧の黒さを並べた句やで。", checks=()):
    return {"action": action, "purpose": "finish_workshop" if action == "close_workshop" else
            "other" if action == "unrelated" else "understand_meaning", "confidence": .95,
            "evidence": text, "speech": speech if action in {"respond", "explain", "ask"} else "",
            "checks": list(checks), "close_after_action": False, "close_evidence": "", "findings": [],
            "line_reference": {"found": False, "concept_id": "unknown", "evidence": "", "confidence": 0.0},
            "line_proposal": {"found": False, "target_fragment": "", "replacement_text": "", "evidence": "", "confidence": 0.0}}


def install(control, fn):
    original = control["structured_handler"]
    calls = []
    def handler(incoming):
        if incoming["max_tokens"] != 420:
            return original(incoming)
        prompt = "\n".join(m["content"] for m in incoming["messages"])
        text = re.search(r"今回のプレイヤー発話（会話理解用）: ([^\n]+)", prompt).group(1)
        calls.append((text, prompt))
        return fn(text, prompt, len(calls))
    control["structured_handler"] = handler
    return calls


def ready(base, send, rows):
    sid = register(base, preview=False)
    send(sid)
    wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
    return sid


def session(base, sid):
    return next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)


def main():
    passed = []
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        before = stored(sid)
        turn = submit(base, sid, "この句はどういう意味？")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "explain", (result, log.read_text())
        assert len(calls) == 1
        assert session(base, sid)["history"] == []
        assert len(session(base, sid)["workshop_history"]) == 1
        send(sid)
        turn2 = submit(base, sid, "その黒さは何を表してる？")
        wait_for(lambda: row(base, turn2, {"completed"}))
        assert "葉の明るさと斧の黒さ" in calls[-1][1]
        assert stored(sid) == before and hud(sid)["canonical_lines"] == LINES
        passed.append("one_step_meaning_and_completed_separate_history_without_mutation")

        def inspect(text, prompt, n):
            if "段階: decide" in prompt:
                return step(text, "inspect", checks=["meter", "reading", "source"])
            assert '"mora_count": 5' in prompt and '"mora_count": 7' in prompt
            assert '"source_status": "recorded"' in prompt
            return step(text, speech="音数は五・七・五やで。三行とも出典の記録があるわ。")
        calls2 = install(control, inspect)
        send(sid)
        turn = submit(base, sid, "音数と読みと出典を確認して")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "explain" and len(calls2) == 2, result
        assert [s["action"] for s in result["workshop_steps"]] == ["inspect", "explain"]
        assert stored(sid) == before
        passed.append("native_inspection_then_one_reply_with_recorded_sources")

        calls3 = install(control, lambda text, prompt, n: step(text, speech="保存したで。"))
        send(sid)
        turn = submit(base, sid, "どういう意味なの？")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_reason"] == "false_persistence_claim" and len(calls3) == 1, result
        assert "保存した" not in result["text"] and stored(sid) == before
        passed.append("false_save_claim_rejected_without_extra_model_loop")

        calls4 = install(control, lambda text, prompt, n: step("相談はここでおしまい", "close_workshop"))
        send(sid)
        turn = submit(base, sid, "今日は相談はここでおしまいにしよう")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "close_workshop" and len(calls4) == 1, result
        assert hud(sid)["state"] == "closed", hud(sid)
        passed.append("natural_close_applied_once_after_original_evidence_check")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step("終了", "close_workshop"))
        sid = ready(base, send, rows)
        for text in ["終了しないよ", "『終了』ってどういう意味？", "終了したらどうなる？", "終了かな？"]:
            send(sid)
            turn = submit(base, sid, text)
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_action"] == "fallback" and hud(sid)["state"] == "open", (text, result)
        assert len(calls) == 4
        passed.append("negation_quotation_condition_question_never_close")
        send(sid)
        turn = submit(base, sid, "終了でいいよ")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "close_workshop" and len(calls) == 4
        assert hud(sid)["state"] == "closed"
        passed.append("fixed_close_uses_no_model")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: {"action": "explain"} if n == 1 else step(text))
        sid = ready(base, send, rows)
        turn = submit(base, sid, "この句を説明して")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "explain" and len(calls) == 2
        passed.append("schema_retry_once_then_accepted")

        calls2 = install(control, lambda text, prompt, n: step(text, "inspect", checks=["meter"]))
        send(sid)
        turn = submit(base, sid, "音数を確認して")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert len(calls2) == 3 and result["workshop_action"] == "fallback", result
        assert result["text"].startswith("音数は上から5・7・5"), result
        passed.append("inspection_not_repeated_after_replan_failure")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text, speech="失敗した返答の印やで。" if n == 1 else "次の説明やで。"))
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("fail")
        turn = submit(base, sid, "一句を説明して")
        wait_for(lambda: row(base, turn, {"failed"}))
        assert not session(base, sid)["workshop_history"]
        (folder / "player_mode").write_text("ok")
        send(sid)
        turn = submit(base, sid, "もう少し説明して")
        wait_for(lambda: row(base, turn, {"completed"}))
        assert "失敗した返答の印" not in calls[-1][1]
        passed.append("failed_audio_excluded_from_next_workshop_prompt")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        entered = threading.Event(); release = threading.Event()
        def blocking(text, prompt, n):
            if n == 1:
                entered.set(); release.wait(timeout=8)
            return step(text, speech="古い返答やで。" if n == 1 else "新しい返答やで。")
        calls = install(control, blocking)
        sid = ready(base, send, rows)
        try:
            old = submit(base, sid, "一句を説明して")
            wait_for(entered.is_set)
            duplicate = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": "一句を説明して"})
            assert duplicate["deduplicated"] and duplicate["turn_id"] == old
            new = submit(base, sid, "下の行を説明して")
            wait_for(lambda: row(base, old, {"cancelled"}))
            release.set()
            wait_for(lambda: row(base, new, {"completed"}))
            history = session(base, sid)["workshop_history"]
            assert len(history) == 1 and history[0]["turn_id"] == new, history
            assert len(calls) == 2
            passed.append("duplicate_no_new_call_and_new_input_cancels_old_result")
        finally:
            release.set()

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        turn = submit(base, sid, "この句を説明して")
        wait_for(lambda: row(base, turn, {"started"}))
        before = stored(sid)
        send(sid, event={"name": "hostile_audio_detected", "source_kind": "auditory", "priority_hint": "background", "certainty": "high"},
             auditory_threats=[{"label": "zombie", "source_id": "sound-z", "spoken_name_allowed": True}])
        wait_for(lambda: row(base, turn, {"cancelled"}))
        assert not session(base, sid)["workshop_history"] and hud(sid)["state"] == "danger"
        assert stored(sid) == before
        passed.append("hostile_interrupt_keeps_poem_and_excludes_partial_explanation")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text, "unrelated"))
        sid = ready(base, send, rows)
        wid = hud(sid)["workshop_id"]
        before = stored(sid)
        for text in ["今日は楽しかったね", "明日も一緒に遊ぼうね"]:
            send(sid)
            turn = submit(base, sid, text)
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_action"] == "unrelated" and result["text"] == control["leaf"]
            assert len(result["llm_reports"]) == 3  # workshop + ordinary planner + leaf
            if len(calls) == 1:
                assert hud(sid)["workshop_id"] == wid and hud(sid)["state"] == "open"
        assert hud(sid)["state"] == "closed" and len(calls) == 2
        assert not session(base, sid)["workshop_history"]
        assert len(session(base, sid)["history"]) == 4 and stored(sid) == before
        passed.append("unrelated_input_handed_to_casual_once_and_closes_after_two_completed_replies")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text, "accept_pending"))
        sid = ready(base, send, rows)
        before = stored(sid)
        turn = submit(base, sid, "その案でお願い")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "fallback" and len(calls) == 2
        assert hud(sid)["state"] == "open" and stored(sid) == before
        passed.append("unavailable_edit_action_cannot_change_or_save_canonical_verse")

    with fixture(workshop_idle_ms=2500) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        entered = threading.Event(); release = threading.Event()
        def delayed_close(text, prompt, n):
            entered.set(); release.wait(timeout=6)
            return step(text, "close_workshop")
        calls = install(control, delayed_close)
        sid = ready(base, send, rows)
        try:
            turn = submit(base, sid, "そろそろ相談はおしまいにしよう")
            wait_for(entered.is_set)
            wait_for(lambda: row(base, turn, {"cancelled"}), timeout=4)
            release.set()
            assert not session(base, sid)["workshop_history"]
            assert hud(sid)["state"] == "closed"
            passed.append("expired_workshop_rejects_late_close_and_speech")
        finally:
            release.set()

    print(json.dumps({"passed": len(passed), "cases": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
