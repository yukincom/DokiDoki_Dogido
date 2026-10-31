#!/usr/bin/env python3
"""戦闘中の新規入力を川柳へ持ち越さず、敵消失後だけ再開する境界を確認する。"""
from datetime import datetime, timedelta, timezone
import json
import time
from check_dialogue import request, row
from check_haiku_runtime import LINES
from check_workshop_runtime import ready, install, step
from check_workshop_combat import (MOB, SETTINGS, actions, drive, enter, end, fixture,
    classifier_requests, rejected_normal_input, returned)
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
        turn = rejected_normal_input(base, sid, "句の続きを話そう")
        classified = classifier_requests(control)
        # 同じ一頭が停止していても時間経過で安全へ読み替えない。
        deadline = time.monotonic() + 8.2
        while time.monotonic() < deadline:
            threatening(send,sid); time.sleep(.1)
            assert row(base,turn,{"not_selected"}) and hud(sid)["state"] == "danger"
        assert not calls and not revisions(folder,sid)
        end(send,rows,sid)
        returned(send,rows,sid)
        assert not calls and hud(sid)["state"] == "open" and not revisions(folder,sid)
        assert classifier_requests(control) == classified
        passed.append("normal_input_does_not_resume_after_old_stable_period_or_reenter_senryu")

    with fixture(combat_settings=SETTINGS) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls = install(control,lambda text,prompt,n:step(text))
        sid = ready(base,send,rows);enter(send,rows,sid)
        rejected_normal_input(base, sid, "上五を『さくらいろ』にして")
        classified = classifier_requests(control)
        assert len(classified) == 1 and classified[0]["mode"] == "normal", classified
        assert hud(sid)["canonical_lines"] == LINES and not revisions(folder,sid)
        end(send,rows,sid)
        returned(send,rows,sid)
        assert not calls and hud(sid)["canonical_lines"] == LINES and not revisions(folder,sid)
        assert classifier_requests(control) == classified
        passed.append("combat_line_change_stays_normal_and_never_edits_after_clear")

    for change in ["enemy", "new_input", "stale", "stop"]:
        with fixture(combat_settings=SETTINGS) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
            calls = install(control,lambda text,prompt,n:step(text))
            sid = ready(base,send,rows);enter(send,rows,sid)
            turn = rejected_normal_input(base, sid, "句の続きを話そう")
            classified = classifier_requests(control)
            if change == "enemy":
                send(sid,visual_threats=[{**MOB,"entity_id":"z2"}],combat={"combat_active_hint":True})
                assert row(base,turn,{"not_selected"}) and hud(sid)["state"] == "danger"
                end(send,rows,sid)
                returned(send,rows,sid)
            elif change == "new_input":
                rejected_normal_input(base, sid, "終了")
                classified = classifier_requests(control)
                assert len(classified) == 2 and all(r["mode"] == "normal" for r in classified), classified
                assert hud(sid)["state"] == "danger"
                end(send,rows,sid); returned(send,rows,sid)
                assert hud(sid)["state"] == "open"
            elif change == "stale":
                threatening(send,sid,observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat())
                time.sleep(.3)
                assert row(base,turn,{"not_selected"}) and not calls
                assert not actions(rows,sid,"workshop_resume")
                end(send,rows,sid)
                returned(send,rows,sid)
            else:
                request(base,"/api/v1/rust-dialogue/interrupt",{"session_id":sid})
                assert row(base,turn,{"not_selected"})
            assert not calls and not revisions(folder,sid)
            assert classifier_requests(control) == classified
            passed.append("normal_input_handles_" + change + "_without_senryu_reclassification")
    print(json.dumps({"passed":len(passed),"cases":passed,"all_owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
