"""Export six canonical prepare prompts without a model, SDK, or helper process."""
from __future__ import annotations
import ast
import copy
import inspect
import itertools
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(Path(__file__).resolve().parent)]
from dogido_server.llm import haiku_prompts as hp, workshop_prompts as wp
from dogido_server.haiku.workshop_context import workshop_context_block
from haiku_grounding_prompt import build_haiku_line_grounding_messages
from haiku_helper import handle
from dogido_server.llm.prompts import build_messages
from dogido_server.llm.types import StructuredGenerationRequest
OUT=ROOT/'dogido-rust/src/haiku_prompt'
FUNCTIONS={
 'haiku_irony':hp.build_haiku_irony_messages,
 'haiku_scene':hp.build_haiku_scene_messages,
 'haiku_draft':hp.build_haiku_draft_messages,
 'haiku_line_grounding':build_haiku_line_grounding_messages,
 'haiku_line_regeneration':hp.build_haiku_line_regeneration_messages,
 'haiku_workshop_revision':wp.build_haiku_workshop_revision_messages,
}
ALIASES={
 '_dogido_haiku_spirit(has_structure=has_structure, has_poetic_interpretation=has_poetic_interpretation)':'spirit',
 '_form_card()':'form', '_grounding_scene_text(details)':'grounding_scene',
 'workshop_context_block(details)':'workshop_context',
}
class Slots(ast.NodeTransformer):
    def visit_FormattedValue(self,node):
        source=ast.unparse(node.value)
        return ast.Constant(value='@@'+ALIASES.get(source,source)+'@@')
    def visit_Call(self,node):
        if isinstance(node.func,ast.Name) and node.func.id=='_structured_json_tail' and not isinstance(node.args[0],ast.Constant):
            return ast.Constant(value='@@json_tail@@')
        return node

def template(fn):
    tree=ast.parse(inspect.getsource(fn))
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='user_prompt' for t in node.targets):
            node.value=Slots().visit(node.value)
    ast.fix_missing_locations(tree)
    namespace=dict(fn.__globals__)
    exec(compile(tree,'<prompt-template>','exec'),namespace)
    messages=namespace[fn.__name__]({})
    output=[]
    for message in messages:
        segments=[]
        for i,part in enumerate(re.split(r'@@(.*?)@@',message['content'])):
            if part:segments.append({'slot' if i%2 else 'literal':part})
        output.append({'role':message['role'],'segments':segments})
    return output
assets={kind:template(fn) for kind,fn in FUNCTIONS.items()}
assets['spirit']={f'{int(s)}{int(p)}':hp._dogido_haiku_spirit(has_structure=s,has_poetic_interpretation=p) for s,p in itertools.product([False,True],repeat=2)}
assets['form']=hp._form_card()
assets['strategy']={s:hp._generation_strategy_block({'generation_strategy':s}) for s in ['whole_poem','three_slot','one_plus_two','two_plus_one']}
assets['source_guide']=hp._source_atoms_block({'source_atoms':[{'atom_id':'x','text':'y'}]}).split('\n',1)[0]
assets['context_prefix']=workshop_context_block({'workshop_context':{'sentinel':True}}).split('{"sentinel": true}')[0]
assets['edit_failure_guidance']=wp._EDIT_FAILURE_GUIDANCE
assets['grounding_revision_note']=build_haiku_line_grounding_messages({'revision_edits':[]})[-1]['content'].split('【今回の修正差分】\n[]',1)[1].split('【判定する行】',1)[0]
# Extract static labels from canonical local dictionaries rather than rewording them.
regen_tree=ast.parse(inspect.getsource(hp._regeneration_line_prompt))
assets['regeneration_reasons']=next(ast.literal_eval(node.value) for node in ast.walk(regen_tree) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='reason_labels' for t in node.targets))
revision_frame={'op':'prepare','request':{'kind':'haiku_workshop_revision','details':{},'fallback_value':{},'temperature':.3,'route':'haiku','max_tokens':512}}
base=wp.build_haiku_workshop_revision_messages({})[-1]['content']
assets['revision_suffix']=handle(revision_frame)['messages'][-1]['content'][len(base):]
assets['retry']={}
for kind in FUNCTIONS:
    if kind=='haiku_line_grounding':continue
    d={'source_atoms':[{'atom_id':'__ATOM__'}],'failed_line_indices':[7139], '__dogido_structured_contract_retry':{'errors':['__ERRORS__'],'previous_payload':'__PREVIOUS__'}}
    msg=build_messages(StructuredGenerationRequest(kind=kind,details=d,fallback_value={}))[-1]
    content=msg['content'].replace('__ERRORS__','@@errors@@').replace('__PREVIOUS__','@@previous@@').replace('["__ATOM__"]','@@ids@@').replace('[7139]','@@indices@@')
    assets['retry'][kind]=[{'slot' if i%2 else 'literal':part} for i,part in enumerate(re.split(r'@@(.*?)@@',content)) if part]
