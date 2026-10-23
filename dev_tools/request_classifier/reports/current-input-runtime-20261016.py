"""新43の現在発話限定と、完了後のお礼から操作を再発行しないことを隔離HTTPで検査する。"""
import argparse,hashlib,json,sys,tempfile,time,threading
from pathlib import Path
from contextlib import contextmanager
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--model-root',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
args=parser.parse_args()
root=args.root.resolve(); model=args.model_root.resolve(); out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root/'dogido-rust/scripts'))
import check_dialogue as h
import check_horse_recall as horse
binary=root/'dogido-rust/target/release/dogido-rust'
settings={'model_root':str(model),'python':str(root/'.dogido_tools/request_classifier/.venv/bin/python')}
report={'scope':'real v1.9 seed43 CPU + real Rust HTTP; mocked LLM, Minecraft, TTS; no audio',
 'manifest_sha256':hashlib.sha256((model/'adopted-model.json').read_bytes()).hexdigest(),
 'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'sequences':[]}
original=h.running
@contextmanager
def running(*a,**kw):
 kw['extra_args']=(*kw.get('extra_args',()),'--request-classifier-settings',json.dumps(settings),'--no-audio')
 with original(*a,**kw) as value:yield value
h.running=running

class CandidatePlan:
 def __init__(self):
  self.calls=[];self.errors=[];self.current='';self.release=threading.Event();self.release.set()
 def __call__(self,incoming):
  self.calls.append(incoming)
  names=[t['function']['name'] for t in incoming.get('tools',[])]
  assert names in ([],['select_horse']),names
  if names:
   return {'role':'assistant','content':None,'tool_calls':[{'id':f'select-{len(self.calls)}','type':'function','function':{'name':'select_horse','arguments':json.dumps({'candidate_id':'h1','evidence':self.current,'confidence':.99},ensure_ascii=False)}}]}
  return {'action':'speak','speech':'うん、来てくれてよかったな。'}

def route(url,sid,text,sequence):
 a=h.request(url,'/api/v1/player-input',{'session_id':sid,'source':'text','text':text})
 assert a['accepted'],a
 def done():
  row=next((r for r in h.snapshot(url)['utterances'] if r['turn_id']==a['turn_id']),{})
  return row if 'forwarded_input' in row else None
 row=h.wait_for(done,timeout=15)
 classification=row['request_classification']
 sequence['inputs'].append({'text':text,'classification':classification,'forwarded_input':row['forwarded_input']})
 return row

def ack(url,sid,text=None):
 def available():
  rows=[r for r in h.snapshot(url)['utterances'] if r['session_id']==sid and r.get('display_id') and not r.get('text_displayed')]
  if text is not None:rows=[r for r in rows if r.get('text')==text]
  return rows
 rows=h.wait_for(available)
 for row in rows:
  h.acknowledge(url,sid,row)
  h.wait_for(lambda:next((r for r in h.snapshot(url)['utterances'] if r['turn_id']==row['turn_id'] and r.get('playback_status') in ('completed','audio_disabled')),None))
 return rows

def regular_reply(url,sid,row):
 turn=row['forwarded_input']['turn_id']
 reply=h.wait_for(lambda:h.display_row(url,turn))
 h.acknowledge(url,sid,reply)
 return reply['text']

try:
 texts=['ありがとう','そうだね来たね','こっちが来ました']
 plan=CandidatePlan()
 seq={'kind':'horse','inputs':[],'unexpected_actions':[]}
 with horse.fixture(h,binary,plan) as (url,sid,send,rows,history,stories,state,seen):
  plan.current=horse.RECALL
  request_row=route(url,sid,horse.RECALL,seq)
  assert request_row['request_classification']['prediction']['label']=='recall_mount',request_row
  command=horse.get_command(h,send)
  send(results=[horse.result(command)])
  h.wait_for(lambda:stories(horse.SUCCESS_STORY))
  ack(url,sid,'こっちへ来たで。')
  assert any(r.get('text')=='こっちへ来たで。' for r in history()),history()
  seq['completed_command_type']=command['type']
  seq['history_after_completion']=history()
  for text in texts:
   assert not send().get('commands')
   plan.current=text
   row=route(url,sid,text,seq)
   label=row['request_classification']['prediction']['label']
   if label in ('not_request','other_request'):
    regular_reply(url,sid,row)
    horse.quiet_commands(send,seconds=.15)
   else:
    assert label=='recall_mount',row
    extra=horse.get_command(h,send)
    seq['unexpected_actions'].append({'text':text,'predicted':label,'command_type':extra['type']})
    send(results=[horse.result(extra)])
    ack(url,sid,'こっちへ来たで。')
  seq['selection_llm_calls']=sum(any(t['function']['name']=='select_horse' for t in c.get('tools',[])) for c in plan.calls)
  assert seq['selection_llm_calls']==1+len(seq['unexpected_actions'])
  seq['new_commands_after_thanks']=len(seq['unexpected_actions'])
  seq['history_at_end']=history()
 report['sequences'].append(seq)

 seq={'kind':'sword','inputs':[]}
 with tempfile.TemporaryDirectory(prefix='dogido-current-input-') as temp,h.dependencies() as (dep,control,seen):
  with h.running(binary,Path(temp),dep,haiku_settings={'enabled':False,'memory_enabled':False}) as (url,process,log):
   sid=h.request(url,'/api/v1/adapter-sessions',{'adapter_name':'fabric-test','adapter_version':'current-input-test','game':'minecraft-java','schema_version':'2026-05-24','player_name':'試験','capabilities':['dialogue_text.v1'],'voice_enabled':False,'execution_capabilities':['client.hotbar.select.v1']})['session_id']
   state={'sequence':0,'slot':8}
   def send(results=()):
    state['sequence']+=1
    event={'schema_version':'2026-05-24','adapter':'fixture','sequence':state['sequence'],'observed_at':horse.utc_now(),
      'event':{'name':'status_snapshot','source_kind':'system','priority_hint':'background','certainty':'high'},
      'player':{'dimension':'minecraft:overworld','hotbar':{'selected_slot':state['slot'],'slots':[{'slot':0,'item_id':'minecraft:stone_sword','count':1,'damage':0,'max_damage':131,'attack_damage':5,'weapon_kind':'sword'}]}},
      'world':{'time_phase':'day','weather':'clear','biome':'plains','local_light':15,'sky_visible':True,'danger_darkness_score':0},'command_results':list(results)}
    return h.request(url,'/api/v1/game-events',event,sid=sid)
   assert not send().get('commands')
   row=route(url,sid,'とりあえず剣に持ち帰ってもらっていいかな',seq)
   assert row['request_classification']['prediction']['label']=='equip_sword',row
   response=h.wait_for(lambda:(x if (x:=send()).get('commands') else None))
   assert len(response['commands'])==1,response
   command=response['commands'][0];assert command['type']=='select_hotbar',command
   state['slot']=0
   send([{'command_id':command['command_id'],'command_type':'select_hotbar','status':'succeeded','executed_at':horse.utc_now(),'selected_slot':0,'selected_item_id':'minecraft:stone_sword','detail_code':'selected'}])
   ack(url,sid,'剣やな、持ち替えるで！')
   seq['completed_command_type']=command['type']
   seq['history_after_completion']=next(s['history'] for s in h.snapshot(url)['sessions'] if s['session_id']==sid)
   assert any(r.get('text')=='剣やな、持ち替えるで！' for r in seq['history_after_completion'])
   for text in ['お、センキュー バッチリじゃん','今度は','ありがとう！']:
    assert not send().get('commands')
    row=route(url,sid,text,seq)
    assert row['request_classification']['prediction']['label'] in ('not_request','other_request'),row
    regular_reply(url,sid,row)
    horse.quiet_commands(send,seconds=.15)
   row=route(url,sid,'敵はどこ？',seq)
   assert row['request_classification']['prediction']['label']=='enemy_information',row
   assert not send().get('commands')
   seq['new_commands_after_thanks']=0
   seq['history_at_end']=next(s['history'] for s in h.snapshot(url)['sessions'] if s['session_id']==sid)
   (out/'current-input-runtime.log').write_text(log.read_text())
 report['sequences'].append(seq)
finally:h.running=original
report['all_followups_non_action']=all(s['new_commands_after_thanks']==0 for s in report['sequences'])
report['all_owned_processes_stopped']=True
(out/'current-input-runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'sequences':len(report['sequences']),'inputs':sum(len(s['inputs']) for s in report['sequences']),'new_commands_after_thanks':[s['new_commands_after_thanks'] for s in report['sequences']],'all_followups_non_action':report['all_followups_non_action'],'all_owned_processes_stopped':True},ensure_ascii=False))
