#!/usr/bin/env python3
"""戦闘後の自動再開と通常分類の境界を、実HTTP・模擬モデル/再生で検査する。"""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
import time
from check_dialogue import register, request, row, submit, wait_for
from check_haiku_runtime import fixture as haiku_fixture, LINES
from request_classifier_fixture import mock_request_classifier
from check_workshop_runtime import ready, session, install, step
from check_workshop_edits import edit, finish, revisions, adoption

SETTINGS = {"aftermath_time_ms": 150, "combat_clear_time_ms": 150}
MOB = {"type": "zombie", "entity_id": "z1", "distance": 6, "direction": {"horizontal": "front"}}


# BERTの意味精度はここで代替しない。ケースが選んだ原文と分類結果を共通IPCへ渡す。
BERT_LABELS = {
    "終了": "senryu_end", "終了ー": "senryu_end", "お開きー": "senryu_end",
    "終了でお願いします": "senryu_end", "句の相談はお開きにしよう": "senryu_end",
    "上五を『さくらいろ』にして": "senryu_change_upper",
    "もういい": "senryu_end", "続けない": "senryu_end",
    "この句の相談はここで切り上げよう": "senryu_end",
}


@contextmanager
def fixture(*, extra_args=(), **settings):
    with mock_request_classifier(BERT_LABELS, extra_args=extra_args) as (args, classifier), haiku_fixture(extra_args=args, **settings) as state:
        state[3]["classifier_requests"] = classifier / "requests.jsonl"
        yield state


def classifier_requests(control):
    path = control["classifier_requests"]
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def classified_input(base, sid, text):
    """最初の受付行を返す。危険中の最終拒否も配送結果として検査する。"""
    response = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": text})
    assert response["accepted"], response
    turn = response["turn_id"]
    result = wait_for(lambda: next((r for r in request(base, "/api/v1/rust-dialogue/snapshot")["utterances"]
        if r["turn_id"] == turn and r.get("request_classification")), None))
    assert result["classifier_mode"] == "normal", result
    assert result["request_classification"]["prediction"]["label"] == "not_target", result
    return turn


def rejected_normal_input(base, sid, text):
    turn = classified_input(base, sid, text)
    def rejected():
        item = row(base, turn, {"not_selected"})
        return item if item and item.get("forwarded_input") else None
    result = wait_for(rejected)
    assert result["forwarded_input"] == {"accepted": False, "reason": "fresh_safe_snapshot_required"}, result
    return turn


def event(name):
    return {"name": name, "source_kind": "system", "priority_hint": "background", "certainty": "high"}


def actions(rows, sid, kind):
    return [r for r in rows(sid) if any(a["kind"] == kind for a in r.get("combat_actions", []))]


def drive(send, sid, condition, timeout=8):
    last = 0
    def poll():
        nonlocal last
        if time.monotonic() - last >= .10:
            send(sid)
            last = time.monotonic()
        return condition()
    return wait_for(poll, timeout=timeout)


def enter(send, rows, sid):
    send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
    wait_for(lambda: any(r["playback_status"] == "completed" and r.get("combat_actions") for r in rows(sid)))


def end(send, rows, sid, outcome=None):
    payload = [] if outcome is None else [{"entity_id": "z1", "type": "zombie", "outcome": outcome, "evidence": "server_death_event"}]
    send(sid, event=event("combat_ended"), combat={"hostile_outcomes": payload, "hostiles_within_scan_ground": 0})
    return wait_for(lambda: next((r for r in actions(rows, sid, "aftermath") if r["playback_status"] == "completed"), None))


def returned(send, rows, sid, count=1):
    return drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume")[count-1:] if r["playback_status"] == "completed"), None))