(OUT/'templates.json').write_text(json.dumps(assets,ensure_ascii=False,indent=2)+'\n')
print({kind:[p['slot'] for m in assets[kind] for p in m['segments'] if 'slot' in p] for kind in FUNCTIONS})

atoms=[{'atom_id':f'source:{i}','text':text,'source_ref':'fixture','field_path':'world.biome','observation_role':'observed','kind':kind,'claim_class':claim,'claim_scopes':scopes,'basis_atom_ids':basis} for i,text,kind,claim,scopes,basis in [
 (1,'サクラの葉','catalog','factual',['identity_only'],[]),
 (2,'黒い剣を持っている','observation','factual',['observed_state'],[]),
 (3,'黒と桃色がならんでるわ','preface_clause','interpretive',['poetic_interpretation'],['source:1','source:2']),
 (4,'明るい花と黒い刃の取り合わせ','poetic_interpretation','interpretive',['poetic_interpretation'],['source:1','source:2'])]]
base={'biome':'サクラの林','biome_group':'山地','biome_traits':['花びら','斜面'],'held_item':'剣','poem_item_source':'pocket','inventory_items':['剣','サクラの葉','松明','剣','パン','丸石','土'],'nearby_blocks':['葉','草'],'passive_mobs':['ひつじ'],'weather':'clear','weather_label':'晴れ','time_phase':'morning','time_label':'朝','weather_context':'雨は降っていない','climate_hint':'涼しい','feature_candidates':['葉の色','剣の重さ'],'candidate_tensions':['黒と桃色'],'catalog_notes':['桜には薄い葉がある'],'poetic_lines':['ひつじが鳴く'],'haiku_tags':['朝','春'],'haiku_constraints':{'allowed_terms':['つるぎ'],'forbidden_terms':['つるはし'],'player_lessons':['造語は避けたい','自然な日本語','余韻がほしい','4件目は除外']},'scene':{'spoken_text':'黒い剣と花びらがならんでるわ','motifs':['黒','桃色'],'focus':['剣']},'irony':{'description':'黒と桃色がならんでるわ','focus':['剣','花']},'source_atoms':atoms,'grounding_atom_numbers':{f'source:{i}':i for i in range(1,5)},'grounding_lines':[{'line_index':i,'text':s} for i,s in enumerate(['さくらさく','くろきつるぎの','かげひとつ'])],'interpretation':'花と剣','player_dialogue_material':{'summary':'昨日は畑を広げた','motifs':['畑','昨日']},'workshop_context':{'current_verse':'さくらさく\nくろきつるぎの\nかげひとつ','pending_verse':None,'interpretation':'花と剣','recent_dialogue':[{'role':'player','text':'真ん中を直したい'}]},'current_lines':[{'line_index':0,'text':'さくらさく','frozen':True},{'line_index':1,'text':'くろきつるぎの','frozen':False,'sound_count':7,'target_sound_count':7,'allowed_sound_min':6,'allowed_sound_max':8,'meter_status':'within_range','failure_reasons':['meaning_not_retained','unnatural_japanese'],'assessment_comment':'修飾関係があいまい'},{'line_index':2,'text':'かげひとつ','frozen':True}], 'failed_line_indices':[1],'target_line_indices':[1],'workshop_findings':[{'line_index':1,'problem':'unnatural_japanese','note':'別の表現に'}]}
cases=[('empty',{}),('full',base)]
for strategy,structure,visible,poetic in itertools.product(['whole_poem','three_slot','one_plus_two','two_plus_one','unknown'],[False,True],[False,True],[False,True]):
 d=copy.deepcopy(base);d.update(generation_strategy=strategy,has_structure=structure,structure_label='村' if structure else '',sky_context_visible=visible,biome_context_visible=visible)
 if not poetic:d['source_atoms']=d['source_atoms'][:3]
 cases.append((f'matrix-{strategy}-{structure}-{visible}-{poetic}',d))
