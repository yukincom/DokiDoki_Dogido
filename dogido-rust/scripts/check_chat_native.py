#!/usr/bin/env python3
"""Native ordinary Session/HTTP path against owned mock model/audio only."""
import argparse, json, re, tempfile, time
from pathlib import Path
from check_dialogue import dependencies, running, register, request, submit, wait_for, row, snapshot
ROOT=Path(__file__).resolve().parents[1]

def objects(incoming):
    out=[]; decoder=json.JSONDecoder()
    for message in incoming['messages']:
        for match in re.finditer(r'\{\s*"turn_id"\s*:',message['content']):
            try: value,_=decoder.raw_decode(message['content'][match.start():])
            except ValueError: continue
            if value.get('role') in {'user','assistant'}:out.append(value)
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);args=p.parse_args()
    passed=[]
    with tempfile.TemporaryDirectory(prefix='dogido-chat-native-') as temp, dependencies() as (dep,control,seen):
      with running(args.binary.resolve(),Path(temp),dep,haiku_settings={'enabled':False,'memory_enabled':False}) as (base,process,log):
        sid=register(base)
        def calls():return [v['body'] for v in seen if v['path']=='/v1/chat/completions']
        def say(text):
            turn=submit(base,sid,text)
            result=wait_for(lambda:row(base,turn,{'completed','failed','unsupported'}))
            assert result['playback_status']=='completed',(result,log.read_text())
            return result
        first=say('こんにちは')
        assert [c['max_tokens'] for c in calls()]==[640,72],calls()
        assert first['text']==control['leaf']
        assert 'event="helper_started"' not in log.read_text()
        assert 'chat_worker_finished' in log.read_text()
        passed.append('ordinary_http_one_plan_one_leaf_without_dialogue_helper')
        n=len(calls()); sequence=iter(['Sure, here is an explanation.','せやな、いっしょに歩こうか。'])
        def candidate(incoming):
            if incoming['max_tokens']!=72:return None
            return {'id':'mock','object':'chat.completion','created':0,'model':'mock-model',
                'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'action':'speak','speech':next(sequence)},ensure_ascii=False)},'finish_reason':'stop'}]}
        control['completion_override']=candidate
        reconsider=say('今日はのんびり歩こう')
        assert reconsider['text']=='せやな、いっしょに歩こうか。',reconsider
        assert [c['max_tokens'] for c in calls()[n:]]==[640,72,72]
        del control['completion_override']
        passed.append('invalid_candidate_reconsidered_once')
        n=len(calls())
        def presence(incoming):
            if incoming['max_tokens']!=640:return None
            current=[r for r in objects(incoming) if r['turn_id']=='current'][-1]
            return {'action':'check_entity_presence','focus':'猫の在否','entity_query':'猫','evidence':[{'turn_id':'current','quote':current['text']}],'confidence':.95,'repair':None}
        control['structured_handler']=presence
        fixed=say('猫はいる？')
        assert [c['max_tokens'] for c in calls()[n:]]==[640],calls()[n:]
        assert fixed['text']=='どれのことか、今の材料だけやと分からへんわ。',fixed
        del control['structured_handler']
        passed.append('unobserved_presence_is_code_fixed_without_leaf')
        # Repair is returned by the native plan and annotated only by the owner.
        control['leaf']='一位になれんでもええやん。'
        say('一位になるのは無理やな')
        n=len(calls())
        def repair(incoming):
            if incoming['max_tokens']!=640:return None
            rows=objects(incoming)
            target=[r for r in rows if r['role']=='assistant' and r['text']==control['leaf']][-1]
            return {'action':'repair_conversation','focus':'本人の言い直し','entity_query':'',
                'evidence':[{'turn_id':'current','quote':'違う'},{'turn_id':target['turn_id'],'quote':target['text']}],
                'confidence':.95,'repair':{'target_turn_id':target['turn_id'],'target_quote':target['text'],'signal_quote':'違う','replacement_quote':'仲間になるのは無理ってこと'}}
        control['structured_handler']=repair
        # Keep the prior target above stable while supplying separate leaf wording.
        def repair_reply(incoming):
            if incoming['max_tokens']!=72:return None
            return {'id':'mock','object':'chat.completion','created':0,'model':'mock-model','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'action':'speak','speech':'仲間になる話やったんやな。取り違えてすまん。'},ensure_ascii=False)},'finish_reason':'stop'}]}
        control['completion_override']=repair_reply
        repaired=say('違う、仲間になるのは無理ってこと')
        assert repaired['llm_reports'][0]['plan']['action']=='repair_conversation',repaired
        hist=snapshot(base)['sessions'][0]['history']
        assert any(r.get('repair_action')=='repair_conversation' for r in hist),hist
        del control['structured_handler'];del control['completion_override']
        passed.append('repair_survives_native_plan_leaf_and_completed_owner_history')
        n=len(calls());knowledge=say('枕詞って何？')
        assert len(calls())==n and knowledge['knowledge_status']=='found',knowledge
        assert knowledge['references']
        passed.append('canonical_knowledge_has_no_model_or_dialogue_helper')
        control['delay']=.7;n=len(calls())
        turn=submit(base,sid,'少しゆっくり話そう')
        wait_for(lambda:len(calls())>n)
        request(base,'/api/v1/rust-dialogue/interrupt',{'session_id':sid})
        wait_for(lambda:row(base,turn,{'cancelled'}))
        time.sleep(.85)
        assert len(calls())==n+1,calls()[n:]
        assert not any(r.get('turn_id')==turn+':reply' for r in snapshot(base)['sessions'][0]['history'])
        passed.append('cancelled_planner_never_starts_leaf_or_commits_reply')
        assert 'event="helper_started"' not in log.read_text(),log.read_text()
      assert process.poll()==0
    out=ROOT/'reports/chat-native-check.json';out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'passed':passed,'model':'mock','tts':'mock','playback':'mock','all_owned_processes_stopped':True},ensure_ascii=False,indent=2)+'\n')
    print(f'PASS {len(passed)} native chat HTTP checks; all owned processes stopped')
if __name__=='__main__':main()
