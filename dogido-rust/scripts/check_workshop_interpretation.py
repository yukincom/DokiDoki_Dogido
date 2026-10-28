#!/usr/bin/env python3
"""解釈済み語句を、相談・再編集・採用・保存まで通す模擬HTTP回帰試験。"""
import json
from check_dialogue import row, submit, wait_for
from check_haiku_runtime import LINES
from check_workshop_fixture import fixture
from check_workshop_runtime import install, ready, step, session
from check_workshop_edits import edit, adoption, finish, revisions
from check_workshop_revision import wire
from check_memory_runtime import records


def main():
    passed=[]
    text="上の行は、あの花の色と春風の表現にするのはどう？"
    wording="桜色 春の風 あたたかい" + "、ゆっくりと景色を思い出す" * 8
    with fixture(bert_labels={text:"senryu_line_upper"}) as (base,_,log,control,_,_,_,_,send,hud,rows,stored,folder):
        def discuss(raw,prompt,n):
            if raw=="それでいこう":
                return {"action":"stage_conversation_candidate"}
            return {"action":"respond","speech":"その表現もええな。",
                    "line_proposal":{"found":True,"target_fragment":"","replacement_text":wording}}
        calls=install(control,discuss);sid=ready(base,send,rows);original=stored(sid)
        first=finish(base,send,sid,text)
        assert first['workshop_action']=='respond' and hud(sid)['canonical_lines']==LINES,first
        assert not revisions(folder,sid)
        result=finish(base,send,sid,"それでいこう")
        assert result['workshop_outcome']=='player_edit_saved',result
        saved=revisions(folder,sid)
        assert len(saved)==1 and saved[0]['lines'][0]['surface_text']==wording,saved
        assert hud(sid)['canonical_lines']==[wording,*LINES[1:]] and stored(sid)==original
        assert len(calls)==2
        passed.append('interpreted_candidate_without_raw_words_or_metadata_preserves_full_text_on_adoption')

    with fixture(bert_labels={"別の表現で最後の行の案を考えて":"senryu_line_lower"}) as (base,_,log,control,_,_,_,_,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wait_for(lambda:stored(sid));original=stored(sid)
        install(control,lambda text,prompt,n:edit(text,replacement="松明"))
        result=finish(base,send,sid,"上五を『桜色』にして")
        assert result['workshop_outcome']=='player_edit_saved',result
        assert revisions(folder,sid)[0]['lines'][0]['source_atom_ids']==[]
        previous=control['structured_handler'];generated=[]
        def generate(incoming):
            if incoming.get('temperature')==.30:
                generated.append(incoming)
                return {'lines':[{'line_index':2,'expected_text':LINES[2],'replacement_text':'夜の色、光る'}]}
            return previous(incoming)
        control['structured_handler']=generate
        def choose(text,prompt,n):
            if text=='その案で':return {'action':'accept_pending'}
            return {'action':'propose_revision','findings':[{'line_index':2,'problem':'preference'}]}
        calls=install(control,choose)
        result=finish(base,send,sid,'別の表現で最後の行の案を考えて')
        assert result.get('workshop_outcome')=='revision_proposed',result
        assert hud(sid)['pending_lines']==['松明',LINES[1],'夜の色、光る'],hud(sid)
        result=finish(base,send,sid,'その案で')
        saved=revisions(folder,sid)
        assert result['workshop_outcome']=='pending_saved' and len(saved)==2,result
        assert hud(sid)['canonical_lines']==['松明',LINES[1],'夜の色、光る']
        assert saved[1]['lines'][0]==saved[0]['lines'][0] and saved[1]['lines'][2]['source_atom_ids']==[]
        assert stored(sid)==original and len(generated)==1 and len(calls)==2
        passed.append('new_revision_after_player_edit_accepts_missing_sources_and_preserves_surface')

    text='真ん中を例の景色の言葉にして'
    wording='桜色 春の風 あたたかい'
    with fixture(bert_labels={text:'senryu_change_middle'}) as (base,_,log,control,_,_,_,_,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wait_for(lambda:stored(sid));original=stored(sid)
        wire(control,stored,sid)
        first=finish(base,send,sid,'上五のさくらのはを別の表現に直して')
        assert first['workshop_outcome']=='revision_proposed',first
        def edit_then_adopt(raw,prompt,n):
            if raw=='採用して':return {'action':'accept_pending'}
            payload=edit(raw,index=1,replacement=wording)
            payload.pop('confidence');payload.pop('evidence')
            return payload
        calls=install(control,edit_then_adopt)
        result=finish(base,send,sid,text)
        assert result['workshop_outcome']=='player_edit_staged', (result,log.read_text())
        assert hud(sid)['canonical_lines']==LINES and hud(sid)['pending_lines']==['さくらいろ',wording,LINES[2]]
        assert not revisions(folder,sid)
        result=finish(base,send,sid,'採用して');saved=revisions(folder,sid)
        assert result['workshop_outcome']=='pending_saved' and len(saved)==1,result
        assert hud(sid)['canonical_lines']==['さくらいろ',wording,LINES[2]]
        assert saved[0]['lines'][0]['provenance']=='generated_confirmed'
        assert saved[0]['lines'][0]['source_atom_ids']
        assert saved[0]['lines'][1]['provenance']=='player_explicit' and not saved[0]['lines'][1]['source_atom_ids']
        assert stored(sid)==original and len(calls)==2
        passed.append('generated_pending_accepts_spaced_player_edit_keeps_provenance_and_saves_only_after_adoption')

    with fixture() as (base,_,log,control,_,_,_,_,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);wait_for(lambda:stored(sid));original=stored(sid)
        wire(control,stored,sid)
        result=finish(base,send,sid,'上五のさくらのはを別の表現に直して')
        assert result['workshop_outcome']=='revision_proposed',result
        calls=install(control,lambda text,prompt,n:edit(text,replacement=LINES[0]))
        result=finish(base,send,sid,"上五を『桜色』にして")
        assert result['workshop_action']=='reject_pending' and result['workshop_outcome']=='pending_rejected',result
        assert hud(sid)['state']=='open' and hud(sid)['canonical_lines']==LINES and not hud(sid)['pending_lines']
        assert stored(sid)==original and not revisions(folder,sid) and len(calls)==1
        passed.append('returning_pending_to_original_clears_proposal_without_saving_or_closing')

    with fixture() as (base,_,log,control,_,_,_,_,send,hud,rows,stored,folder):
        sid=ready(base,send,rows);before=records(folder,'lessons')
        calls=install(control,lambda text,prompt,n:{'action':'clear_lessons'})
        text='今までの注意はひとまず脇に置いて、一緒に考えよう'
        (folder/'player_mode').write_text('slow')
        send(sid);turn=submit(base,sid,text)
        wait_for(lambda:row(base,turn,{'started'}))
        assert submit(base,sid,text)==turn
        result=wait_for(lambda:row(base,turn,{'completed'}))
        assert result['workshop_action']=='clear_lessons' and result['memory_outcome']=='saved',result
        after=records(folder,'lessons')
        assert len(after)==len(before)+1 and after[-1]['polarity']=='loosen',after
        assert hud(sid)['state']=='open' and hud(sid)['canonical_lines']==LINES and len(calls)==1
        assert records(folder,'lessons')==after
        passed.append('interpreted_lesson_clear_executes_once_without_raw_phrase_whitelist')
    print(json.dumps({'count':len(passed),'passed':passed},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
