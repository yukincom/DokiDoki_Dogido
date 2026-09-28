#!/usr/bin/env python3
"""Rust音近傍補正の実HTTP配線。モデル/TTSは模擬、全所有プロセスを回収する。"""
import json
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, session, step
from check_workshop_edits import edit, finish, revisions
from check_workshop_combat import enter, SETTINGS
from check_workshop_provisional import classifier, intent, done


def main():
    passed = []
    with fixture(combat_settings=SETTINGS) as f:
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
        typed = request(base, "/api/v1/player-input", {"session_id":sid, "text":raw, "source":"text"})
        result = wait_for(lambda: row(base, typed["turn_id"], {"completed"}))
        assert result["interpreted_player_input_text"] == raw and not result["asr_corrections"]
        assert len(calls) == 2 and calls[-1][0] == raw
        passed.append("typed_input_does_not_inherit_voice_correction")

        install(control, lambda text, prompt, n: edit(text, replacement="はるのくさ"))
        result = finish(base, send, sid, "上五を『はるのくさ』にして")
        assert result.get("workshop_outcome") == "player_edit_staged", result
        pending = hud(sid)["pending_lines"]
        calls = install(control, lambda text, prompt, n: step(text))
        result = finish(base, send, sid, "ハルノクザって何？")
        assert result["workshop_action"] == "explain" and calls[-1][0] == "はるのくさって何？", result
        assert result["asr_corrections"][0]["candidate_source"] == "verse:0"
        assert hud(sid)["pending_lines"] == pending and not revisions(folder, sid)
        passed.append("pending_is_current_candidate_source_without_implicit_adoption")

        install(control, lambda text, prompt, n: edit("にして", replacement="はるのくさ"))
        result = finish(base, send, sid, "上五を『ハルノクザ』にして")
        assert result["asr_corrections"] and result["workshop_action"] == "fallback", result
        assert hud(sid)["pending_lines"] == pending and not revisions(folder, sid)
        assert stored(sid) == original
        passed.append("corrected_replacement_cannot_authorize_edit_or_save")

        calls = install(control, lambda text, prompt, n: step(text))
        result = finish(base, send, sid, "ハルノクサをあおいくさに変えて")
        assert result["asr_corrections"] and result.get("workshop_outcome") == "player_edit_staged", result
        assert not calls and hud(sid)["pending_lines"][0] == "あおいくさ" and not revisions(folder, sid)
        passed.append("original_fixed_edit_survives_correction_without_model_or_save")

        finish(base, send, sid, "却下して")
        result = finish(base, send, sid, "ハルノクザって何？")
        assert result["interpreted_player_input_text"] == "ハルノクザって何？" and not result["asr_corrections"]
        assert not hud(sid)["pending_lines"]
        passed.append("discarded_pending_candidates_do_not_leak_to_next_turn")

        calls = install(control, lambda text, prompt, n: step(text))
        classifications = classifier(control, lambda text: intent(text, "uncertain"))
        enter(send, rows, sid)
        result = done(base, send, sid, submit(base, sid, raw))
        assert classifications == [raw] and not calls
        assert not result.get("asr_corrections") and hud(sid)["state"] == "danger"
        assert "asr_fix_conversation" in log.read_text()
        passed.append("combat_pause_classifies_original_without_workshop_correction")

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
