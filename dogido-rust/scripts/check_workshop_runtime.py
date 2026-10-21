#!/usr/bin/env python3
"""実HTTPと模擬モデル/TTSで一句の相談・検査・取消を確認する。所有プロセスは全回収。"""
import argparse
import json
import re
import threading
import time

from check_dialogue import acknowledge, display_row, request, register, row, snapshot, submit, submit_text, wait_for
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
        prompt = "\n".join(m.get("content") or "" for m in incoming["messages"])
        text = re.search(r"今回のプレイヤー発話（会話理解用）: ([^\n]+)", prompt).group(1)
        calls.append((text, prompt))
        payload = fn(text, prompt, len(calls))
        return native_mock_message(payload, prompt, len(calls))
    control["structured_handler"] = handler
    return calls


def native_mock_message(payload, prompt, number):
    """既存の領域別ケースを使い、模擬LLMの送信形式だけ現行APIへ揃える。

    本体の互換処理ではない。旧形式拒否の試験はcompletion_overrideで生の本文JSONを送る。
    """
    if not isinstance(payload, dict) or payload.get("action") not in {"inspect", "stage_player_edit"}:
        return payload
    action = payload["action"]
    if action == "inspect":
        name = "inspect_verse"
        arguments = {key:payload[key] for key in ("checks", "inspect_text", "inspect_line_index") if key in payload}
    else:
        name = action
        context = {}
        decoder = json.JSONDecoder()
        for match in re.finditer(r'\{', prompt):
            try:
                value, _ = decoder.raw_decode(prompt[match.start():])
            except ValueError:
                continue
            if isinstance(value, dict) and "current_verse" in value and "saved_line_sources" in value:
                context = value
        lines = (context.get("pending_verse") or context.get("current_verse") or "").splitlines()
        proposal = payload.get("line_proposal", {})
        reference = payload.get("line_reference", {})
        fragment = proposal.get("target_fragment", "")
        index = None
        concept = reference.get("concept_id") if reference.get("found") else None
        if concept in {"line_1", "line_2", "line_3"}:
            index = int(concept[-1]) - 1
        elif fragment:
            matches = [i for i, line in enumerate(lines) if fragment in line]
            if len(matches) == 1:
                index = matches[0]
        if index is None:
            target = context.get("discussion_target") or {}
            index = target.get("line_index")
        arguments = {"line_id":f"line_{index + 1}" if isinstance(index, int) else "unknown",
            "expected_text":lines[index] if isinstance(index, int) and 0 <= index < len(lines) else "",
            "target_fragment":fragment, "replacement_text":proposal.get("replacement_text", ""),
            "evidence":payload.get("evidence"), "confidence":payload.get("confidence")}
        if reference.get("found") and reference.get("evidence"):
            arguments["line_reference_evidence"] = reference["evidence"]
    return {"role":"assistant", "content":None, "tool_calls":[{
        "id":f"fixture-tool-{number}", "type":"function",
        "function":{"name":name, "arguments":json.dumps(arguments, ensure_ascii=False)}}]}


def ready(base, send, rows):
    sid = register(base, preview=False)
    send(sid)
    wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "completed" for r in rows(sid)))
    return sid


def wait_with_observations(base, send, sid, turn):
    """実推論中もMinecraftと同じく観測を継続し、通常の鮮度判定を通す。"""
    next_observation = 0.0
    def poll():
        nonlocal next_observation
        if time.monotonic() >= next_observation:
            send(sid)
            next_observation = time.monotonic() + .25
        return row(base, turn, {"completed", "failed", "cancelled"})
    return wait_for(poll, timeout=95)


def session(base, sid):
    return next(s for s in snapshot(base)["sessions"] if s["session_id"] == sid)