def main():
    passed = []
    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text,"respond","気に入ってもらえてうれしいわ。"))
        sid = ready(base, send, rows)
        r = finish(base, send, sid, "いい句だね")
        assert r["workshop_action"] == "respond" and len(calls) == 1 and hud(sid)["state"] == "open"
        assert hud(sid)["canonical_lines"] == LINES and not revisions(folder,sid)
        enter(send, rows, sid)
        rejected_normal_input(base, sid, "いい句だね")
        classified = classifier_requests(control)
        assert len(calls) == 1 and hud(sid)["state"] == "danger" and not revisions(folder,sid)
        end(send, rows, sid)
        returned(send, rows, sid)
        assert len(calls) == 1 and classifier_requests(control) == classified
        assert hud(sid)["state"] == "open" and not revisions(folder,sid)
        passed.append("combat_praise_uses_normal_once_and_never_reenters_workshop")
    # 終了はBERTだけで閉じる。本文モデルを呼ばず、返事を戦闘が消しても復活しない。
    for text in ["終了ー", "お開きー"]:
        with fixture(combat_settings=SETTINGS, interval_ms=5000) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
            sid = register(base, preview=False, body=True)
            drive(send, sid, lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
            original = stored(sid)
            calls = install(control, lambda text, prompt, n: step(text, "respond", "呼ばれへんはずや"))
            (folder / "player_mode").write_text("slow")
            turn = submit(base, sid, text)
            wait_for(lambda: hud(sid)["state"] == "closed")
            wait_for(lambda: row(base, turn, {"started"}))
            (folder / "player_mode").write_text("ok")
            send(sid, visual_threats=[{**MOB, "type": "enderman"}], combat={"combat_active_hint": True})
            wait_for(lambda: row(base, turn, {"cancelled"}))
            end(send, rows, sid)
            for _ in range(5):
                send(sid); time.sleep(.1)
            assert hud(sid)["state"] == "closed" and not actions(rows, sid, "workshop_resume")
            assert len(calls) == 0 and stored(sid) == original and not revisions(folder, sid)
            passed.append("bert_close_without_model_before_interrupted_ack:" + text)
    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows); original = stored(sid)
        enter(send, rows, sid)
        assert hud(sid)["state"] == "danger"
        aftermath = end(send, rows, sid, "player_kill")
        llm_before = len([x for x in seen if x["path"] == "/v1/chat/completions"])
        r = returned(send, rows, sid)
        assert "倒せた" in r["text"] and r["text"].endswith("\n".join(LINES)) and "続ける？" not in r["text"], r
        assert datetime.fromisoformat(r["created_at"]) >= datetime.fromisoformat(aftermath["completed_at"])
        assert session(base, sid)["workshop_followup"] == "discussion"
        history = session(base, sid)["history"]
        # 前置き・固定警告・復帰案内も、実再生が終われば共通履歴に残る。
        for spoken in [aftermath, r]:
            matching = [h for h in history if h["role"] == "assistant" and h["turn_id"] == spoken["turn_id"] + ":reply"]
            assert len(matching) == 1 and " ".join(matching[0]["text"].split()) == " ".join(spoken["text"].split()), history
        assert sum(h["text"] == "句の話に戻る。" for h in history) == 1, history
        assert not session(base, sid)["workshop_history"]
        assert not any(item["role"] == "assistant" and "\n".join(LINES) in item["text"] for item in history)
        for _ in range(3): send(sid)
        assert len(actions(rows, sid, "workshop_resume")) == 1
        assert stored(sid) == original and not revisions(folder, sid)
        assert len([x for x in seen if x["path"] == "/v1/chat/completions"]) == llm_before
        passed.append("aftermath_then_one_resume_notice_returns_to_discussion_without_confirmation_model")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        install(control, lambda text, prompt, n: edit(text))
        sid = ready(base, send, rows)
        # 明示編集は現在は即保存。保存失敗で保持された未採用案を戦闘へ持ち越す。
        revision_path = folder / "memory/sessions" / sid / "long_term/haiku_revisions.jsonl"
        revision_path.mkdir()
        r = finish(base, send, sid, "上五を『さくらいろ』にして")
        assert r["workshop_outcome"] == "pending_save_failed", r
        revision_path.rmdir()
        enter(send, rows, sid); end(send, rows, sid)
        r = returned(send, rows, sid)
        assert "離れられた" in r["text"] and "さくらいろ" in r["text"] and "倒" not in r["text"], r
        assert hud(sid)["canonical_lines"] == LINES and hud(sid)["pending_lines"][0] == "さくらいろ"
        calls = install(control, lambda text, prompt, n: step(text,"ask", speech="残った案を採用するか、破棄するか教えてな。"))
        r = finish(base, send, sid, "もういい")
        # 終了意思はBERT確定済み。未採用案の採否だけ本文モデルが一度確認する。
        assert r["workshop_action"] == "ask" and hud(sid)["state"] == "open" and len(calls) == 1, r
        assert hud(sid)["pending_lines"] and not revisions(folder, sid)
        install(control, lambda text, prompt, n: adoption(text))
        r = finish(base, send, sid, "採用して")
        assert r["workshop_outcome"] == "pending_saved" and len(revisions(folder, sid)) == 1
        passed.append("escape_reposts_pending_and_bert_end_preserves_it")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid)
        returned(send, rows, sid)
        calls = install(control, lambda text, prompt, n: step(text,"respond"))
        r = finish(base, send, sid, "続けない")
        assert r["workshop_action"] == "close_workshop" and hud(sid)["state"] == "closed", r
        assert not calls and not r["llm_reports"] and not revisions(folder, sid), r
        passed.append("bert_end_after_auto_resume_closes_without_generation_or_save")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def choose(text, prompt, n):
            assert "会話段階: discussion" in prompt
            assert text == "この句についてまた話そうよ"
            return step(text, "respond", "この句のどこから話そか。")
        calls = install(control, choose)
        sid = ready(base, send, rows)
        for count, (text, expected, model_calls) in enumerate([
                ("この句についてまた話そうよ", "respond", 1),
                ("この句の相談はここで切り上げよう", "close_workshop", 0)], start=1):
            enter(send, rows, sid); end(send, rows, sid)
            returned(send, rows, sid, count=count)
            r = finish(base, send, sid, text)
            assert r["workshop_action"] == expected and len(r["llm_reports"]) == model_calls, r
        assert len(calls) == 1 and hud(sid)["state"] == "closed" and not revisions(folder, sid)
        passed.append("automatic_resume_uses_ordinary_reply_and_bert_end_uses_no_model")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid, "player_kill")
        (folder / "player_mode").write_text("fail")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "failed"), None))
        assert session(base, sid)["workshop_followup"] == "discussion" and hud(sid)["state"] == "danger"
        assert not any(h["text"] == "句の話に戻る。" for h in session(base, sid)["history"])
        for _ in range(4): send(sid)
        assert len(actions(rows, sid, "workshop_resume")) == 1
        (folder / "player_mode").write_text("ok")
        r = returned(send, rows, sid, count=2)
        assert "倒せた" in r["text"] and session(base, sid)["workshop_followup"] == "discussion"
        assert not revisions(folder, sid)
        passed.append("failed_resume_keeps_pin_and_reason_and_retries_after_backoff")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid, "player_kill")
        (folder / "player_mode").write_text("slow")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "started"), None))
        assert session(base, sid)["workshop_followup"] == "discussion"
        (folder / "player_mode").write_text("ok")
        send(sid, visual_threats=[{**MOB, "entity_id": "z2"}], combat={"combat_active_hint": True})
        wait_for(lambda: row(base, r["turn_id"], {"cancelled"}))
        assert hud(sid)["state"] == "danger" and hud(sid)["canonical_lines"] == LINES
        assert not any(h["text"] == "句の話に戻る。" for h in session(base, sid)["history"])
        send(sid, event=event("combat_ended"), combat={"hostile_outcomes": [], "hostiles_within_scan_ground": 0})
        r = returned(send, rows, sid, count=2)
        assert "倒せた" not in r["text"] and "離れられた" in r["text"], r
        passed.append("new_threat_cancels_notice_and_discards_previous_victory_reason")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid)
        (folder / "player_mode").write_text("slow")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "started"), None))
        (folder / "player_mode").write_text("ok")
        install(control, lambda text, prompt, n: step(text,"show_current"))
        shown = finish(base, send, sid, "今の句")
        assert shown["text"] == "\n".join(LINES)
        assert row(base, r["turn_id"], {"completed"})
        assert session(base, sid)["workshop_followup"] == "discussion"
        for _ in range(3): send(sid)
        assert len(actions(rows, sid, "workshop_resume")) == 1
        passed.append("new_question_waits_for_notice_then_uses_current_poem_without_repeating_notice")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        (folder / "player_mode").write_text("slow")
        send(sid, visual_threats=[MOB], combat={"combat_active_hint": True})
        warning = wait_for(lambda: next((r for r in rows(sid) if r.get("combat_actions") and r["playback_status"] == "started"), None))
        turn = classified_input(base, sid, "終了")
        classified = classifier_requests(control)
        assert row(base, warning["turn_id"], {"started"}) and hud(sid)["state"] == "danger"
        (folder / "player_mode").write_text("ok")
        drive(lambda s: send(s, visual_threats=[MOB], combat={"combat_active_hint": True}), sid,
              lambda: row(base, warning["turn_id"], {"completed"}))
        assert hud(sid)["state"] == "danger" and row(base, turn, {"waiting_for_warning"})
        end(send, rows, sid)
        returned(send, rows, sid)
        r = wait_for(lambda: row(base, turn, {"cancelled"}))
        assert r["resolution"] == "superseded_classifier_mode", r
        assert classifier_requests(control) == classified and hud(sid)["state"] == "open"
        assert not actions(rows, sid, "workshop_close_feedback") and not revisions(folder, sid)
        passed.append("combat_end_waits_for_warning_then_expires_without_senryu_reclassification")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid)
        rejected_normal_input(base, sid, "終了")
        classified = classifier_requests(control)
        assert hud(sid)["state"] == "danger" and not actions(rows, sid, "workshop_close_feedback")
        end(send, rows, sid); returned(send, rows, sid)
        assert hud(sid)["state"] == "open" and not revisions(folder, sid)
        assert classifier_requests(control) == classified
        passed.append("combat_end_after_warning_cannot_close_or_save_the_workshop")

    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid = ready(base, send, rows)
        enter(send, rows, sid); end(send, rows, sid)
        (folder / "player_mode").write_text("slow")
        r = drive(send, sid, lambda: next((r for r in actions(rows, sid, "workshop_resume") if r["playback_status"] == "started"), None))
        send(sid, observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat())
        wait_for(lambda: row(base, r["turn_id"], {"cancelled"}))
        assert session(base, sid)["workshop_followup"] == "discussion"
        assert not any(r["playback_status"] == "completed" for r in actions(rows, sid, "workshop_resume"))
        passed.append("stale_snapshot_cancels_return_prompt_and_cannot_confirm_resume")

    # 戦闘中の新規入力はすべて通常分類だけ。終了の原文も川柳へ再分類しない。
    with fixture(combat_settings=SETTINGS) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        enter(send, rows, sid)
        for text in ["いい句だね", "終了しないよ", "『終了』って言っただけ", "終了"]:
            send(sid, visual_threats=[MOB], combat={"combat_active_hint":True})
            rejected_normal_input(base, sid, text)
            assert hud(sid)["state"] == "danger"
        classified = classifier_requests(control)
        assert len(classified) == 4 and all(r["mode"] == "normal" for r in classified), classified
        end(send, rows, sid); returned(send, rows, sid)
        assert not calls and not revisions(folder, sid)
        assert classifier_requests(control) == classified and hud(sid)["state"] == "open"
        passed.append("combat_praise_negation_quote_and_end_remain_normal_without_workshop_effects")

    print(json.dumps({"passed": len(passed), "cases": passed, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
