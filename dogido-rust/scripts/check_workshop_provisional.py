#!/usr/bin/env python3
"""暫定再開と非同期5分類。実モデル・OS AI・スピーカーを使わない。"""
from datetime import datetime, timedelta, timezone
import json
import re
import threading
import time
from check_dialogue import request, row, submit, wait_for
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import ready, install, step, session
from check_workshop_combat import MOB, SETTINGS, actions, drive, enter
from check_workshop_edits import edit, revisions


def classifier(control, choose):
    original = control["structured_handler"]; calls=[]
    def handle(incoming):
        if incoming["max_tokens"] != 120: return original(incoming)
        prompt="\n".join(m["content"] for m in incoming["messages"])
        text=re.search(r"^プレイヤー: (.+)$",prompt,re.M).group(1)
        calls.append(text)
        return choose(text)
    control["structured_handler"]=handle
    return calls


def intent(text, action="resume_workshop", confidence=.95):
    return {"action":action,"confidence":confidence,"evidence":text}


def threatening(send,sid,**extra):
    send(sid,visual_threats=[MOB],combat={"combat_active_hint":True,"hostiles_within_10":1},**extra)


def stable(send,rows,sid):
    enter(send,rows,sid)
    until=time.monotonic()+1.15
    while time.monotonic()<until:
        threatening(send,sid);time.sleep(.08)


def done(base,send,sid,turn):
    return drive(lambda s:threatening(send,s),sid,lambda:row(base,turn,{"quiet","completed","failed","cancelled"}))


