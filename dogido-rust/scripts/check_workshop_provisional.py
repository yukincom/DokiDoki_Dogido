#!/usr/bin/env python3
"""旧暫定再開の撤去後、敵消失を待つ保留入力を実HTTP・模擬本文/再生で確認する。"""
from datetime import datetime, timedelta, timezone
import json
import time
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_runtime import ready, install, step, session
from check_workshop_combat import MOB, SETTINGS, actions, drive, enter, end, fixture
from check_workshop_edits import revisions


def threatening(send,sid,**extra):
    send(sid,visual_threats=[MOB],combat={"combat_active_hint":True,"hostiles_within_10":1},**extra)



def done(base, send, sid, turn, *, hostile=True):
    # 保留・BERT分類・再生待ちから転送された入力の実際の配送先まで追う。
    current = turn
    terminal = {"quiet", "completed", "failed", "cancelled"}
    def result():
        nonlocal current
        item = next((r for r in request(base, "/api/v1/rust-dialogue/snapshot")["utterances"] if r["turn_id"] == current), None)
        if item and "forwarded_input" in item:
            target = item["forwarded_input"]
            assert target["accepted"], target
            current = target["turn_id"]
            return None
        # 解除判定とsubmitの間では元行がquietでも転送先はまだ付いていない。
        if item and item.get("combat_input_outcome") == "resumed": return None
        return item if item and item["playback_status"] in terminal else None
    return drive((lambda s: threatening(send, s)) if hostile else send, sid, result, timeout=15)


def main():
    passed = []
    with fixture(combat_settings=SETTINGS) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls = install(control, lambda text,prompt,n: step(text))
        sid = ready(base,send,rows); enter(send,rows,sid)
        turn = submit(base,sid,"句の続きを話そう")
        held = wait_for(lambda: row(base,turn,{"queued"}))
        assert held["combat_input_outcome"] == "held" and not held["combat_input_result"]["llm_reports"], held
        assert held["combat_input_result"]["analysis"]["action"] == "deferred_input", held
        # 同じ一頭が停止していても時間経過で安全へ読み替えない。
        deadline = time.monotonic() + 8.2
        while time.monotonic() < deadline:
            threatening(send,sid); time.sleep(.1)
            assert row(base,turn,{"queued"}) and hud(sid)["state"] == "danger"
        assert not calls and not revisions(folder,sid)
        end(send,rows,sid)
        result = done(base,send,sid,turn,hostile=False)
        assert result["workshop_action"] == "explain" and len(calls) == 1, result
        assert calls[0][0] == "句の続きを話そう" and hud(sid)["state"] == "open"
        assert not actions(rows,sid,"workshop_resume") and not revisions(folder,sid)
        passed.append("not_target_waits_past_old_stable_period_then_resumes_same_input_without_classifier")

    with fixture(combat_settings=SETTINGS) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls = install(control,lambda text,prompt,n:step(text))
        sid = ready(base,send,rows);enter(send,rows,sid)
        turn = submit(base,sid,"上五を『さくらいろ』にして")
        held = wait_for(lambda:row(base,turn,{"queued"}))
        assert held["combat_input_result"]["analysis"]["action"] == "workshop_input", held
        assert held["combat_input_result"]["provider"] == "modernbert" and not calls
        assert hud(sid)["canonical_lines"] == LINES and not revisions(folder,sid)
        end(send,rows,sid)
        result = done(base,send,sid,turn,hostile=False)
        assert result["workshop_action"] == "stage_player_edit" and not calls, result
        assert result["workshop_outcome"] == "player_edit_saved", result
        assert hud(sid)["canonical_lines"] == ["さくらいろ",*LINES[1:]]
        assert len(revisions(folder,sid)) == 1
        passed.append("bert_change_waits_for_clear_then_edits_once_without_model")

    for change in ["enemy", "new_input", "stale", "stop"]:
        with fixture(combat_settings=SETTINGS) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
            calls = install(control,lambda text,prompt,n:step(text))
            sid = ready(base,send,rows);enter(send,rows,sid)
            turn = submit(base,sid,"句の続きを話そう")
            wait_for(lambda:row(base,turn,{"queued"}))
            assert submit(base,sid,"句の続きを話そう") == turn
            if change == "enemy":
                send(sid,visual_threats=[{**MOB,"entity_id":"z2"}],combat={"combat_active_hint":True})
                assert row(base,turn,{"queued"}) and hud(sid)["state"] == "danger"
                end(send,rows,sid)
                result = done(base,send,sid,turn,hostile=False)
                assert result["workshop_action"] == "explain" and len(calls) == 1, result
            elif change == "new_input":
                closing = submit(base,sid,"終了")
                wait_for(lambda:row(base,turn,{"cancelled"}))
                result = done(base,send,sid,closing)
                assert result["combat_input_outcome"] == "close_checked" and hud(sid)["state"] == "closed", result
                assert not calls
            elif change == "stale":
                threatening(send,sid,observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat())
                time.sleep(.3)
                assert row(base,turn,{"queued"}) and not calls
                end(send,rows,sid)
                result = done(base,send,sid,turn,hostile=False)
                assert result["workshop_action"] == "explain" and len(calls) == 1, result
            else:
                request(base,"/api/v1/rust-dialogue/interrupt",{"session_id":sid})
                wait_for(lambda:row(base,turn,{"cancelled"}))
                assert not calls
            assert not revisions(folder,sid)
            passed.append("held_input_handles_" + change + "_without_safety_classification")
    print(json.dumps({"passed":len(passed),"cases":passed,"all_owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