def check_text_workshop(passed):
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = register(base, preview=False, voice_enabled=False, body=True)
        send(sid)
        acknowledged = set()
        def display_poem():
            for reply in rows(sid):
                if reply.get("display_id") and reply["display_id"] not in acknowledged:
                    acknowledge(base, sid, reply)
                    acknowledged.add(reply["display_id"])
            return next((r for r in rows(sid) if r["turn_id"].endswith(":poem")
                         and r["playback_status"] == "audio_disabled"), None)
        poem = wait_for(display_poem)
        assert poem["text_displayed"] and poem["delivery_confirmed"], poem
        before = wait_for(lambda: stored(sid))
        assert len(before) == 1 and hud(sid)["canonical_lines"] == LINES
        history = session(base, sid)["history"]
        assert len([r for r in history if r["role"] == "event" and "示した" in r["text"]]) == 1, history
        assert not any(r["playback_status"] == "completed" for r in rows(sid))
        passed.append("text_haiku_ack_records_one_delivery_and_keeps_canonical_verse")

        turn = submit_text(base, sid, "この句はどういう意味？")
        reply = wait_for(lambda: display_row(base, turn))
        assert reply["workshop_action"] == "explain", reply
        assert not session(base, sid)["workshop_history"]
        acknowledge(base, sid, reply)
        acknowledge(base, sid, reply)
        wait_for(lambda: row(base, turn, {"audio_disabled"}))
        assert len(session(base, sid)["workshop_history"]) == 1
        send(sid)
        second = submit_text(base, sid, "その黒さは何を表してる？")
        reply2 = wait_for(lambda: display_row(base, second))
        assert "葉の明るさと斧の黒さ" in calls[-1][1], calls
        acknowledge(base, sid, reply2)
        wait_for(lambda: row(base, second, {"audio_disabled"}))
        assert len(session(base, sid)["workshop_history"]) == 2
        assert stored(sid) == before and hud(sid)["canonical_lines"] == LINES
        assert not any(r["path"].startswith(("/audio_query", "/synthesis")) for r in seen), seen
        passed.append("text_workshop_ack_continues_once_without_audio_or_verse_mutation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-only", action="store_true", help="テキスト配送の追加確認だけ実行")
    args = parser.parse_args()
    passed = []
    check_text_workshop(passed)
    if args.text_only:
        print(json.dumps({"passed": passed, "all_owned_processes_stopped": True}, ensure_ascii=False, indent=2))
        return
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        before = stored(sid)
        turn = submit(base, sid, "この句はどういう意味？")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "explain", (result, log.read_text())
        assert len(calls) == 1
        history = session(base, sid)["history"]
        assert len(history) == 3 and history[0]["role"] == "assistant", history
        assert all(r["role"] == "event" and r["reaction"] == "story" for r in history[1:]), history
        assert "この句はどういう意味？" in history[-1]["text"] and result["text"] in history[-1]["text"]
        assert len(session(base, sid)["workshop_history"]) == 1
        send(sid)
        turn2 = submit(base, sid, "その黒さは何を表してる？")
        wait_for(lambda: row(base, turn2, {"completed"}))
        assert "葉の明るさと斧の黒さ" in calls[-1][1]
        assert stored(sid) == before and hud(sid)["canonical_lines"] == LINES
        passed.append("one_step_meaning_shared_as_event_without_verse_mutation")

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

        natural = "『ふゆむ』は『冬む』やなくて、『草を踏む』の『ふむ』やと思うてな。雪もないのに、なぜ『冬』って響きが出たんやろうか？"
        calls3 = install(control, lambda text, prompt, n: step(text, speech=natural))
        send(sid)
        turn = submit(base, sid, "どういう意味なの？")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_reason"] == "accepted" and len(calls3) == 1, result
        assert result["text"] == natural and stored(sid) == before
        passed.append("quoted_natural_explanation_passes_without_prose_screening")

        calls4 = install(control, lambda text, prompt, n: step("相談はここでおしまい", "close_workshop"))
        send(sid)
        turn = submit(base, sid, "今日は相談はここでおしまいにしよう")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "close_workshop" and len(calls4) == 1, result
        assert hud(sid)["state"] == "closed", hud(sid)
        passed.append("natural_close_applied_once_after_original_evidence_check")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: {
            "action": "close_workshop", "purpose": "finish_workshop", "confidence": .95,
            "evidence": text, "speech": "ほな、また話そな。"})
        sid = ready(base, send, rows)
        before = stored(sid)
        turn = submit(base, sid, "この句の相談はここで終わりにしよう。")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "close_workshop" and len(calls) == 1, result
        assert result["text"] == "ほな、また話そな。" and hud(sid)["state"] == "closed"
        record_path = folder / "memory/sessions" / sid / "long_term/haiku_workshop_turns.jsonl"
        def completed_records():
            if not record_path.exists():
                return []
            records = [json.loads(line) for line in record_path.read_bytes().splitlines(keepends=True)
                       if line.endswith(b"\n")]
            return [r for r in records if r["event_kind"] == "turn_result"
                    and r["result"]["turn_id"] == turn]
        recorded = wait_for(completed_records)
        assert len(recorded) == 1 and recorded[0]["state_after"]["open"] is False, recorded
        assert recorded[0]["state_after"]["close_reason"] == "explicit_close", recorded
        assert stored(sid) == before
        passed.append("missing_checks_defaulted_and_close_completed_without_model_retry")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        def mistaken_close(text, prompt, n):
            result = step(text, "close_workshop" if text == "終了でいいよ" else "respond")
            result["speech"] = ("ほな今日はここまでや。また話そな。" if text == "終了でいいよ"
                                else "うん、まだこの句の話を続けよか。")
            return result
        calls = install(control, mistaken_close)
        sid = ready(base, send, rows)
        for text in ["終了しないよ", "『終了』ってどういう意味？", "終了したらどうなる？", "終了かな？"]:
            send(sid)
            turn = submit(base, sid, text)
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_action"] == "respond" and hud(sid)["state"] == "open", (text, result)
            assert result["workshop_reason"] == "accepted" and len(result["llm_reports"]) == 1
            assert result["text"] == "うん、まだこの句の話を続けよか。"
        assert len(calls) == 4
        passed.append("model_continuation_keeps_workshop_open_without_retry")
        send(sid)
        turn = submit(base, sid, "終了でいいよ")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "close_workshop" and len(calls) == 5
        assert hud(sid)["state"] == "closed"
        assert result["text"] == "ほな今日はここまでや。また話そな。"
        passed.append("short_close_uses_model_decision_and_model_speech")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        for overrides in [{"evidence": "今日は終了"}, {"checks": "meter"},
                          {"checks": ["shell"]}, {"confidence": "invalid", "speech": ""}]:
            calls = install(control, lambda text, prompt, n: {
                **step(text, "close_workshop"), "speech": "この句の話、聞いとるで。", **overrides})
            sid = ready(base, send, rows)
            before = stored(sid)
            turn = submit(base, sid, "相談はここでおしまい")
            result = wait_for(lambda: row(base, turn, {"completed", "quiet"}))
            assert result["workshop_action"] == "respond" and result["workshop_reason"] == "close_skipped", result
            assert len(calls) == 1 and len(result["llm_reports"]) == 1, result
            assert hud(sid)["state"] == "open" and hud(sid)["canonical_lines"] == LINES
            assert stored(sid) == before
            if overrides.get("speech") == "":
                assert result["text"] == "" and result["playback_status"] == "quiet", result
            else:
                assert result["text"] == "この句の話、聞いとるで。" and result["playback_status"] == "completed", result
                assert session(base, sid)["workshop_history"][-1]["dogido_text"] == result["text"]
        passed.append("invalid_close_keeps_original_reply_or_silence_without_retry_or_failure_message")

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
        history = session(base, sid)["workshop_history"]
        assert len(history) == 1 and history[0]["turn_id"] == turn and not history[0]["dogido_text"], history
        assert history[0]["reply_delivery"] == "unspoken_combat" and history[0]["unspoken_text"] == step("")["speech"], history
        assert "言葉を飲み込んで、戦いに挑んだ。" in history[0]["narrative"] and hud(sid)["state"] == "danger"
        assert stored(sid) == before
        passed.append("hostile_interrupt_keeps_poem_and_records_unspoken_explanation")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text, "unrelated"))
        sid = ready(base, send, rows)
        wid = hud(sid)["workshop_id"]
        before = stored(sid)
        turns = []
        for text in ["今日は楽しかったね", "明日も一緒に遊ぼうね"]:
            send(sid)
            turn = submit(base, sid, text)
            turns.append(turn)
            result = wait_for(lambda: row(base, turn, {"completed"}))
            assert result["workshop_action"] == "unrelated" and result["text"] == control["leaf"]
            assert len(result["llm_reports"]) == 2  # workshop + ordinary single generation
            if len(calls) == 1:
                assert hud(sid)["workshop_id"] == wid and hud(sid)["state"] == "open"
        assert hud(sid)["state"] == "closed" and len(calls) == 2
        assert not session(base, sid)["workshop_history"]
        expected_turns = {t for turn in turns for t in (turn, turn + ":reply")}
        conversation = [r for r in session(base, sid)["history"] if r["turn_id"] in expected_turns]
        assert len(conversation) == 4 and [r["role"] for r in conversation] == ["user", "assistant"] * 2, conversation
        assert stored(sid) == before
        passed.append("unrelated_input_handed_to_casual_once_and_closes_after_two_completed_replies")

    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        calls = install(control, lambda text, prompt, n: step(text, "accept_pending"))
        sid = ready(base, send, rows)
        before = stored(sid)
        turn = submit(base, sid, "その案でお願い")
        result = wait_for(lambda: row(base, turn, {"completed"}))
        assert result["workshop_action"] == "fallback" and len(calls) == 2
        assert hud(sid)["state"] == "open" and stored(sid) == before
        passed.append("unresolved_repair_target_cannot_change_or_save_canonical_verse")

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
            time.sleep(3)  # 返答を生成している時間は無操作に数えない。
            assert hud(sid)["state"] == "open"
            assert not row(base, turn, {"cancelled"})
            release.set()
            wait_for(lambda: row(base, turn, {"completed"}), timeout=4)
            assert hud(sid)["state"] == "closed"  # 明示終了の意思で閉じる。
            passed.append("inflight_workshop_response_survives_idle_timeout_and_explicit_close_still_works")
        finally:
            release.set()

    print(json.dumps({"passed": len(passed), "cases": passed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
