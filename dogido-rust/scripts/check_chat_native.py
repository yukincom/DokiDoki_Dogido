#!/usr/bin/env python3
"""通常対話の一回生成・形式補完・沈黙履歴を、実HTTPと模擬LLM/音声で確認する。"""
import argparse, json, tempfile, time
from pathlib import Path
from check_dialogue import dependencies, running, register, request, submit, wait_for, row, snapshot
ROOT=Path(__file__).resolve().parents[1]
SILENCE='難しい話だったので、ドギドは沈黙を選んだ'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);args=p.parse_args()
    passed=[]
    with tempfile.TemporaryDirectory(prefix='dogido-chat-native-') as temp, dependencies() as (dep,control,seen):
      with running(args.binary.resolve(),Path(temp),dep,haiku_settings={'enabled':False,'memory_enabled':False}) as (base,process,log):
        sid=register(base)
        def calls():return [v['body'] for v in seen if v['path']=='/v1/chat/completions']
        def audio():return [v for v in seen if v['path'].startswith('/audio_query?')]
        def history():return next(s['history'] for s in snapshot(base)['sessions'] if s['session_id']==sid)
        def say(text, status='completed', count=1):
            start=len(calls());turn=submit(base,sid,text)
            result=wait_for(lambda:row(base,turn,{'completed','quiet','failed','unsupported'}))
            assert result['playback_status']==status,(result,log.read_text())
            assert len(calls())-start==count,calls()[start:]
            return result
        first=say('こんにちは')
        assert [c['max_tokens'] for c in calls()]==[72],calls()
        assert first['text']==control['leaf'] and len(first['llm_reports'])==1
        assert 'event="helper_started"' not in log.read_text()
        passed.append('ordinary_http_one_model_call')
        for text in ['うん','Sure, here is an explanation.','そこのエンダーマン、元気やな。','土の匂いがするわ。']:
            control['leaf']=text
            assert say('少し話そう。'+str(len(calls())))['text']==text
        passed.append('short_foreign_and_unobserved_content_is_not_regenerated')
        control['leaf']='そこまでは分からへん。'
        assert say('猫はいる？')['text']==control['leaf']
        passed.append('target_choice_and_reply_share_one_call')
        control['leaf']='同じや。同じや。同じや。'
        audio_before=len(audio());bad=say('続きも聞かせて','quiet')
        assert not bad['text'] and bad['dialogue_outcome']=='rejected'
        assert len(audio())==audio_before
        assert history()[-1]=={'turn_id':bad['turn_id']+':reply','role':'event','reaction':'story','text':SILENCE},history()
        assert all(control['leaf'] not in r['text'] for r in history())
        control['leaf']='そばにおるで。'
        say('じゃあ一緒にいよう')
        prompt=json.dumps(calls()[-1],ensure_ascii=False)
        assert SILENCE in prompt and '同じや。同じや。同じや。' not in prompt
        passed.append('broken_output_is_quiet_and_only_silence_event_reaches_next_prompt')
        for raw,expected,reason in [
            ('{"action":"speak"}', '', 'stop'),
            ('{"action":"speak","speech":"そや', 'そや', 'length'),
            ('{"action":"speak","speech":123}', '123', 'stop'),
        ]:
            control['completion_override']=lambda incoming,raw=raw,reason=reason: {'id':'mock','object':'chat.completion','created':0,'model':'mock-model','choices':[{'index':0,'message':{'role':'assistant','content':raw},'finish_reason':reason}]}
            answer=say('今の気分を教えて。'+str(len(calls())),'completed' if expected else 'quiet')
            assert answer['text']==expected,answer
        control.pop('completion_override')
        passed.append('missing_speech_truncation_and_numeric_speech_are_handled_in_code')
        control['leaf']='一位になれんでもええやん。'
        say('一位になるのは無理やな')
        control['leaf']='仲間になる話やったんやな。取り違えてすまん。'
        answer=say('違う、仲間になるのは無理ってこと')
        assert answer['text']==control['leaf']
        assert '一位になれんでもええやん。' in json.dumps(calls()[-1],ensure_ascii=False)
        assert history()[-1]['role']=='assistant'
        passed.append('human_correction_uses_existing_history_in_one_call')
        knowledge=say('枕詞って何？',count=0)
        assert knowledge['knowledge_status']=='found' and knowledge['references']
        passed.append('canonical_knowledge_still_has_no_model_call')
        control['delay']=.7;n=len(calls())
        turn=submit(base,sid,'少しゆっくり話そう')
        wait_for(lambda:len(calls())>n)
        request(base,'/api/v1/rust-dialogue/interrupt',{'session_id':sid})
        wait_for(lambda:row(base,turn,{'cancelled'}));time.sleep(.85)
        assert len(calls())==n+1
        assert not any(r.get('turn_id')==turn+':reply' for r in history())
        passed.append('cancelled_generation_has_no_reply_or_silence_history')
      assert process.poll()==0
    out=ROOT/'reports/chat-native-check.json';out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'passed':passed,'model':'mock','tts':'mock','playback':'mock','all_owned_processes_stopped':True},ensure_ascii=False,indent=2)+'\n')
    print(f'PASS {len(passed)} native chat HTTP checks; all owned processes stopped')
if __name__=='__main__':main()
