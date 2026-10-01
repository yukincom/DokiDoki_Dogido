"""No model calls: export canonical lighting prompt, schema and retry cases."""
import itertools
import json
import logging
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.dialogue.light_source_comment_planner import LightSourceCommentContext, _parse_plan
from dogido_server.llm.client import DogidoLLM
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.structured_contracts import validate_structured_payload
from dogido_server.llm.types import StructuredGenerationRequest, GeneratedText

logging.disable(logging.CRITICAL)
KIND = 'light_source_comment_plan'
FALLBACK = {'action':'stay_silent','basis_ids':['light_source_gain_observed'],'confidence':0.0}
OUT = ROOT/'dogido-rust/src/light_plan'

def request(details):
    return StructuredGenerationRequest(kind=KIND,details=details,fallback_value=FALLBACK,temperature=0.0,route='chat',max_tokens=160)

def parts(text, replacements):
    pieces = [{'literal':text}]
    for marker, slot in replacements:
        result=[]
        for piece in pieces:
            if 'literal' not in piece:
                result.append(piece); continue
            split=piece['literal'].split(marker)
            for i, s in enumerate(split):
                if i:result.append({'slot':slot})
                if s:result.append({'literal':s})
        pieces=result
    return pieces

sentinel={'allowed_actions':['__ACTIONS__'],'facts':[{'basis_id':'__BASIS__','value':'__VALUE__'}]}
asset=[]
for message in build_messages(request(sentinel)):
    asset.append({'role':message['role'],'segments':parts(message['content'],[
        (json.dumps(sentinel['allowed_actions']), 'actions'),
        (json.dumps(sentinel['facts']), 'facts')])})
retry=dict(sentinel,__dogido_structured_contract_retry={'errors':['__ERRORS__'],'previous_payload':'__PREVIOUS__'})
asset.append({'role':'user','segments':parts(build_messages(request(retry))[-1]['content'],[
    ('__ERRORS__','errors'),('__PREVIOUS__','previous'),
    (json.dumps(['__ACTIONS__']),'sorted_actions'),(json.dumps(['__BASIS__']),'sorted_basis')])})
(OUT/'prompts.json').write_text(json.dumps(asset,ensure_ascii=False,indent=2)+'\n')

contexts=[]
for before, after in [(0,1),(2,5),(7,8),(30,32),(32,64),(4,4)]:
    for booleans in itertools.product([False,True],repeat=6):
        c=LightSourceCommentContext(before,after,*booleans)
        contexts.append({'allowed_actions':list(c.allowed_actions()),'facts':c.basis_rows()})
# Keep full context matrix for prompt and domain checks.
prompts=[{'details':d,'messages':build_messages(request(d))} for d in contexts]
ack=next(d for d in contexts if 'acknowledge_supply_gain' in d['allowed_actions'])
relief=next(d for d in contexts if 'relief_after_darkness' in d['allowed_actions'])
good={'action':'acknowledge_supply_gain','basis_ids':['first_light_supply'],'confidence':0.91}
values=[None,True,False,0,1,0.0,1.0,-1,1.01,'','0.9','stay_silent',[],{},[''],[1],['a']]
payloads=[{},good,FALLBACK,dict(good,confidence=.77),dict(good,confidence=.78),dict(good,**{f'extra{i}':i for i in range(12)}),dict(good,basis_ids=['光😀'],extra='あ😀'*2500)]
for field in good:
    for value in values:
        payloads.append(dict(good,**{field:value}))
for ids in [[],['a','b','c','d'],[1,2,3,4],['','','',''],['first_light_supply']*2,[' supply_before '],['z','b','a'],['dark_push_recovered'],['supply_after'],[1,'',False]]:
    payloads.append(dict(good,basis_ids=ids))
for fields in [{'extra':1},{'z':1,'a':2}, {'confidence':None,'basis_ids':[1]*12,'action':'x','z':1}]:
    payloads.append(dict(good,**fields))
for action in ['stay_silent','relief_after_darkness','acknowledge_supply_gain']:
    for basis in [['dark_push_recovered'],['first_light_supply'],['supply_before'],['surroundings_light'],['supply_after']]:
        payloads.append(dict(good,action=action,basis_ids=basis))
contracts=[]
for d in [ack,relief,contexts[1],{'allowed_actions':[],'facts':[]},{},{'allowed_actions':['stay_silent','光😀'], 'facts':[{'basis_id':'光😀','value':'true'}]}]:
    for payload in payloads:
        contract=validate_structured_payload(KIND,payload,details=d)
        retry_d=dict(d,__dogido_structured_contract_retry={'errors':list(contract.errors),'previous_payload':json.dumps(payload,ensure_ascii=False)})
        contracts.append({'details':d,'payload':payload,'errors':list(contract.errors),'retry_messages':build_messages(request(retry_d))})

class Fake(DogidoLLM):
    def __init__(self, replies):
        self.replies=iter(replies);self.requests=[];self._lock=threading.Lock()
    def enabled(self):return True
    def _settings_for_request(self,request):return SimpleNamespace(llm_max_tokens=160)
    def _generate_backend_text(self,req):
        self.requests.append({'messages':build_messages(req),'max_tokens':req.max_tokens,'temperature':req.temperature,'route':req.route})
        reply=next(self.replies)
        if 'error' in reply:raise RuntimeError(reply['error'])
        return GeneratedText(reply['text'],finish_reason=reply.get('finish_reason'),completion_tokens=10,prompt_tokens=20)

def text(payload,finish_reason='stop'):
    return {'text':json.dumps(payload,ensure_ascii=False),'finish_reason':finish_reason}
invalid=dict(good,basis_ids=['invented'])
cases=[
    ('accepted',[text(good)]),('low_confidence',[text(dict(good,confidence=.7))]),
    ('silent',[text(FALLBACK)]),('length_complete',[text(good,'length')]),
    ('generation_error',[{'error':'test failure'}]),('invalid_json',[{'text':'not JSON'}]),
    ('length_truncated',[{'text':'{"action":','finish_reason':'length'}]),
    ('max_tokens_truncated',[{'text':'{"action":','finish_reason':'MAX_TOKENS'}]),
    ('contract_retry',[text(invalid),text(good)]),
    ('retry_invalid',[text(invalid),{'text':'invalid'}]),
    ('retry_truncated',[text(invalid),{'text':'{"action":','finish_reason':'length'}]),
    ('retry_error',[text(invalid),{'error':'test failure'}]),
    ('retry_contract_error',[text(invalid),text(invalid)]),
    ('fenced',[{'text':'```json\n'+json.dumps(good)+'\n```'}]),
    ('nested',[{'text':'['+json.dumps(good)+']'}]),
    ('prefixed',[{'text':'Here is JSON: '+json.dumps(good)}]),
    ('complete_retry_length',[text(invalid),text(good,'length')]),
]
runtime=[]
for name,replies in cases:
    llm=Fake(replies)
    result=llm.generate_structured_json(request(ack))
    plan=_parse_plan(result,allowed_actions=tuple(ack['allowed_actions']),facts=ack['facts'])
    runtime.append({'name':name,'details':ack,'fallback':FALLBACK,'replies':replies,'expected_payload':result,'requests':llm.requests,'should_speak':bool(plan and plan.should_speak)})
(OUT/'fixtures.json').write_text(json.dumps({'prompts':prompts,'contracts':contracts,'runtime':runtime},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'prompts':len(prompts),'contracts':len(contracts),'runtime':len(runtime)}))
print(sorted(set(e for row in contracts for e in row['errors'])))
