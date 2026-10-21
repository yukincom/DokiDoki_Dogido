#!/usr/bin/env python3
"""Owned mock HTTP: single AI revision, code checks, pending/adoption and cancellation."""
import json
import re
import threading
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import install, ready, step
from check_workshop_edits import finish, revisions
from check_dialogue import submit, row, wait_for
from test_support import read_jsonl


def proposal(text):
    p=step(text,"propose_revision"); p["purpose"]="improve_wording"
    p["findings"]=[{"line_index":0,"fragment":"さくらのは","problem":"preference","note":"違う表現に直す","confidence":.95}]
    return p


def wire(control, stored, sid, *, bad=False, blocked=None, inspect=False):
    original=control["structured_handler"]; calls=[]
    atom=stored(sid)[0]["materials_snapshot"]["line_sources"][0]["atom_ids"][0]
    def editor(incoming):
        if incoming.get("temperature")==.30:
            calls.append(incoming)
            if blocked: blocked[0].set(); blocked[1].wait(8)
            return {"lines":[{"line_index":0,"expected_text":"別の句" if bad else LINES[0],"replacement_text":"さくらいろ","atom_ids":[atom]}]}
        return original(incoming)
    control["structured_handler"]=editor
    def select(text,prompt,n):
        # 補正後も修正依頼の根拠は原文。相談・検査の根拠は会話理解用の文。
        recognized=re.search(r"今回のプレイヤー発話（認識原文）: ([^\n]+)",prompt)
        original_text=recognized.group(1) if recognized else text
        if text == "その案で":
            from check_workshop_edits import adoption
            return adoption(original_text)
        if text == "今の句":
            return step(text, "show_current")
        if "段階: after_validation" in prompt:
            raise AssertionError("editing must not trigger another conversation call")
        if inspect and "段階: decide" in prompt: return step(text,"inspect",checks=["meter"])
        return proposal(original_text)
    steps=install(control,select)
    return calls,steps


