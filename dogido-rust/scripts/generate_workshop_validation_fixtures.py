"""Canonical structured and semantic workshop validation, without SDK/model calls."""
from __future__ import annotations
import ast, copy, dataclasses, inspect, itertools, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(Path(__file__).parent)]
import workshop_helper as helper
from dogido_server.haiku import workshop as w, workshop_agent as a
from dogido_server.llm.structured_contracts import _WorkshopAgentStep
from dogido_server import tts_reading

def forbidden():raise AssertionError('validation must not initialize dictionary')
tts_reading._get_unidic_tagger=forbidden
OUT=ROOT/'dogido-rust/src/workshop_validation';OUT.mkdir(exist_ok=True)
assets={
 'schema':_WorkshopAgentStep.model_json_schema(), 'inactive':helper.INACTIVE_FIELDS,
 'followups':sorted(helper.FOLLOWUP_ACTIONS), 'actions':sorted(a.WORKSHOP_AGENT_ACTIONS),
 'purposes':sorted(a.WORKSHOP_AGENT_PURPOSES), 'problems':sorted(w.WORKSHOP_PROBLEM_TYPES),
 'intents':sorted(w.WORKSHOP_LLM_INTENTS), 'direct':sorted(a.WORKSHOP_AGENT_DIRECT_ACTIONS),
 'mutation_purposes':a._MUTATION_REQUIRED_PURPOSES,
 'unsaved':a._UNSAVED_ACTION_CLAIMS, 'unfinished':a._UNFINISHED_SUCCESS_CLAIMS,
 'inspection_claims':a._INSPECTION_CLAIM_MARKERS,'inspection_requests':a._INSPECTION_REQUEST_MARKERS,
 'positive':{k:v.pattern for k,v in a._STATE_CHANGE_POSITIVE_MARKERS.items()},
 'contradiction_actions':list(a._STATE_CHANGE_CONTRADICTIONS),
 'explicit_lines':[[i,p.pattern] for i,p in w._EXPLICIT_LINE_PATTERNS],
 'concepts':[dataclasses.asdict(c) for c in w.WORKSHOP_LINE_CONCEPTS],
 'pending_accept':w._PENDING_REVISION_ACCEPT_PATTERN.pattern,
 'pending_reject':w._PENDING_REVISION_REJECT_PATTERN.pattern,
}
# Preserve helper-specific regex strings, including their original order.
for name,func in [('edit',helper.explicit_player_edit),('repair',helper.explicit_repair)]:
 tree=ast.parse(inspect.getsource(func))
 assets[name+'_patterns']=[ast.literal_eval(n.args[0]) for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('search','sub')]
# ast.walk is breadth-first; label the two search patterns explicitly by contents.
for name in ['edit','repair']:
 p=assets.pop(name+'_patterns')
 assets[name+'_quote']=next(s for s in p if s.startswith('「'))
 searches=[s for s in p if s!=assets[name+'_quote']]
 assets[name+'_negative']=searches[0];assets[name+'_positive']=searches[1]
