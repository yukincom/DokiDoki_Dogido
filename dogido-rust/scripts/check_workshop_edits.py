#!/usr/bin/env python3
"""Rust pending/CAS/save integration with mock model/audio and owned processes."""
import fcntl
import json
import threading
from check_workshop_runtime import install, ready, step, session
from check_haiku_runtime import fixture, LINES
from check_dialogue import request, row, submit, wait_for


def edit(text, index=0, replacement="さくらいろ", reference="上五", fragment=""):
    p=step(text,"stage_player_edit")
    p["purpose"]="improve_wording"
    p["line_reference"]={"found":True,"concept_id":f"line_{index+1}","evidence":reference,"confidence":.95}
    p["line_proposal"]={"found":True,"target_fragment":fragment,"replacement_text":replacement,"evidence":text,"confidence":.95}
    return p


def adoption(text, action="accept_pending", close=False):
    p=step(text,action)
    p["purpose"]="adopt_pending" if action=="accept_pending" else "discard_pending"
    p["close_after_action"]=close
    p["close_evidence"]="ここでおしまい" if close else ""
    return p


def revisions(folder,sid):
    path=folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []


def finish(base,send,sid,text):
    send(sid)
    turn=submit(base,sid,text)
    return wait_for(lambda:row(base,turn,{"completed","failed"}))


