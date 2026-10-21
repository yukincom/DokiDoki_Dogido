#!/usr/bin/env python3
"""native stage_player_editを既存の原文補完・保存経路へ通す。観測・初期句・音声は模擬。"""
import argparse
import json
import time
from pathlib import Path
import urllib.request

from check_dialogue import submit
from check_haiku_runtime import fixture, LINES
from check_workshop_edits import revisions
from check_workshop_runtime import ready, step, wait_with_observations


def edit_args(text, replacement="しろい"):
    return {"line_id":"line_2", "expected_text":"line_1=さくらのは, line_2=くろいおのへと, line_3=あさのいろ。",
            "target_fragment":"くろい", "replacement_text":replacement, "evidence":text, "confidence":0.95}


def run_case(name, text, arguments, *, expected_lines=None, expected_code=None, batch=False, base_url=None,
             legacy_action=None, sampling=None, initial_lines=None):
    calls = []
    responses = []
    tool_results = []
    base_lines = LINES if initial_lines is None else initial_lines
    with fixture(initial_lines=initial_lines) as (base, _, log, control, _, _, _, _, send, hud, rows, stored, folder):
        def completion(incoming):
            if incoming["max_tokens"] != 420:
                return None
            if base_url and sampling:
                incoming.update(sampling)
            calls.append(incoming)
            tools = {tool["function"]["name"] for tool in incoming.get("tools", [])}
            messages = [m for m in incoming["messages"] if m["role"] == "tool"]
            if messages:
                assert incoming["tool_choice"] == "none" and len(messages) == 1, incoming
                result = json.loads(messages[0]["content"])
                assert result["status"] == "rejected" and result["canonical_unchanged"], result
                assistant = next(m for m in incoming["messages"] if m.get("tool_calls"))
                assert messages[0]["tool_call_id"] == assistant["tool_calls"][0]["id"]
                tool_results.append(result)
            if base_url:
                started = time.monotonic()
                request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                    json.dumps(incoming).encode(), {"Content-Type":"application/json"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    result = json.load(response)
                responses.append({"response":result, "elapsed_ms":round((time.monotonic() - started) * 1000)})
                return result
            if legacy_action:
                # Deliberately keep returning the removed wire format, including after retry.
                # It must never run an inspection, retain a candidate, or change a verse.
                payload = step(text, legacy_action, checks=["meter"] if legacy_action == "inspect" else [])
                if legacy_action == "stage_player_edit":
                    payload.update(purpose="improve_wording",
                        line_reference={"found":True,"concept_id":"line_2","evidence":"","confidence":.95},
                        line_proposal={"found":True,"target_fragment":"くろい","replacement_text":"しろい",
                                       "evidence":text,"confidence":.95})
                message = {"role":"assistant","content":json.dumps(payload, ensure_ascii=False)}
            elif len(calls) == 1:
                assert "stage_player_edit" in tools and incoming["tool_choice"] == "auto", incoming
                call = {"id":"edit-1", "type":"function", "function":{"name":"stage_player_edit",
                        "arguments":json.dumps(arguments, ensure_ascii=False)}}
                native = [call, {**call, "id":"edit-2"}] if batch else [call]
                message = {"role":"assistant", "content":None, "tool_calls":native}
            else:
                assert incoming.get("tool_choice") == "none" or "stage_player_edit" not in tools, incoming
                message = {"role":"assistant", "content":json.dumps(step(text, "respond",
                    "今の句を見ながら、一緒に考えよか。"), ensure_ascii=False)}
            return {"id":"edit-tool-smoke", "model":"fixture", "object":"chat.completion", "created":0,
                    "choices":[{"index":0, "message":message,
                        "finish_reason":"tool_calls" if message.get("tool_calls") else "stop"}]}

        control["completion_override"] = completion
        sid = ready(base, send, rows)
        original_entries = stored(sid)
        turn = submit(base, sid, text)
        reply = wait_with_observations(base, send, sid, turn)
        current = hud(sid)
        saved = revisions(folder, sid)
        native_calls = []
        for report in reply.get("llm_reports", []):
            try:
                response = json.loads(report.get("generated", {}).get("text", ""))
                if isinstance(response, dict):
                    native_calls.extend(response.get("tool_calls", []))
            except ValueError:
                pass
        passed = reply["playback_status"] == "completed" and stored(sid) == original_entries
        if expected_lines is not None:
            passed = passed and current["canonical_lines"] == expected_lines and len(saved) == 1
            passed = passed and reply.get("workshop_outcome") == "player_edit_saved" and bool(native_calls)
            passed = passed and current["editing"] and current["selected_line"] == next(
                i for i, pair in enumerate(zip(base_lines, expected_lines)) if pair[0] != pair[1])
            passed = passed and any(s.get("transport") == "native_tool" and
                s.get("tool_call", {}).get("name") == "stage_player_edit" for s in reply.get("workshop_steps", []))
            if saved:
                changed = [i for i, pair in enumerate(zip(base_lines, expected_lines)) if pair[0] != pair[1]]
                assert len(changed) == 1 and len(saved[0]["edits"]) == 1, saved
                assert saved[0]["edits"][0]["line_index"] == changed[0], saved
                assert saved[0]["edits"][0]["replacement_text"] == expected_lines[changed[0]], saved
                assert saved[0]["base_text"] == "\n".join(base_lines), saved
            if not base_url:
                assert len(calls) == 1, calls
        else:
            passed = passed and current["canonical_lines"] == base_lines and not saved and not current["pending_lines"]
            if expected_code:
                evidence = json.dumps([tool_results, reply.get("workshop_steps", [])], ensure_ascii=False) + log.read_text()
                passed = passed and expected_code in evidence
            if not base_url:
                assert len(calls) == 2, calls
            if legacy_action:
                assert not tool_results and not native_calls, reply
                assert reply.get("workshop_conversation_candidate") is None, reply
                assert all(s.get("outcome") == "rejected" and s.get("transport") == "content_json"
                           for s in reply["workshop_steps"]), reply
                assert len(reply["workshop_steps"]) == 2, reply
        return {"case":name, "passed":bool(passed), "model_calls":len(calls), "native_calls":native_calls,
                "reply":reply, "canonical_lines":current["canonical_lines"], "revisions":saved,
                "tool_results":tool_results, "requests":calls, "responses":responses, "runtime_log":log.read_text()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="指定時は変更依頼一件だけを実モデルへ送る。推論サーバーは起動・停止しない")
    parser.add_argument("--output", type=Path, help="結果と生のリクエスト・応答を新規ファイルへ保存")
    parser.add_argument("--official-sampling", action="store_true",
                        help="実モデル試験だけtemperature=1.0, top_p=0.95, top_k=64で送信")
    args = parser.parse_args()
    if args.official_sampling and not args.base_url:
        parser.error("--official-samplingには--base-urlが必要です")
    sampling = {"temperature":1.0, "top_p":0.95, "top_k":64} if args.official_sampling else None
    raw = "くろいをしろいに変えて"
    cases = [run_case("whole_expected_changes_only_middle", raw, edit_args(raw),
                      expected_lines=[LINES[0], "しろいおのへと", LINES[2]], base_url=args.base_url, sampling=sampling)]
    if not args.base_url:
        full = "中七をしろいおのへとに変えて"
        cases.append(run_case("whole_replacement_keeps_other_lines", full,
            edit_args(full, "\n".join([LINES[0], "しろいおのへと", LINES[2]])),
            expected_lines=[LINES[0], "しろいおのへと", LINES[2]]))
        short = "くろいをあおに変えて"
        cases.append(run_case("short_meter_is_saved", short, edit_args(short, "あお"),
                              expected_lines=[LINES[0], "あおおのへと", LINES[2]]))
        stale = edit_args(raw)
        stale["expected_text"] = "\n".join(["さくらいろ", *LINES[1:]])
        cases.append(run_case("stale_unselected_line_blocks_edit", raw, stale, expected_code="stale_tool_edit"))
        stale_evidence = edit_args(raw)
        stale_evidence["evidence"] = "前の案をしろいにするつもり"
        cases.append(run_case("past_evidence_cannot_authorize_edit", raw, stale_evidence,
                              expected_code="tool_evidence_not_in_original"))
        cases.append(run_case("multiple_calls_execute_nothing", raw, edit_args(raw),
                              batch=True, expected_code="one_workshop_tool_required"))
        for action in ("stage_player_edit", "inspect"):
            cases.append(run_case("removed_json_" + action + "_cannot_execute", raw, edit_args(raw),
                                  legacy_action=action, expected_code="native_tool_required"))
        # 2026-10-18実ログの原文と誤ったtool引数を再現。モデルによる末尾・旧語の短縮後も
        # 原文の「うつろいの」を使い、同じ保存経路で上五一行だけを一度保存する。
        reported_lines = ["ゆらぐひが", "たましいのひが", "ゆらゆらと"]
        for name, text, target in [
            ("reported_typed_keeps_trailing_no", "ゆらぐひが を うつろいの に変えてください。", "ゆらぐひが"),
            ("reported_voice_keeps_both_phrases", "揺らぐひがをうつろいのに変えてください", "ひが"),
            ("reported_voice_with_intro_keeps_both_phrases", "では揺らぐひがをうつろいのに変えてください", "ゆらぐひが"),
        ]:
            arguments = {"line_id":"line_1", "expected_text":reported_lines[0],
                "target_fragment":target, "replacement_text":"うつろい", "evidence":text, "confidence":1.0}
            cases.append(run_case(name, text, arguments, initial_lines=reported_lines,
                expected_lines=["うつろいの", *reported_lines[1:]]))
        # 2026-10-21実発話。句中の「もし」で変更を棄却し、音数説明だけへ戻っていた。
        lamp_lines = ["いしのそこ", "ゆれるともしのひ", "だいじょうぶや"]
        for name, text, fragment, reference in [
            ("lamp_arrow", "ゆれるともしのひ->ゆれるともしび", "ゆれるともしのひ", ""),
            ("lamp_full_request", "ゆれるともしのひをゆれるともしびに変更してください", "ゆれるともしのひ", ""),
            ("lamp_free_wording", "ゆれるともしのひをゆれるともしびにかえようぜ", "ゆれるともしのひ", ""),
            ("lamp_line_reference", "中句をゆれるともしびに変更してください", "", "中句"),
        ]:
            arguments = {"line_id":"line_2", "expected_text":lamp_lines[1],
                "target_fragment":fragment, "line_reference_evidence":reference,
                "replacement_text":"ゆれるともしび", "evidence":text, "confidence":1.0}
            case = run_case(name, text, arguments, initial_lines=lamp_lines,
                expected_lines=[lamp_lines[0], "ゆれるともしび", lamp_lines[2]])
            assert case["reply"]["text"] == "変更しました。", case["reply"]
            cases.append(case)
        text = "ではじめるをおわるに変えて"
        arguments = {"line_id":"line_1", "expected_text":"ではじめる",
            "target_fragment":"ではじめる", "replacement_text":"おわる", "evidence":text, "confidence":1.0}
        cases.append(run_case("word_initial_dewa_is_not_removed_to_fit_meter", text, arguments,
            initial_lines=["ではじめる", *reported_lines[1:]], expected_lines=["おわる", *reported_lines[1:]]))
        # 17:01の矢印入力。今回の7音を指定したのに、モデルが前の8音を再利用したケース。
        sand_lines = ["やさしいひが", "しろいしをてらし", "うみのゆめ"]
        for name, text, model_word, expected_lines, expected_code in [
            ("reported_arrow_eight_mora_is_saved",
             "しろいしをてらし -> しろすなをてらし", "しろすなをてらし", [sand_lines[0], "しろすなをてらし", sand_lines[2]], None),
            ("reported_arrow_seven_mora_overrides_previous_model_word",
             "しろいしをてらし -> しろすなてらし", "しろすなをてらし",
             [sand_lines[0], "しろすなてらし", sand_lines[2]], None),
            ("arrow_preserves_players_eight_mora",
             "しろいしをてらし → しろすなをてらし", "しろすなてらし", [sand_lines[0], "しろすなをてらし", sand_lines[2]], None),
            ("arrow_denial_uses_model_choice",
             "しろいしをてらし → しろさではない", "しろさ", [sand_lines[0], "しろさ", sand_lines[2]], None),
            ("arrow_condition_uses_model_choice",
             "しろいしをてらし → さくらいろなら", "さくらいろ", [sand_lines[0], "さくらいろ", sand_lines[2]], None),
            ("arrow_question_with_period_uses_model_choice",
             "しろいしをてらし → さくらいろかな。", "さくらいろ", [sand_lines[0], "さくらいろ", sand_lines[2]], None),
        ]:
            arguments = {"line_id":"line_2", "expected_text":sand_lines[1],
                "target_fragment":sand_lines[1], "replacement_text":model_word, "evidence":text, "confidence":.95}
            cases.append(run_case(name, text, arguments, initial_lines=sand_lines,
                expected_lines=expected_lines, expected_code=expected_code))
    if args.output:
        with args.output.open("x") as handle:
            json.dump({"mode":"live" if args.base_url else "mock", "cases":cases}, handle, ensure_ascii=False, indent=2)
    print(json.dumps([{k:case[k] for k in ("case", "passed", "model_calls", "canonical_lines")}
                      for case in cases], ensure_ascii=False, indent=2))
    if not all(case["passed"] for case in cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
