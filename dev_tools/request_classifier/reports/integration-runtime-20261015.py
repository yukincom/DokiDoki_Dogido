"""実ModernBERT＋Rust受付、模擬ゲーム/LLMを使う有界な接続確認。"""
import argparse, hashlib, json, sys, tempfile
from pathlib import Path
from contextlib import contextmanager
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model-root',type=Path,required=True)
parser.add_argument('--output-dir',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[3]
base=args.output_dir.resolve(); base.mkdir(parents=True,exist_ok=True)
model=args.model_root.resolve()
sys.path.insert(0,str(root/'dogido-rust/scripts'))
import check_dialogue as h
import check_horse_recall as horse
settings={'model_root':str(model),'python':str(root/'.dogido_tools/request_classifier/.venv/bin/python')}
binary=root/'dogido-rust/target/release/dogido-rust'
report={'scope':'real CPU v1.9 seed43 + real Rust HTTP runtime; mocked Minecraft, LLM, TTS',
 'manifest_sha256':hashlib.sha256((model/'adopted-model.json').read_bytes()).hexdigest(),
 'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(), 'cases':[]}
with tempfile.TemporaryDirectory(prefix='dogido-classifier-live-') as temp,h.dependencies() as (dep,control,seen):
 with h.running(binary,Path(temp),dep,haiku_settings={'enabled':False,'memory_enabled':False},extra_args=('--request-classifier-settings',json.dumps(settings),'--no-llm','--no-audio')) as (url,_,log):
  for text,label in [('こんにちは','not_request'),('敵はどこ？','enemy_information'),('剣に持ち替えて','equip_sword'),(horse.RECALL,'recall_mount'),('松明を置いて','other_request')]:
   sid=h.register(url,preview=True)
   # 剣も既存の現在hotbar検査へ到達させる。previewセッションには実操作能力を渡さない。
   event={'schema_version':'2026-05-24','adapter':'fixture','sequence':1,'observed_at':horse.utc_now(),
    'event':{'name':'status_snapshot','source_kind':'system','priority_hint':'background','certainty':'high'},
    'player':{'dimension':'minecraft:overworld'},'world':{'time_phase':'day','local_light':15}}
   h.request(url,'/api/v1/game-events',event,sid=sid)
   admission=h.request(url,'/api/v1/player-input',{'session_id':sid,'source':'text','text':text})
   assert admission['reason']=='request_classification',admission
   def done():
    row=next((r for r in h.snapshot(url)['utterances'] if r['turn_id']==admission['turn_id']),{})
    return row if 'forwarded_input' in row else None
   row=h.wait_for(done,timeout=15)
   classification=row['request_classification']
   assert classification['prediction']['label']==label,row
   report['cases'].append({'text':text,'expected':label,'classification':classification,'forwarded_input':row['forwarded_input']})
   h.request(url,'/api/v1/adapter-sessions/'+sid,method='DELETE')
  assert not [r for r in seen if r['path'].startswith('/v1/')],seen
  report['five_label_llm_calls']=0
  (base/'runtime-live.log').write_text(log.read_text())
original=h.running
@contextmanager
def classified_running(*args,**kwargs):
 kwargs['extra_args']=(*kwargs.get('extra_args',()),'--request-classifier-settings',json.dumps(settings),'--no-audio')
 with original(*args,**kwargs) as value: yield value
h.running=classified_running
plan=horse.MockPlan([('selection',horse.select_response())])
try:
 with horse.fixture(h,binary,plan) as (url,sid,send,rows,history,stories,state,seen):
  admission=h.request(url,'/api/v1/player-input',{'session_id':sid,'source':'text','text':horse.RECALL})
  assert admission['reason']=='request_classification',admission
  command=horse.get_command(h,send)
  plan.verify()
  assert len(plan.calls)==1
  send(results=[horse.result(command)])
  h.wait_for(lambda:stories(horse.SUCCESS_STORY))
  classified=h.wait_for(lambda:next((r for r in rows() if r['turn_id']==admission['turn_id'] and 'forwarded_input' in r),None))
  report['horse_recall']={'classification':classified['request_classification'],'llm_calls':len(plan.calls),
   'tool_names':[[t['function']['name'] for t in call.get('tools',[])] for call in plan.calls],
   'command_type':command['type'],'mock_ack_verified':True,'minecraft_verified':False,
   'messages_characters':len(json.dumps(plan.calls[0]['messages'],ensure_ascii=False))}
finally: h.running=original
report['all_owned_processes_stopped']=True
(base/'runtime-live-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'cases':len(report['cases']),'horse_llm_calls':report['horse_recall']['llm_calls'],'all_owned_processes_stopped':True},ensure_ascii=False))