variants={
 'padded_strategy':{'generation_strategy':' whole_poem ','current_lines':[{'line_index':1,'text':'かな','sound_count':2,'target_sound_count':7,'allowed_sound_min':6,'allowed_sound_max':8,'meter_status':' too_short '}]},
 'literal_slots':{'held_item':'@@materials@@','scene':{'spoken_text':'@@workshop_context@@'},'workshop_context':{'text':'@@spirit@@'}},
 'whitespace':{'held_item':' \u001c ','inventory_items':['',' \u001c','葉','葉'],'structure_label':' \u001c ','interpretation':' \u001c 語 \u001c '},
 'missing_scene':{'scene':None,'irony':None,'source_atoms':None,'current_lines':None,'grounding_lines':None,'workshop_context':None},
 'empty_lists':{'grounding_lines':[],'source_atoms':[],'current_lines':[],'target_line_indices':[],'failed_line_indices':[],'haiku_constraints':{},'workshop_context':{}},
 'long_context':{'player_dialogue_material':{'summary':'あ😀'*100,'motifs':[' \u001cひとつ😀'*10,'ふたつ'*10,'みっつ'*10,'除外']},'scene':{'spoken_text':' あ😀'*1000},'current_lines':[{'line_index':1,'text':'くろきつるぎの','assessment_comment':' あ😀'*200,'failure_reasons':['unknown']}]},
 'raw_weather':{'weather_label':None,'time_label':None,'biome_group':None,'biome':None},
 'malformed_rows':{'source_atoms':[None,True,{}, {'atom_id':'x','text':'y','claim_scopes':['',None,'observed_state'],'basis_atom_ids':['',None,'source:1']}], 'grounding_lines':[None,{}, {'line_index':True,'text':None},{'line_index':2,'text':'く'}], 'current_lines':[None,{}, {'line_index':True,'text':'く','sound_count':True,'target_sound_count':7,'allowed_sound_min':6,'allowed_sound_max':8,'failure_reasons':[None,1,'empty_line']}], 'failed_line_indices':[True,False,1,1.5,'2']},
 'revision_grounding':{'revision_edits':[{'line_index':1,'expected_text':'くろきつるぎの','replacement_text':'くろきはものの'}]},
 'retry':{'edit_retry_feedback':{'global_failure_reasons':list(wp._EDIT_FAILURE_GUIDANCE)+['new_failure'],'line_failures':[{'line_index':1,'failure_reasons':['meaning_not_retained','unknown','',None],'assessment_comment':' 指摘😀'*100}]},'rejected_replacements':[{'line_index':1,'replacement_text':'くろきつるぎの'}]},
 'retry_empty':{'edit_retry_feedback':{},'rejected_replacements':[]},
 'schema_retry':{'__dogido_structured_contract_retry':{'errors':['lines:wrong','出典なし'],'previous_payload':'あ😀'*1500}},
 'schema_retry_empty':{'__dogido_structured_contract_retry':{}},
 'fallback_interpretation':{'scene':{},'irony':{'description':'見どころ😀'},'interpretation':'\u001c '},
 'structure_only':{'has_structure':False,'structure_label':'神殿','biome_context_visible':False,'sky_context_visible':False},
 'regeneration_labels':{'current_lines':[{'line_index':i,'text':'かな','sound_count':n,'target_sound_count':5,'allowed_sound_min':4,'allowed_sound_max':6,'meter_status':status,'failure_reasons':list(assets['regeneration_reasons'])} for i,n,status in [(0,2,'too_short'),(1,9,'too_long'),(2,5,'unknown')]]},
}
for name,changes in variants.items():
 d=copy.deepcopy(base);d.update(changes);cases.append((name,d))
rows=[]
for name,details in cases:
 for kind in FUNCTIONS:
  request={'kind':kind,'details':details,'fallback_value':{},'temperature':0.0 if kind=='haiku_line_grounding' else .3,'route':'chat' if kind in ['haiku_irony','haiku_scene','haiku_line_grounding'] else 'haiku','max_tokens':512 if kind=='haiku_line_grounding' else None}
  rows.append({'name':name,'request':request,**handle({'op':'prepare','request':request})})
(OUT/'fixtures.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print('fixtures',len(rows))