def main():
    passed=[]
    with fixture(combat_settings=SETTINGS,low_threat_resume_delay_ms=1000) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls=classifier(control,lambda text:intent(text))
        install(control,lambda text,prompt,n:step(text))
        sid=ready(base,send,rows);stable(send,rows,sid)
        assert hud(sid)["state"]=="danger" and not hud(sid)["provisional_resume"]
        t=submit(base,sid,"句の続きを話そう")
        r=done(base,send,sid,t);assert r["combat_input_outcome"]=="provisional_resume",r
        drive(lambda s:threatening(send,s),sid,lambda:any(r["playback_status"]=="completed" for r in actions(rows,sid,"workshop_direct_resume")))
        assert hud(sid)["provisional_resume"] and hud(sid)["state"]=="open"
        assert len(calls)==1 and not revisions(folder,sid)
        t=submit(base,sid,"この句の意味を教えて")
        r=done(base,send,sid,t);assert r["playback_status"]=="completed" and r["workshop_action"]=="explain",r
        assert len(calls)==1 and session(base,sid)["workshop_history"]
        (folder/"player_mode").write_text("slow")
        t=submit(base,sid,"もう一度句を説明して")
        drive(lambda s:threatening(send,s),sid,lambda:row(base,t,{"started"}))
        send(sid,visual_threats=[{**MOB,"approaching":True}],combat={"combat_active_hint":True})
        wait_for(lambda:row(base,t,{"cancelled"}))
        assert hud(sid)["state"]=="danger" and not hud(sid)["provisional_resume"]
        assert len(session(base,sid)["workshop_history"])==1
        passed.append("explicit_resume_only_then_same_enemy_keeps_dialogue_and_approach_cancels")

    with fixture(combat_settings=SETTINGS,low_threat_resume_delay_ms=1000) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls=classifier(control,lambda text:intent(text,"workshop_input"))
        edits=install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows);stable(send,rows,sid)
        t=submit(base,sid,"上五を『さくらいろ』にして")
        r=drive(lambda s:threatening(send,s),sid,lambda:next((r for r in rows(sid) if r["turn_id"]==t and "forwarded_input" in r),None))
        follow=r["forwarded_input"]["turn_id"]
        r=done(base,send,sid,follow)
        assert r["workshop_action"]=="stage_player_edit" and len(calls)==1 and len(edits)==1,r
        assert r["workshop_outcome"]=="player_edit_saved",r
        assert hud(sid)["canonical_lines"]==["さくらいろ",*LINES[1:]] and not hud(sid)["pending_lines"]
        assert len(revisions(folder,sid))==1
        send(sid,visual_threats=[MOB],combat={"recent_damage_ms":0,"combat_active_hint":True})
        assert hud(sid)["state"]=="danger" and hud(sid)["canonical_lines"]==["さくらいろ",*LINES[1:]]
        assert len(revisions(folder,sid))==1
        passed.append("substantive_input_forwarded_once_and_damage_keeps_saved_explicit_edit")

    with fixture(combat_settings=SETTINGS,low_threat_resume_delay_ms=8000) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        classifier(control,lambda text:intent(text))
        sid=ready(base,send,rows);enter(send,rows,sid)
        t=submit(base,sid,"句の続きを話そう")
        r=done(base,send,sid,t);assert r["combat_input_outcome"]=="threat_not_stable",r
        assert hud(sid)["state"]=="danger" and not hud(sid)["provisional_resume"]
        passed.append("model_cannot_shorten_eight_second_safety_period")

    with fixture(combat_settings=SETTINGS,low_threat_resume_delay_ms=1000) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        classifier(control,lambda text:intent(text))
        sid=ready(base,send,rows);stable(send,rows,sid)
        for text in ["句に戻らない", "『句の続きを話そう』と言われた", "句を続けるな", "うん"]:
            t=submit(base,sid,text);r=done(base,send,sid,t)
            assert r["combat_input_result"]["analysis"]["action"]=="uncertain",r
            assert hud(sid)["state"]=="danger"
        passed.append("model_and_rule_fallback_cannot_resume_negated_or_quoted_intent")

    with fixture(combat_settings=SETTINGS,low_threat_resume_delay_ms=1000) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        classifier(control,lambda text:intent(text,"close_workshop"))
        sid=ready(base,send,rows)
        (folder/"player_mode").write_text("slow")
        threatening(send,sid)
        warning=wait_for(lambda:next((r for r in rows(sid) if r.get("combat_actions") and r["playback_status"]=="started"),None))
        t=submit(base,sid,"句の相談はお開きにしよう")
        r=wait_for(lambda:row(base,t,{"quiet"}))
        assert r["combat_input_outcome"]=="close_checked" and hud(sid)["state"]=="closed"
        assert row(base,warning["turn_id"],{"started"})
        passed.append("natural_close_never_interrupts_current_warning")

    for change in ["enemy","new_input","stale","stop"]:
        with fixture(combat_settings=SETTINGS,low_threat_resume_delay_ms=1000) as (base,p,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
            entered=threading.Event();release=threading.Event()
            def slow(text):
                if text == "終了":
                    return intent(text,"close_workshop")
                entered.set();release.wait(timeout=10);return intent(text)
            calls=classifier(control,slow)
            try:
                sid=ready(base,send,rows);stable(send,rows,sid)
                t=submit(base,sid,"句の続きを話そう");assert entered.wait(timeout=5)
                duplicate=submit(base,sid,"句の続きを話そう");assert duplicate==t and len(calls)==1
                if change=="enemy":send(sid,visual_threats=[{**MOB,"entity_id":"z2"}],combat={"combat_active_hint":True})
                elif change=="new_input":
                    closing=submit(base,sid,"終了")
                elif change=="stale":threatening(send,sid,observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat())
                else:request(base,"/api/v1/rust-dialogue/interrupt",{"session_id":sid})
                release.set()
                r=wait_for(lambda:row(base,t,{"quiet","cancelled"}))
                assert r["playback_status"]=="cancelled" or r["combat_input_outcome"]=="threat_not_stable",r
                if change=="new_input":
                    closed=wait_for(lambda:row(base,closing,{"quiet"}))
                    assert closed["combat_input_outcome"]=="close_checked" and hud(sid)["state"]=="closed",closed
                    assert len(calls)==2
                assert not hud(sid)["provisional_resume"] and not revisions(folder,sid)
                passed.append("late_classifier_discarded_after_"+change)
            finally:release.set()
    print(json.dumps({"passed":len(passed),"cases":passed,"all_owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