def main():
    passed=[]
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls=install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows)
        original=wait_for(lambda:stored(sid))
        r=finish(base,send,sid,"上五を『さくらいろ』にして")
        assert r.get("workshop_outcome")=="player_edit_staged",(r,log.read_text())
        assert hud(sid)["canonical_lines"]==LINES and hud(sid)["pending_lines"]==["さくらいろ",*LINES[1:]]
        assert hud(sid)["editing"] and hud(sid)["selected_line"]==0
        assert not revisions(folder,sid) and stored(sid)==original
        passed.append("stage_displays_pending_without_saving_or_changing_canonical")
        calls=install(control,lambda text,prompt,n:edit(text,2,"あさひかる","下五"))
        r=finish(base,send,sid,"下五を『あさひかる』にして")
        assert r.get("workshop_outcome")=="player_edit_staged",r
        assert hud(sid)["pending_lines"]==["さくらいろ",LINES[1],"あさひかる"]
        assert not revisions(folder,sid)
        passed.append("second_edit_uses_pending_and_preserves_first_edit")
        r=finish(base,send,sid,"今の句")
        assert r["text"]=="さくらいろ\nくろいおのへと\nあさひかる",r
        r=finish(base,send,sid,"終了")
        assert r["workshop_action"]=="ask" and hud(sid)["state"]=="open" and hud(sid)["pending_lines"]
        assert len(calls)==1
        passed.append("show_pending_and_close_requires_explicit_adoption_or_rejection")
        r=finish(base,send,sid,"いい句だね")
        assert r["workshop_action"]=="ask" and hud(sid)["state"]=="open" and hud(sid)["pending_lines"]
        assert not revisions(folder,sid) and stored(sid)==original and len(calls)==1
        passed.append("praise_does_not_adopt_or_discard_pending_edit")
        r=finish(base,send,sid,"採用して")
        assert r.get("workshop_outcome")=="pending_saved",r
        rs=revisions(folder,sid)
        assert len(rs)==1 and len(rs[0]["edits"])==2 and rs[0]["parent_revision_id"] is None
        assert rs[0]["original_reading_text"]=="\n".join(LINES) and rs[0]["source"]=="player_line_confirmed"
        assert rs[0]["lines"][0]["source_atom_ids"]==[] and rs[0]["lines"][0]["provenance"]=="player_explicit"
        assert hud(sid)["canonical_lines"]==["さくらいろ",LINES[1],"あさひかる"] and not hud(sid)["pending_lines"]
        assert stored(sid)==original
        passed.append("explicit_accept_appends_once_promotes_and_keeps_original")
        calls=install(control,lambda text,prompt,n: edit(text,1,"くろいおのもつ","中七"))
        r=finish(base,send,sid,"中七を『くろいおのもつ』にして")
        assert r.get("workshop_outcome")=="player_edit_staged",r
        calls=install(control,lambda text,prompt,n:adoption(text,close=True))
        r=finish(base,send,sid,"その案を採用して、ここでおしまいにしよう")
        assert r.get("workshop_outcome")=="pending_saved" and hud(sid)["state"]=="closed",r
        rs2=revisions(folder,sid)
        assert len(rs2)==2 and rs2[1]["parent_revision_id"]==rs[0]["id"] and rs2[1]["base_text"]==rs[0]["revised_text"]
        passed.append("continued_adoption_links_parent_then_closes_after_save")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls=install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows)
        finish(base,send,sid,"上五を『さくらいろ』にして")
        calls=install(control,lambda text,prompt,n:adoption("採用"))
        for text in ["採用しないよ","『採用』って言っただけ","採用したらどうなる？","採用かな？"]:
            r=finish(base,send,sid,text)
            assert r["workshop_action"]=="fallback" and not revisions(folder,sid),r
            assert hud(sid)["pending_lines"]
        assert len(calls)==4
        passed.append("negative_quoted_conditional_question_do_not_adopt")
        r=finish(base,send,sid,"却下して")
        assert r.get("workshop_outcome")=="pending_rejected" and not hud(sid)["pending_lines"],r
        assert hud(sid)["canonical_lines"]==LINES and not revisions(folder,sid)
        passed.append("explicit_rejection_drops_only_pending_without_model")
        for text,payload in [
            ("上五を『さくら』にして",edit("上五を『さくら』にして",replacement="さくら")),
            ("上五を『さくらいろ』にしないで",edit("上五を『さくらいろ』にしないで")),
            ("上五を直して",edit("上五を直して")),
            ("上五を『あさのいろ』にして",edit("上五を『あさのいろ』にして",replacement="あさのいろ")),
        ]:
            install(control,lambda text,prompt,n,p=payload:p)
            r=finish(base,send,sid,text)
            assert not hud(sid)["pending_lines"] and not revisions(folder,sid),(text,r)
        passed.append("bad_meter_negated_edit_invented_replacement_duplicate_line_rejected")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows)
        finish(base,send,sid,"上五を『さくらいろ』にして")
        path=folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl"
        path.mkdir() # deterministic write failure, not an unrelated service failure
        r=finish(base,send,sid,"採用して")
        assert r.get("workshop_outcome")=="pending_save_failed",r
        assert hud(sid)["canonical_lines"]==LINES and hud(sid)["pending_lines"] and hud(sid)["state"]=="open"
        assert "覚えといた" not in r["text"]
        path.rmdir()
        r=finish(base,send,sid,"採用して")
        assert r.get("workshop_outcome")=="pending_saved" and len(revisions(folder,sid))==1,r
        passed.append("save_failure_preserves_both_versions_and_retry_saves_once")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        entered=threading.Event(); release=threading.Event()
        def blocked(text,prompt,n):
            entered.set(); release.wait(8)
            return edit(text)
        install(control,blocked)
        sid=ready(base,send,rows)
        try:
            old=submit(base,sid,"上五を『さくらいろ』にして")
            wait_for(entered.is_set)
            new=submit(base,sid,"今の句")
            release.set()
            wait_for(lambda:row(base,new,{"completed"}))
            assert row(base,old,{"cancelled"}) and not hud(sid)["pending_lines"] and not revisions(folder,sid)
            passed.append("cancelled_extraction_cannot_stage_late_result")
        finally: release.set()

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows)
        finish(base,send,sid,"上五を『さくらいろ』にして")
        def inspection(text,prompt,n):
            if "段階: decide" in prompt:
                return step(text,"inspect",checks=["source","meter"])
            assert '"verse_kind": "pending"' in prompt and '"source_status": "unavailable"' in prompt
            return step(text,speech="出典の記録がない行があるで。")
        install(control,inspection)
        r=finish(base,send,sid,"出典と音数を確認して")
        assert r["workshop_action"]=="explain" and not revisions(folder,sid),r
        passed.append("inspection_uses_pending_reading_without_borrowing_original_source")
        install(control,lambda text,prompt,n:adoption(text,"reject_pending",close=True))
        r=finish(base,send,sid,"その案を却下して、ここでおしまいにしよう")
        assert r.get("workshop_outcome")=="pending_rejected" and hud(sid)["state"]=="closed" and not revisions(folder,sid),r
        passed.append("reject_and_close_preserves_original_without_saving")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows)
        finish(base,send,sid,"上五を『さくらいろ』にして")
        path=folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl"
        with path.open("a+") as locked:
            fcntl.flock(locked,fcntl.LOCK_EX)
            try:
                turn=submit(base,sid,"採用して")
                wait_for(lambda:'event="workshop_revision_save_started"' in log.read_text())
                assert hud(sid)["pending_lines"] # state lock must not be held by disk I/O
                newer=submit(base,sid,"今の句")
                wait_for(lambda:row(base,turn,{"cancelled"}))
            finally: fcntl.flock(locked,fcntl.LOCK_UN)
        r=wait_for(lambda:row(base,newer,{"completed"}))
        assert r["text"]=="さくらいろ\nくろいおのへと\nあさのいろ",r
        assert len(revisions(folder,sid))==1 and not hud(sid)["pending_lines"]
        assert not any(p["turn_id"]==turn for p in session(base,sid)["workshop_history"])
        passed.append("authorized_save_finishes_through_speech_cancel_without_blocking_snapshot")

    with fixture(memory_enabled=False) as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit(text))
        sid=ready(base,send,rows)
        finish(base,send,sid,"上五を『さくらいろ』にして")
        r=finish(base,send,sid,"採用して")
        assert r.get("workshop_outcome")=="pending_save_failed" and hud(sid)["pending_lines"] and not revisions(folder,sid),r
        passed.append("disabled_memory_never_claims_saved_or_discards_pending")

    print(json.dumps({"passed":passed,"count":len(passed)},ensure_ascii=False,indent=2))


if __name__=="__main__": main()
