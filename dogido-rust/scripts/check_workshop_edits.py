#!/usr/bin/env python3
"""Rust pending/CAS/save integration with mock model/audio and owned processes."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
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


def edit_or_command(text, **kwargs):
    if text in {"直った？", "今の句"}:
        return step(text, "show_current")
    if text == "終了でお願いします":
        return step(text, "close_workshop")
    if text == "採用して":
        return adoption(text)
    return edit(text, **kwargs)


def revisions(folder,sid):
    path=folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []


def finish(base,send,sid,text):
    send(sid)
    turn=submit(base,sid,text)
    return wait_for(lambda:row(base,turn,{"completed","failed"}))


@contextmanager
def token_fixture(status):
    """既存token IPCだけを模擬し、実機の辞書有無に依存しない編集試験。"""
    with tempfile.TemporaryDirectory(prefix="dogido-workshop-tokens-") as temp:
        folder = Path(temp)
        scripts = Path(__file__).resolve().parent
        for source in scripts.glob("*.py"):
            if source.name != "tts_tokens_worker.py":
                (folder / source.name).symlink_to(source)
        (folder / "token_status").write_text(status)
        (folder / "tts_tokens_worker.py").write_text('''import json,sys
from pathlib import Path
folder=Path(__file__).parent
readings={"桜の葉":"サクラノハ","桜色":"サクライロ"}
for line in sys.stdin:
    frame=json.loads(line)
    assert frame["op"]=="tts_tokens" and frame["schema_version"]==1
    with (folder/"requests.jsonl").open("a") as out:
        out.write(json.dumps(frame,ensure_ascii=False)+"\\n")
    text=frame["text"]
    status=folder.joinpath("token_status").read_text()
    reading=readings.get(text)
    status=status if reading else "unavailable"
    tokens=[] if status!="ok" else [{"surface":text,"kana":reading,"pron":reading,"goshu":None,"pos1":None}]
    print(json.dumps({"schema_version":1,"request_id":frame["request_id"],"status":status,"tokens":tokens}),flush=True)
''')
        yield ("--helper", str(folder / "haiku_tokens.py")), folder


def main():
    passed=[]
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit_or_command(text))
        sid=ready(base,send,rows); original=stored(sid)
        (folder/"player_mode").write_text("slow")
        turn=submit(base,sid,"上五を『さくらいろ』にして")
        wait_for(lambda:row(base,turn,{"started"}))
        assert hud(sid)["canonical_lines"]==["さくらいろ",*LINES[1:]] and not hud(sid)["pending_lines"]
        first=revisions(folder,sid)
        assert len(first)==1 and first[0]["parent_revision_id"] is None and stored(sid)==original
        assert first[0]["lines"][0]["source_atom_ids"]==[] and first[0]["source"]=="player_line_confirmed"
        passed.append("explicit_edit_saved_and_displayed_before_speech_finishes_preserving_original")
        (folder/"player_mode").write_text("ok")
        r=finish(base,send,sid,"直った？")
        assert r["workshop_action"]=="show_current" and "さくらいろ" in r["text"]
        assert row(base,turn,{"cancelled"}) and len(revisions(folder,sid))==1
        passed.append("confirmation_reads_current_without_reapplying_after_cancelled_speech")
        install(control,lambda text,prompt,n:edit_or_command(text,index=2,replacement="あさひかる",reference="下五"))
        r=finish(base,send,sid,"下五を『あさひかる』にして")
        assert r["workshop_outcome"]=="player_edit_saved" and not hud(sid)["pending_lines"],r
        rs=revisions(folder,sid)
        assert len(rs)==2 and rs[1]["parent_revision_id"]==first[0]["id"]
        assert rs[1]["base_text"]==first[0]["revised_text"] and len(rs[1]["edits"])==1
        assert hud(sid)["canonical_lines"]==["さくらいろ",LINES[1],"あさひかる"]
        passed.append("next_edit_uses_new_canonical_and_links_revision_parent")
        r=finish(base,send,sid,"終了でお願いします")
        assert r["workshop_action"]=="close_workshop" and hud(sid)["state"]=="closed"
        passed.append("polite_explicit_close_needs_no_second_adoption")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        sid=ready(base,send,rows)
        for text,payload in [
            ("上五を『さくら』にして",edit("上五を『さくら』にして",replacement="さくら")),
            ("上五を『さくらいろ』にしないで",edit("上五を『さくらいろ』にしないで")),
            ("上五を直して",edit("上五を直して")),
            ("上五を『あさのいろ』にして",edit("上五を『あさのいろ』にして",replacement="あさのいろ")),
            ("『上五をさくらいろにして』って言っただけ",edit("上五をさくらいろにして")),
            ("上五をさくらいろにしたらどうなる？",edit("上五をさくらいろにしたらどうなる？")),
        ]:
            install(control,lambda text,prompt,n,p=payload:p)
            r=finish(base,send,sid,text)
            assert hud(sid)["canonical_lines"]==LINES and not hud(sid)["pending_lines"] and not revisions(folder,sid),(text,r)
        passed.append("bad_meter_negation_invented_word_duplicate_quote_and_question_never_save")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit_or_command(text));sid=ready(base,send,rows)
        path=folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl";path.mkdir()
        r=finish(base,send,sid,"上五を『さくらいろ』にして")
        assert r["workshop_outcome"]=="pending_save_failed",r
        assert hud(sid)["canonical_lines"]==LINES and hud(sid)["pending_lines"] and "保存できん" in r["text"]
        path.rmdir()
        r=finish(base,send,sid,"採用して")
        assert r["workshop_outcome"]=="pending_saved" and len(revisions(folder,sid))==1,r
        passed.append("save_failure_keeps_original_and_validated_proposal_for_explicit_retry")

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        entered=threading.Event();release=threading.Event()
        def blocked(text,prompt,n):
            entered.set();release.wait(8);return edit_or_command(text)
        install(control,blocked);sid=ready(base,send,rows)
        try:
            old=submit(base,sid,"上五を『さくらいろ』にして");wait_for(entered.is_set)
            newer=submit(base,sid,"今の句");release.set();wait_for(lambda:row(base,newer,{"completed"}))
            assert row(base,old,{"cancelled"}) and hud(sid)["canonical_lines"]==LINES and not revisions(folder,sid)
            passed.append("cancelled_extraction_cannot_apply_late_result")
        finally:release.set()

    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit_or_command(text));sid=ready(base,send,rows)
        path=folder/"memory/sessions"/sid/"long_term/haiku_revisions.jsonl"
        with path.open("a+") as locked:
            fcntl.flock(locked,fcntl.LOCK_EX)
            try:
                turn=submit(base,sid,"上五を『さくらいろ』にして")
                wait_for(lambda:'event="workshop_revision_save_started"' in log.read_text())
                assert hud(sid)["canonical_lines"]==LINES
                newer=submit(base,sid,"今の句");wait_for(lambda:row(base,turn,{"cancelled"}))
            finally:fcntl.flock(locked,fcntl.LOCK_UN)
        r=wait_for(lambda:row(base,newer,{"completed"}))
        assert r["text"]=="さくらいろ\nくろいおのへと\nあさのいろ" and len(revisions(folder,sid))==1,r
        assert not any(p["turn_id"]==turn for p in session(base,sid)["workshop_history"])
        passed.append("authorized_save_finishes_through_cancellation_without_blocking_snapshot")

    with fixture(memory_enabled=False) as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        install(control,lambda text,prompt,n:edit_or_command(text));sid=ready(base,send,rows)
        r=finish(base,send,sid,"上五を『さくらいろ』にして")
        assert r["workshop_outcome"]=="pending_save_failed" and hud(sid)["canonical_lines"]==LINES
        assert hud(sid)["pending_lines"] and not revisions(folder,sid)
        passed.append("disabled_memory_keeps_canonical_and_never_claims_saved")

    with token_fixture("ok") as (args, token_dir), fixture(extra_args=args) as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        calls=install(control,lambda text,prompt,n:edit(text,
            replacement="桜色" if "桜色" in text else "さくらさく", reference="", fragment="桜の葉" if "桜の葉" in text else "さくらいろ"))
        sid=ready(base,send,rows);original=stored(sid)
        for text,want in [("桜の葉を桜色に変えて","桜色"),("さくらいろをさくらさくに変えて","さくらさく")]:
            r=finish(base,send,sid,text)
            assert r["workshop_outcome"]=="player_edit_saved" and len(r["llm_reports"])==1,r
            assert hud(sid)["canonical_lines"]==[want,*LINES[1:]] and not hud(sid)["pending_lines"]
        assert len(revisions(folder,sid))==2 and stored(sid)==original
        assert len(calls)==2
        first=revisions(folder,sid)[0]["lines"][0]
        assert first["surface_text"]=="桜色" and first["reading_text"]=="さくらいろ"
        token_requests=[json.loads(line) for line in (token_dir/"requests.jsonl").read_text().splitlines()]
        assert {"桜の葉", "桜色"} <= {request["text"] for request in token_requests}
        passed.append("model_selected_current_fragment_and_kanji_edit_apply_once")
        def inspection(text,prompt,n):
            if "段階: decide" in prompt:return step(text,"inspect",checks=["source","meter"])
            assert '\"verse_kind\": \"canonical\"' in prompt and '\"source_status\": \"unavailable\"' in prompt
            return step(text,speech="出典の記録がない行があるで。")
        install(control,inspection)
        r=finish(base,send,sid,"出典と音数を確認して")
        assert r["workshop_action"]=="explain" and len(revisions(folder,sid))==2,r
        passed.append("inspection_reads_new_canonical_without_borrowing_old_source")

    with token_fixture("unavailable") as (args, token_dir), fixture(extra_args=args) as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        def no_dictionary(text,prompt,n):
            if "段階: after_validation" in prompt:
                assert "not_hiragana" in prompt and "rejected" in prompt
                return step(text,"respond","読みが確定できんから、ひらがなで教えてな。")
            return edit(text,replacement="桜色")
        calls=install(control,no_dictionary)
        sid=ready(base,send,rows);original=stored(sid)
        r=finish(base,send,sid,"上五を『桜色』にして")
        assert r["workshop_action"]=="respond" and len(calls)==2,r
        assert r["workshop_steps"][0]["validation_codes"]==["not_hiragana"] and "読みが確定できん" in r["text"],r
        assert hud(sid)["canonical_lines"]==LINES and not hud(sid)["pending_lines"]
        assert stored(sid)==original and not revisions(folder,sid)
        assert any(json.loads(line)["text"]=="桜色" for line in (token_dir/"requests.jsonl").read_text().splitlines())
        passed.append("unavailable_dictionary_exposes_reading_failure_and_never_saves_or_repeats_edit")

    # Model-generated suggestions remain unadopted until the player decides.
    from check_workshop_revision import wire
    with fixture() as (base,process,log,control,seen,gate,drafting,checks,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wire(control,stored,sid)
        finish(base,send,sid,"上五のさくらのはを別の表現に直して")
        install(control,lambda text,prompt,n:adoption("採用"))
        for text in ["採用しないよ","『採用』って言っただけ","採用したらどうなる？","採用かな？"]:
            r=finish(base,send,sid,text)
            assert r["workshop_action"]=="fallback" and hud(sid)["pending_lines"] and not revisions(folder,sid),r
        bad_close=install(control,lambda text,prompt,n:step(text,"close_workshop"))
        r=finish(base,send,sid,"お開きー")
        assert r["workshop_action"]=="fallback" and len(bad_close)==2,r
        assert "採用するか、元の句に戻すか" in r["text"] and hud(sid)["state"]=="open",r
        assert hud(sid)["canonical_lines"]==LINES and hud(sid)["pending_lines"] and not revisions(folder,sid)
        passed.append("invalid_model_close_keeps_pending_and_falls_back_to_adoption_confirmation")
        install(control,lambda text,prompt,n:step(text,"ask",speech="今の案を使うか、もう少し話すか聞かせてな。"))
        for text in ["終了","いい句だね"]:
            r=finish(base,send,sid,text)
            assert r["workshop_action"]=="ask" and hud(sid)["state"]=="open" and not revisions(folder,sid),r
        install(control,lambda text,prompt,n:adoption(text,"reject_pending",close=True))
        r=finish(base,send,sid,"その案を却下して、ここでおしまいにしよう")
        assert r["workshop_outcome"]=="pending_rejected" and hud(sid)["state"]=="closed" and not revisions(folder,sid),r
        passed.append("generated_proposal_requires_adoption_and_reject_close_preserves_original")
    print(json.dumps({"passed":passed,"count":len(passed)},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