# The followup branch uses the same four conditions as the canonical helper.
handle=ast.parse(inspect.getsource(helper.handle))
branch=next(n for n in ast.walk(handle) if isinstance(n,ast.If) and ast.unparse(n.test)=="frame['op'] == 'validate'")
followup=branch.body[1] if isinstance(branch.body[0],ast.Assign) else branch.body[0]
patterns=[ast.literal_eval(n.args[0]) for n in ast.walk(followup) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='search']
assert len(patterns)==3
assets['followup_negative'],assets['resume_negative'],assets['ack_negative']=patterns
(OUT/'assets.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2)+'\n')

old=json.loads((ROOT/'dogido-rust/src/workshop_prompt/fixtures.json').read_text())
base=old['projection_cases'][0]['frame'];base['op']='validate'
pending_view=next(c['frame']['workshop']['pending'] for c in old['projection_cases'] if c['frame']['workshop'].get('pending'))
contexts=[]; context_map={};cases=[]
def case(name,payload,frame=None,details_change=None):
 f=copy.deepcopy(frame or base);f.update(op='validate',payload=copy.deepcopy(payload))
 details=helper.details_for(f)
 if details_change:details.update(copy.deepcopy(details_change))
 context={'frame':{k:v for k,v in f.items() if k!='payload'},'details':details}
 key=json.dumps(context,ensure_ascii=False)
 if key not in context_map:context_map[key]=len(contexts);contexts.append(context)
 original=helper.details_for
 try:
  helper.details_for=lambda _:copy.deepcopy(details)
  expected=helper.handle(copy.deepcopy(f));error=False
 except (TypeError,ValueError,KeyError):expected=None;error=True
 finally:helper.details_for=original
 cases.append({'name':name,'context':context_map[key],'payload':payload,'expected':expected,'error':error})
def payload(action='ask',text='どうかな',**kw):
 p={'action':action,'purpose':assets['mutation_purposes'].get(action,'continue_discussion'), 'confidence':.9,'evidence':text,'speech':'気になるところを教えてな。' if action in assets['direct'] else '', 'checks':['source'] if action=='inspect' else []}
 p.update(kw);return p

# All primitive, nested and list schema defects; multiple-error truncation/order.
p=payload(text=base['text']);p.update(copy.deepcopy(helper.INACTIVE_FIELDS))
values=[None,False,True,0,1,-1,1.25,'', 'unknown',[],{},['reading'],[1],list(range(4))]
for field in p:
 for v in values:case('field-'+field+'-'+str(len(cases)),{**p,field:v})
 q=copy.deepcopy(p);del q[field];case('missing-'+field,q)
for field in ['line_reference','line_proposal']:
 for key in p[field]:
  for v in values: q=copy.deepcopy(p);q[field][key]=v;case('nested-'+field+'-'+key+'-'+str(len(cases)),q)
  q=copy.deepcopy(p);del q[field][key];case('missing-'+field+'-'+key,q)
 q=copy.deepcopy(p);q[field]['extra']=True;case('extra-'+field,q)
finding={'line_index':0,'fragment':'さくら','problem':'reading','note':'読みが気になる','confidence':.9}
for key in finding:
 for v in values:q=copy.deepcopy(p);q['findings']=[{**finding,key:v}];case('finding-'+key+'-'+str(len(cases)),q)
 q=copy.deepcopy(finding);del q[key];case('finding-missing-'+key,{**p,'findings':[q]})
for v in [None,False,1,1.5,'unknown',[],{},[{}, {}, {}, {}]]:case('root-'+str(len(cases)),v)
case('many-errors',{'unknown':1,'action':None,'purpose':None,'confidence':'x','checks':[1,2],'speech':0})
case('ordered-extras',{**p,'z_extra':True,'a_extra':False,'__dogido_status':'fallback'})

# Actions, confidence thresholds, purpose, exact evidence and phases.
texts=['この句はどうかな','採用して','却下して','終了して','「さくらのは」を「さくらいろ」にして','上五を直して','その案でお願い','その案を採用して終了して','うん、教えて']
for action,text,score in itertools.product(assets['actions']+assets['followups'],texts,[.64,.65,.719,.72,.749,.75,.849,.85,1]):
 f=copy.deepcopy(base);f['text']=text;f['allowed_actions']+=assets['followups']
 if action in ('accept_pending','reject_pending','compare'):f['workshop']['pending']=copy.deepcopy(pending_view)
 f['workshop']['conversation_candidate']={'proposal':{'line_index':0,'target_fragment':'さくらのは','replacement_text':'さくらいろ'}}
 q=payload(action,text,confidence=score)
 if action=='stage_player_edit':q['line_proposal']={'found':True,'target_fragment':'さくらのは','replacement_text':'さくらいろ','evidence':text,'confidence':score}
 if action=='propose_revision':q['findings']=[finding]
 case('action-threshold-'+str(len(cases)),q,f)
for action in assets['actions']:
 for purpose in assets['purposes']:case('purpose-'+action+'-'+purpose,payload(action,base['text'],purpose=purpose))

# Negation, quoted/report/hypothetical intent and local sentence boundaries.
acts=[('accept_pending','採用して'),('reject_pending','却下して'),('close_workshop','終了して'),('stage_player_edit','さくらいろにして'),('propose_revision','上五を直して'),('stage_conversation_candidate','その案でお願い')]
for action,act in acts:
 for text,evidence in [(act,act),(act+'？',act),('「'+act+'」',act),(act+'と言われた',act),('もし'+act+'たらどうなる',act),('そう言うのはやめて。'+act,act),(act+'。それはどういう意味？',act),(act+'じゃない',act),('前に'+act+'と言った',act),('採用しない。'+act,act)]:
  f=copy.deepcopy(base);f['text']=text
  if action in ('accept_pending','reject_pending'):f['workshop']['pending']=copy.deepcopy(pending_view)
  q=payload(action,evidence)
  if action=='propose_revision':q['findings']=[finding]
  if action=='stage_player_edit':q['line_proposal']={'found':True,'replacement_text':'さくらいろ','target_fragment':'さくらのは','evidence':evidence,'confidence':.95}
  case('intent-'+str(len(cases)),q,f)
for action,text in itertools.product(assets['followups'],['うん','いいよ','いいよ？','「いいよ」','いいよとは言ってない','そうとは思わない','わからない','続けない','終了しない','やめたい','続けよう','はい、そうしよう']):
 f=copy.deepcopy(base);f['text']=text;f['allowed_actions']+=assets['followups']
 for ev in [text,'いいよ','うん']:case('followup-'+str(len(cases)),payload(action,ev),f)

# ASR cannot invent raw mutation evidence/replacements; previous findings/pending.
for idx in [0,2,48,96,190,290,384,575]:
 f=copy.deepcopy(old['projection_cases'][idx]['frame'])
 for action in assets['actions']:
  q=payload(action,f.get('interpreted_text',f['text']))
  case('projection-'+str(len(cases)),q,f)
for raw,semantic in [('シュウリョウして','終了して'),('採用しない','採用して'),('上五を「サクライロ」にして','上五を「さくらいろ」にして')]:
 f=copy.deepcopy(base);f.update(text=raw,interpreted_text=semantic)
 for action in ['close_workshop','accept_pending','stage_player_edit']:
  for ev in [raw,semantic]:case('asr-'+str(len(cases)),payload(action,ev),f)

# Speech cleanup/usability and unperformed claims require actual inspection.
speeches=['教えてほしいな。','保存したで。','採用できたで。','直したで。','案ができたで。','読みはこうやで。','音数は五七五やで。','出典は葉っぱやで。','ええ意味やな。','<think>考え\n中</think>ドギド: ほっとしたわ。','Final answer: ええ感じやな。','English explanation','ああああやで。','OK、ええ感じやな。','「気になるな。」','あ'*121,'はい','本番：そうなんやな。','あ\x1c気になるな。']
for speech,action,obs in itertools.product(speeches,assets['direct'],[None,{'kind':'inspection','status':'completed','checks':['reading','meter','source']},{'kind':'inspection','status':'started','checks':['reading']},{'kind':'revision_validation','status':'proposed'},{'kind':'revision_validation','status':'rejected'}]):
 f=copy.deepcopy(base);f['text']='どうかな';f['observation']=obs
 case('speech-'+str(len(cases)),payload(action,f['text'],speech=speech),f)
for text,checks in itertools.product(['読みはどう？','音数はどう？','出典はどこ？','由来を教えて','意味はどう？'],[[],['reading'],['meter'],['source'],['reading','meter','source']]):
 f=copy.deepcopy(base);f['text']=text;f['observation']={'kind':'inspection','status':'completed','checks':checks}
 for action in assets['direct']:case('inspection-'+str(len(cases)),payload(action,text),f)

# Findings bind unique fragments, not model indices; references/proposals conflict.
for fragment,index,score in itertools.product(['さくら','サクラ','くろい','あさの','ない語','', 'さ'],[None,0,1,2,3,True],[.64,.65,.75,1]):
 f=copy.deepcopy(base);f['text']='上五を直して'
 case('finding-semantic-'+str(len(cases)),payload('propose_revision',f['text'],findings=[{**finding,'fragment':fragment,'line_index':index,'confidence':score}]),f)
for text,concept,target,score in itertools.product(['上五をさくらいろにして','真ん中をさくらいろにして','後ろのパートをさくらいろにして','上五と下五をさくらいろにして','さくらいろにして'],['line_1','line_2','line_3','unknown'],['さくら','くろい','ない語',''],[.74,.75,.95]):
 f=copy.deepcopy(base);f['text']=text
 ref={'found':True,'concept_id':concept,'evidence':text,'confidence':score}
 proposal={'found':True,'target_fragment':target,'replacement_text':'さくらいろ','evidence':text,'confidence':score}
 case('line-target-'+str(len(cases)),payload('stage_player_edit',text,line_reference=ref,line_proposal=proposal),f)
for action,close,evidence in itertools.product(['accept_pending','reject_pending','close_workshop','ask'],[False,True],['','終了して','採用して','終了しない']):
 f=copy.deepcopy(base);f['text']='採用して、終了して'
 if action in ('accept_pending','reject_pending'):f['workshop']['pending']=copy.deepcopy(pending_view)
 case('close-after-'+str(len(cases)),payload(action,'採用して',close_after_action=close,close_evidence=evidence),f)

# Reach the remaining semantic branches directly through valid code projections.
for action,act in [('accept_pending','それでいい'),('reject_pending','その案なし'),('close_workshop','ここまででええ'),('stage_conversation_candidate','その案でお願い')]:
 for candidate,pending in itertools.product([False,True],[False,True]):
  f=copy.deepcopy(base);f['text']=act
  if pending:f['workshop']['pending']=copy.deepcopy(pending_view)
  if candidate:f['workshop']['conversation_candidate']={'proposal':{'line_index':0}}
  case('state-presence-'+str(len(cases)),payload(action,act),f)
for previous in [None,[],[{'line_index':0,'problem':'reading'}]]:
 f=copy.deepcopy(base);f['text']='上五を直して'
 d=helper.details_for(f);d['workshop_context']['last_findings']=previous
 case('previous-findings-'+str(len(cases)),payload('propose_revision',f['text']),f,d)
f=copy.deepcopy(base);f['text']='どうかな'
d=helper.details_for(f);d['allowed_actions'].append('defer_to_legacy')
case('deferred',payload('defer_to_legacy',''),f,d)
for verse in ['', 'さくらのは', 'さくらのは くろいおのへと あさのいろ', 'さくらのは\x85くろいおのへと\x1eあさのいろ', 'さくらのは\nさくらのは\nあさのいろ']:
 for fragment in ['さくら', 'あさ']:
  f=copy.deepcopy(base);f['text']='句を直して'
  case('verse-split-'+str(len(cases)),payload('propose_revision',f['text'],findings=[{**finding,'fragment':fragment}]),f,{'working_reading':verse})
for n in [0,1,2,119,120,121,200]:
 f=copy.deepcopy(base);f['text']='あ'*n
 case('evidence-length-'+str(n),payload('ask',f['text']),f)
for checks in [[],['reading','reading'],['meter','source'],['unknown']]:
 for action in ['ask','inspect']:case('dynamic-checks-'+str(len(cases)),payload(action,base['text'],checks=checks))
for action in assets['followups']:
 case('followup-disallowed-'+action,payload(action,base['text']))
for action,text_ in [('compare','どうかな'),('stage_conversation_candidate','その案でお願い')]:
 f=copy.deepcopy(base);f['text']=text_;d=helper.details_for(f);d['allowed_actions'].append(action)
 case('missing-state-'+action,payload(action,text_),f,d)
for close_text in ['終了しない','「終了して」と言われた','まだ話したい','うれしいね','終了して？']:
 f=copy.deepcopy(base);f['text']='採用して。'+close_text;f['workshop']['pending']=copy.deepcopy(pending_view)
 case('close-safe-'+str(len(cases)),payload('accept_pending','採用して',close_after_action=True,close_evidence=close_text),f)
for text_ in ['あ！','！！','a？','・、','\x1cあ\x1f','ＡＢ','サクライロ','上五をサクライロにして']:
 f=copy.deepcopy(base);f['text']=text_
 case('compact-evidence-'+str(len(cases)),payload('ask',text_),f)
 if 'にして' in text_:
  p=payload('stage_player_edit',text_,line_proposal={'found':True,'target_fragment':'さくらのは','replacement_text':'さくらいろ','evidence':text_,'confidence':.95})
  case('raw-kana-no-authority',p,f)
for replacement in ['さ'*47,'さ'*48,'さ'*49,'さ'*130,'ｻｸﾗｲﾛ','サクライロ','さくらいろ']:
 f=copy.deepcopy(base);f['text']='上五を「'+replacement+'」にして'
 p=payload('stage_player_edit',f['text'],line_reference={'found':True,'concept_id':'line_1','evidence':'上五','confidence':.95},line_proposal={'found':True,'target_fragment':'さくらのは','replacement_text':replacement,'evidence':f['text'],'confidence':.95})
 case('replacement-slice-'+str(len(cases)),p,f)
(OUT/'fixtures.json').write_text(json.dumps({'contexts':contexts,'cases':cases},ensure_ascii=False,separators=(',',':'))+'\n')
print(json.dumps({'cases':len(cases),'contexts':len(contexts),'errors':sum(c['error'] for c in cases)}))