def main():
    passed=[]
    for inspect in [False,True]:
        with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
            sid=ready(base,send,rows); wait_for(lambda:stored(sid));original=stored(sid)
            calls,steps=wire(control,stored,sid,inspect=inspect)
            r=finish(base,send,sid,"上五のさくらのはを別の表現に直して")
            assert r.get("workshop_outcome")=="revision_proposed",(r,log.read_text())
            assert hud(sid)["pending_lines"]==["さくらいろ",*LINES[1:]] and hud(sid)["canonical_lines"]==LINES
            assert hud(sid)["editing"] is True and hud(sid)["selected_line"]==0,hud(sid)
            assert "その案で" in r["text"] and "さくらいろ" in r["text"] and not revisions(folder,sid)
            assert len(calls)==1 and len(steps)==(2 if inspect else 1)
            assert stored(sid)==original
            r=finish(base,send,sid,"その案で")
            assert r.get("workshop_outcome")=="pending_saved",r
            saved=revisions(folder,sid);assert len(saved)==1
            assert saved[0]["source"]=="generated_confirmed" and saved[0]["edit_contract"]=="line_compare_and_swap_v1"
            assert len(saved[0]["line_sources"])==3 and saved[0]["lines"][0]["provenance"]=="generated_confirmed"
            assert saved[0]["lines"][1:]==original[0]["lines"][1:]
            assert stored(sid)==original and hud(sid)["canonical_lines"]==["さくらいろ",*LINES[1:]]
            assert hud(sid)["editing"] is True and hud(sid)["selected_line"]==0,hud(sid)
            persisted = read_jsonl(folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl")
            assert persisted == saved and persisted[0]["source"] == "generated_confirmed"
            passed.append("inspect_then_propose_accept" if inspect else "propose_pending_then_explicit_accept_and_persist")
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wait_for(lambda:stored(sid));original=stored(sid)
        wire(control,stored,sid)
        r=finish(base,send,sid,"上五のさくらのはを別の表現に直して")
        assert r.get("workshop_outcome")=="revision_proposed" and hud(sid)["pending_lines"],r
        pending=hud(sid)["pending_lines"]
        # モデルが会話継続を選ぶ間は未採用案を保持する。
        calls=install(control,lambda text,prompt,n:dict(step(text,"respond"), speech="元の句と案はそのままやで。"))
        for text in ["終了しないよ", "『終了』ってどういう意味？", "終了したらどうなる？", "終了かな？"]:
            r=finish(base,send,sid,text)
            assert r["workshop_action"]=="respond" and r["workshop_reason"]=="accepted",r
            assert hud(sid)["state"]=="open" and hud(sid)["pending_lines"]==pending
            assert hud(sid)["canonical_lines"]==LINES and stored(sid)==original and not revisions(folder,sid)
        assert len(calls)==4
        def close(text,prompt,n):
            reply=step(text,"close_workshop")
            reply["speech"]="今の案を保存して終了したで。"  # 保存成功の誤案内を使わない。
            return reply
        calls=install(control,close)
        r=finish(base,send,sid,"そうおしまいにしてください")
        assert r["workshop_action"]=="close_workshop" and r["workshop_reason"]=="accepted",r
        assert len(calls)==1,calls
        assert hud(sid)["state"]=="closed" and not hud(sid)["pending_lines"],hud(sid)
        assert hud(sid)["editing"] is False and hud(sid)["selected_line"] is None
        assert stored(sid)==original and not revisions(folder,sid)
        assert r["text"]=="元の句はそのままにして、案は採用せずにここで終わるで。",r
        passed.append("pending_close_executes_model_decision_and_discards_only_unadopted_proposal")
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wait_for(lambda:stored(sid));original=stored(sid)
        wire(control,stored,sid)
        finish(base,send,sid,"上五のさくらのはを別の表現に直して")
        pending=hud(sid)["pending_lines"];assert pending
        entered=threading.Event();release=threading.Event()
        def delayed_close(text,prompt,n):
            if text=="今の句":return step(text,"show_current")
            entered.set();release.wait(8)
            return step(text,"close_workshop")
        install(control,delayed_close)
        try:
            old=submit(base,sid,"そうおしまいにしてください");wait_for(entered.is_set)
            newer=submit(base,sid,"今の句");release.set();wait_for(lambda:row(base,newer,{"completed"}))
            assert row(base,old,{"cancelled"}) and hud(sid)["state"]=="open"
            assert hud(sid)["pending_lines"]==pending and hud(sid)["canonical_lines"]==LINES
            assert stored(sid)==original and not revisions(folder,sid)
            passed.append("cancelled_close_cannot_discard_current_pending_proposal")
        finally:release.set()
    for mode in ["bad_cas","semantic_checker_unused"]:
        with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
            sid=ready(base,send,rows);wait_for(lambda:stored(sid))
            calls,steps=wire(control,stored,sid,bad=mode=="bad_cas")
            if mode=="semantic_checker_unused":checks["fail"]=True
            before = len(seen)
            r=finish(base,send,sid,"上五のさくらのはを別の表現に直して")
            assert not revisions(folder,sid) and hud(sid)["canonical_lines"]==LINES,r
            if mode=="semantic_checker_unused":
                assert r.get("workshop_outcome")=="revision_proposed" and hud(sid)["pending_lines"],r
                assert len(calls)==1 and "さくらいろ" in r["text"]
            else:
                assert not hud(sid)["pending_lines"] and "元の句" in r["text"],r
                assert len(calls)==1
            assert len(steps)==1
            # 編集案の意味評価や再応答を含めてもHTTP呼出しは相談1 + 案1だけ。
            assert len([x for x in seen[before:] if x["path"] == "/v1/chat/completions"]) == 2
            passed.append(mode)
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wait_for(lambda:stored(sid));entered=threading.Event();release=threading.Event()
        calls,steps=wire(control,stored,sid,blocked=(entered,release))
        try:
            old=submit(base,sid,"上五のさくらのはを別の表現に直して");wait_for(entered.is_set)
            new=submit(base,sid,"今の句");release.set();wait_for(lambda:row(base,new,{"completed"}))
            assert row(base,old,{"cancelled"}) and not hud(sid)["pending_lines"] and not revisions(folder,sid)
            passed.append("cancelled_editor_cannot_stage_late_result")
        finally:release.set()
    print(json.dumps({"count":len(passed),"passed":passed},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
