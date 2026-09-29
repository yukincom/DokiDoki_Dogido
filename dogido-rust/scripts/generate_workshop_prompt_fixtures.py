"""Export canonical consultation prompts; no model, service, or dictionary calls."""
from __future__ import annotations
import ast
import copy
import hashlib
import inspect
import itertools
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(Path(__file__).resolve().parent)]
import workshop_helper as helper
from dogido_server.haiku.workshop_context import workshop_context_block
from dogido_server import tts_reading

OUT=ROOT/'dogido-rust/src/workshop_prompt'
OUT.mkdir(parents=True,exist_ok=True)

def forbidden(): raise AssertionError('fixture generation must not initialize UniDic')
tts_reading._get_unidic_tagger=forbidden

SLOTS={
 "details['original_player_text']":'original',
 "', '.join(details['allowed_problem_types'])":'problems',
 'extra':'extra',
 "details['conversation_stage']":'stage',
 "details['phase']":'phase',
 "details['player_text']":'player',
 "json.dumps(details['turn_steps'], ensure_ascii=False)":'steps',
 "json.dumps(details['tool_observation'], ensure_ascii=False)":'observation',
 'workshop_context_block(details)':'context',
 "', '.join(details['allowed_actions'])":'actions',
 "', '.join(details['allowed_purposes'])":'purposes',
 "json.dumps(retry, ensure_ascii=False)":'retry',
}
def segments(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str): return [{'literal':node.value}]
    if isinstance(node,ast.JoinedStr): return [p for n in node.values for p in segments(n)]
    if isinstance(node,ast.FormattedValue): return [{'slot':SLOTS[ast.unparse(node.value)]}]
    if isinstance(node,ast.Call): return [{'slot':SLOTS[ast.unparse(node)]}]
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):return segments(node.left)+segments(node.right)
    raise ValueError(f'unexpected template expression: {ast.dump(node)}')

fn=ast.parse(inspect.getsource(helper.consultation_messages)).body[0]
conditions=[n for n in fn.body if isinstance(n,ast.If)]
keys=['raw_semantic','current_idea','meaning_ack','confirm_close','resume','followup','editing','conversation_candidate','propose_revision','pending','after_validation']
assert len(conditions)==len(keys), 'consultation conditions changed; audit the native order'
assets={'extras':{key:segments(node.body[0].value) for key,node in zip(keys,conditions)}}
assets['extra_order']=keys
assets['main']=segments(next(n.value for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='prompt' for t in n.targets)))
base={'allowed_actions':['ask'], 'allowed_problem_types':['unreadable'], 'allowed_purposes':['continue_discussion'],
      'original_player_text':'どう？','player_text':'どう？','conversation_stage':'discussion','phase':'decide',
      'turn_steps':[],'tool_observation':None,'workshop_context':{},'pending_verse':None}
