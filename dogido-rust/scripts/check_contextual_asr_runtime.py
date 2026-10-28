#!/usr/bin/env python3
"""Rust音近傍補正の実HTTP配線。モデル/TTSは模擬、全所有プロセスを回収する。"""
import json
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, finish, revisions
from check_workshop_combat import enter, end, returned, SETTINGS
from check_workshop_provisional import done


def main():
    passed = []
    # BERTへ変更済み。旧判定へ戻さない。原文ごとの期待ラベルを固定する。
    labels = dict.fromkeys([
        "上五を『はるのくさ』にして",
        "上五なら『ハルノクザ』がいい。変更をお願い",
        "ハルノクサをあおいくさに変えて",
    ], "senryu_change_upper")
    with fixture(combat_settings=SETTINGS, bert_labels=labels) as f:
        base, _, log, control, _, _, _, _, send, hud, rows, stored, folder = f
        calls = install(control, lambda text, prompt, n: step(text))
        sid = ready(base, send, rows)
        original = wait_for(lambda: stored(sid))
        raw = "クロイオノエトって何？"
        interpreted = "くろいおのへとって何？"
        result = finish(base, send, sid, raw)
        assert result["workshop_action"] == "explain", (result, log.read_text())
        assert result["player_input_text"] == raw and result["interpreted_player_input_text"] == interpreted
        assert len(result["asr_corrections"]) == 1 and result["asr_corrections"][0]["distance"] == 1
        assert len(calls) == 1 and calls[0][0] == interpreted
        assert "今回のプレイヤー発話（認識原文）: " + raw in calls[0][1]
        assert session(base, sid)["workshop_history"][-1]["player_text"] == raw
        assert stored(sid) == original and hud(sid)["canonical_lines"] == LINES and not revisions(folder, sid)
        passed.append("voice_corrects_current_verse_question_with_one_call_and_raw_history")

        send(sid)
        typed = submit(base, sid, raw, source="text")
        result = wait_for(lambda: row(base, typed, {"completed"}))
        assert result["interpreted_player_input_text"] == raw and not result["asr_corrections"]
        assert len(calls) == 2 and calls[-1][0] == raw
        passed.append("typed_input_does_not_inherit_voice_correction")

        install(control, lambda text, prompt, n: edit(text, replacement="はるのくさ"))
        result = finish(base, send, sid, "上五を『はるのくさ』にして")
        assert result.get("workshop_outcome") == "player_edit_saved", result
        current = hud(sid)["canonical_lines"]
        calls = install(control, lambda text, prompt, n: step(text))
        result = finish(base, send, sid, "ハルノクザって何？")
        assert result["workshop_action"] == "explain" and calls[-1][0] == "はるのくさって何？", result
        assert result["asr_corrections"][0]["candidate_source"] == "verse:0"
        assert hud(sid)["canonical_lines"] == current and len(revisions(folder, sid)) == 1
        passed.append("saved_canonical_is_current_candidate_source")

        # 原文A/Bの直接編集にせず、限定抽出が補正語を誤って返した場合を調べる。
        calls = install(control, lambda text, prompt, n: edit(text, replacement="はるのくさ"))
        result = finish(base, send, sid, "上五なら『ハルノクザ』がいい。変更をお願い")
        assert result["asr_corrections"] and result["workshop_action"] == "respond", result
        assert len(calls) == len(result["llm_reports"]) == 1, result
        assert hud(sid)["canonical_lines"] == current and len(revisions(folder, sid)) == 1
        assert stored(sid) == original
        passed.append("corrected_replacement_cannot_authorize_edit_or_save")

        raw_edit = "ハルノクサをあおいくさに変えて"
        calls = install(control, lambda text, prompt, n: edit(raw_edit, replacement="あおいくさ", fragment="ハルノクサ"))
        result = finish(base, send, sid, raw_edit)
        assert result["asr_corrections"] and result.get("workshop_outcome") == "player_edit_saved", result
        assert len(calls) == len(result["llm_reports"]) == 1, result
        assert calls[0][0] == "はるのくさをあおいくさに変えて", calls
        assert hud(sid)["canonical_lines"][0] == "あおいくさ" and len(revisions(folder, sid)) == 2
        passed.append("bert_target_and_original_edit_arguments_survive_correction_and_save_once")
        calls = install(control, lambda text, prompt, n: step(text))

        result = finish(base, send, sid, "ハルノクザって何？")
        assert result["interpreted_player_input_text"] == "ハルノクザって何？" and not result["asr_corrections"]
        assert not hud(sid)["pending_lines"]
        passed.append("superseded_canonical_candidate_does_not_leak_to_next_turn")

        # 川柳として受け付けた入力を、戦闘中も保持して同じ句へ一度だけ戻す。
        identity = (result["workshop_id"], result["workshop_state_before"]["version"])
        before_revisions = revisions(folder, sid)
        calls = install(control, lambda text, prompt, n: step(text))
        (folder / "player_mode").write_text("slow")
        send(sid)
        speaking = submit(base, sid, "この句の説明を聞かせて")
        wait_for(lambda: row(base, speaking, {"started"}))
        calls.clear()
        accepted = request(base, "/api/v1/player-input", {"session_id": sid, "source": "voice", "text": raw})
        assert accepted["accepted"], accepted
        turn = accepted["turn_id"]
        held = wait_for(lambda: row(base, turn, {"waiting_for_warning"}))
        assert held["player_input_text"] == raw and held["classifier_mode"] == "senryu", held
        classification = held["request_classification"]
        assert held["resolution"] == "reply_queued" and not calls and not held.get("asr_corrections"), held
        (folder / "player_mode").write_text("ok")
        enter(send, rows, sid)
        held = row(base, turn, {"waiting_for_warning"})
        assert held and held["request_classification"] == classification, held
        assert not calls and not held.get("asr_corrections") and hud(sid)["state"] == "danger", held
        assert revisions(folder, sid) == before_revisions
        end(send, rows, sid)
        returned(send, rows, sid)
        # 原文を再送せず、元の受付turnの配送先を最後まで追う。
        result = done(base, send, sid, turn, hostile=False)
        assert (result["workshop_id"], result["workshop_state_before"]["version"]) == identity, result
        assert result["request_classification"] == classification, result
        assert revisions(folder, sid) == before_revisions and stored(sid) == original
        assert result["player_input_text"] == raw and result["interpreted_player_input_text"] == interpreted, result
        assert result["asr_corrections"] and result["workshop_action"] == "explain", result
        assert len(calls) == 1 and calls[0][0] == interpreted and hud(sid)["state"] == "open", calls
        assert "asr_fix_conversation" in log.read_text()
        passed.append("combat_pause_holds_raw_then_code_resume_corrects_current_verse_once")

    # 閉鎖後には句候補を保持しない。
    with fixture() as f:
        base, _, _, control, _, _, _, _, send, hud, rows, _, _ = f
        sid = ready(base, send, rows)
        finish(base, send, sid, "終了でいいよ")
        assert hud(sid)["state"] == "closed"
        result = finish(base, send, sid, "クロイオノエトの話をしよう")
        assert result["interpreted_player_input_text"] == result["player_input_text"] and not result["asr_corrections"]
        passed.append("closed_workshop_does_not_correct_normal_conversation")
    print(json.dumps({"passed":len(passed), "cases":passed, "all_owned_processes_stopped":True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