assets['system']=helper.consultation_messages(base)[0]['content']
probe={'sentinel':True}
assets['context_prefix'],assets['context_suffix']=workshop_context_block({'workshop_context':probe}).split(json.dumps(probe,ensure_ascii=False))
handle_tree=ast.parse(inspect.getsource(helper.handle))
retry=next(n for n in ast.walk(handle_tree) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='retry')
content=next(value for key,value in zip(retry.body[0].value.args[0].keys,retry.body[0].value.args[0].values) if isinstance(key,ast.Constant) and key.value=='content')
assets['retry']=segments(content)
assets['canonical_sha256']=hashlib.sha256((inspect.getsource(helper.consultation_messages)+inspect.getsource(workshop_context_block)).encode()).hexdigest()
(OUT/'templates.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2)+'\n')

ACTIONS=['respond','explain','ask','inspect','propose_revision','stage_player_edit','show_current','compare','accept_pending','reject_pending','close_workshop','unrelated','stage_conversation_candidate']
FOLLOWUPS={
 'discussion':[], 'meaning_explained':['acknowledge_meaning'],
 'close_confirmation':['confirm_close','continue_workshop'],
 'combat_resume_confirmation':['resume_workshop','decline_resume'],
}

def source_frame():
    lines=[{'line_id':f'line_{i+1}','line_index':i,'position':['upper','middle','lower'][i],
        'canonical_name':['上五','中七','下五'][i],'surface_text':text,'reading_text':text,
        'source_atom_ids':[],'source_atoms':[],'provenance':'generated'}
        for i,text in enumerate(['さくらのは','くろいおのへと','あさのいろ'])]
    return {'op':'prepare','text':'この句の意味を教えて','phase':'decide','observation':None,'turn_steps':[],
        'allowed_actions':ACTIONS.copy(),'workshop':{'materials':{},'dialogue':[], 'agent_steps':[],
        'current_lines':copy.deepcopy(lines),'emission':{'reading_text':'\n'.join(l['reading_text'] for l in lines),
        'lines':lines,'created_at':'2026-09-26T10:00:00+00:00','interpretation':'葉と斧の対比'}}}

CANDIDATE={'proposal':{'line_index':0,'target_fragment':'さくらのは','replacement_text':'さくらいろ'},
           'evidence':'「さくらのは」を「さくらいろ」にするのはどう？','validation_codes':['meter_failed'], 'quality':0.85}
RETRY={'errors':['speech:type','checks:duplicate'],'payload':{'action':'inspect','purpose':'understand_meaning','confidence':0.95,'evidence':'意味','speech':None,'checks':['source','source']}}
rows=[]
for phase,pending,stage,semantic,idea,candidate,retry_kind in itertools.product(
        ['decide','after_inspection','after_validation'],[False,True],list(FOLLOWUPS),[False,True],[False,True],[False,True],range(3)):
    f=source_frame();f['phase']=phase;f['workshop']['followup']=stage;f['allowed_actions']+=FOLLOWUPS[stage]
    if semantic: f['text']='サクラノバの意味は？';f['interpreted_text']='さくらのはの意味は？'
    if pending:
        lines=copy.deepcopy(f['workshop']['current_lines']);lines[0]['surface_text']='桜色';lines[0]['reading_text']='さくらいろ'
        f['workshop']['pending']={'lines':lines,'base':f['workshop']['current_lines'],'generated_basis':{'retained':True}}
    if candidate:f['workshop']['conversation_candidate']=copy.deepcopy(CANDIDATE)
    if idea:f['workshop']['current_player_idea']=copy.deepcopy(CANDIDATE)
    if phase!='decide':
        f['turn_steps']=[{'phase':'decide','action':'inspect','outcome':'inspected','checks':['reading','meter','source'],'evidence':f['text'],'validation_codes':[]}]
        f['workshop']['agent_steps']=copy.deepcopy(f['turn_steps'])
        f['observation']={'kind':'inspection' if phase=='after_inspection' else 'revision_validation',
            'status':'rejected' if pending else 'proposed','validation_codes':['meter_passed','source_unavailable'],
            'lines':[{'line_index':0,'mora_count':5,'reading_text':'さくらのは','source':None}]}
    f['workshop']['dialogue']=[{'turn_id':'t0','player_text':'何を並べた？','dogido_text':'葉と斧やで。'},
        {'turn_id':'t1','player_text':'前は違う意味だと言ったよ','dogido_text':'取り違えたかもしれへんな。'}]
    if retry_kind:f['retry']={} if retry_kind==1 else copy.deepcopy(RETRY)
    details=helper.details_for(f)
    prepared={'details':details}
    fixed=helper.fixed_fragment_edit(f,details)
    if fixed is not None:prepared['fixed_payload']=fixed
    rows.append({'name':f'matrix-{phase}-{pending}-{stage}-{semantic}-{idea}-{candidate}-{retry_kind}',
                 'frame':f,'prepared':prepared,'retry':f.get('retry'),'expected':helper.handle(f)})

# Canonical fixed shortcut must continue to bypass the schema retry appendix.
for text,semantic in [('「さくらのは」を「さくらいろ」にして',None),('「さくらのは」を「さくらいろ」にして','「さくらのは」を「さくらいろ」にして。')]:
    for retry in [None,RETRY]:
        f=source_frame();f['text']=text
        if semantic:f['interpreted_text']=semantic
        if retry:f['retry']=retry
        details=helper.details_for(f);fixed=helper.fixed_fragment_edit(f,details)
        assert fixed is not None,'canonical fixed edit lost'
        rows.append({'name':'fixed-'+str(bool(semantic))+'-'+str(bool(retry)),'frame':f,
            'prepared':{'details':details,'fixed_payload':fixed},'retry':retry,'expected':helper.handle(f)})

# Direct, valid prompt projections exercise independent conditional combinations,
# reference JSON formatting, empty and ordered lists, and literal marker text.
pure=[]
variants=[('empty',{}),('no_context',{'workshop_context':{}}),('pending_blank',{'pending_verse':' '}),
 ('only_continue',{'allowed_actions':['continue_workshop']}),('only_decline',{'allowed_actions':['decline_resume']}),
 ('all_followups',{'allowed_actions':list(FOLLOWUPS['meaning_explained']+FOLLOWUPS['close_confirmation']+FOLLOWUPS['combat_resume_confirmation'])}),
 ('empty_allowed',{'allowed_actions':[],'allowed_problem_types':[],'allowed_purposes':[]}),
 ('ordered_duplicates',{'allowed_actions':['ask','stage_player_edit','ask','propose_revision'],'allowed_problem_types':['reading','unnatural_japanese','reading'],'allowed_purposes':['other','understand_meaning','other']}),
 ('literal_slots',{'original_player_text':'@@extra@@ {retry}','player_text':'@@context@@','workshop_context':{'current_player_idea':{'text':'@@original@@'},'tail':'\\n"日本語"\u001c😀'},'turn_steps':[{'z':True,'a':False,'n':None,'ratio':0.85,'zero':-0.0}],'tool_observation':{'items':[1,2,3], 'surface':'猫\n草'}}),
 ('long_unicode',{'player_text':' あ😀'*1000,'original_player_text':'原文😀'*900,'workshop_context':{'interpretation':'見どころ😀'*500}})]
for name,change in variants:
    d=copy.deepcopy(base);d.update(change)
    for retry in [None,{},[],False,0,'',True,{'payload':RETRY,'errors':[]},['引用','😀']]:
        old_details,old_fixed=helper.details_for,helper.fixed_fragment_edit
        try:
            helper.details_for=lambda _frame: d
            helper.fixed_fragment_edit=lambda _frame,_details: None
            expected=helper.handle({'op':'prepare','retry':retry})
        finally:
            helper.details_for,helper.fixed_fragment_edit=old_details,old_fixed
        pure.append({'name':name+'-'+str(len(pure)),'prepared':{'details':d},'retry':retry,'expected':expected})
# Lossless interning keeps full canonical strings without repeating long prompts
# for the three retry variants. Test loaders restore each content verbatim.
texts=[]
indices={}
for row in rows+pure:
    for message in row['expected']['messages']:
        content=message.pop('content')
        if content not in indices:
            indices[content]=len(texts)
            texts.append(content)
        message['content_ref']=indices[content]
(OUT/'fixtures.json').write_text(json.dumps({'message_contents':texts,'projection_cases':rows,'pure_cases':pure},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'projection_cases':len(rows),'pure_cases':len(pure),'conditional_blocks':len(keys)},ensure_ascii=False))
