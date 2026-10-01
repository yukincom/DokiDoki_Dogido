"""Pure canonical structure/tactics fixtures; no services or model calls."""
import ast, copy, hashlib, json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dogido_server import entry_catalog as c
from dogido_server.models import VisualThreat
from dogido_server.state_machine.constants import HOSTILE_LABELS
from dogido_server.state_machine.mixins.narration import NarrationMixin
from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'dogido-rust/src/chat_hints';OUT.mkdir(exist_ok=True)
assert json.loads((ROOT/'dogido-rust/src/threat_catalog.json').read_text())['labels']==HOSTILE_LABELS
fake=SimpleNamespace(_merge_unique_types=lambda *groups:NarrationMixin._merge_unique_types(None,*groups),
                     _direction_label=lambda threat:WorldAnalysisMixin._direction_label(None,threat),
                     _hostile_label=lambda kind:WorldAnalysisMixin._hostile_label(None,kind))
source=ast.parse((ROOT/'dogido_server/state_machine/mixins/narration.py').read_text())
render=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='_render_player_chat_reply')
start=next(i for i,n in enumerate(render.body) if isinstance(n,ast.If) and ast.unparse(n.test)=="reply_stance == 'hypothesis' and topic_for_identify")
end=next(i for i,n in enumerate(render.body[start:],start) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='plausibility_hints' for t in n.targets))
wrapper=ast.parse('def project(self,event,reply_stance,topic_for_identify):\n return None')
wrapper.body[0].body=copy.deepcopy(render.body[start:end+1])+ast.parse('return dict(structure_ids=structure_ids,lines=plausibility_lines,hints=plausibility_hints)').body
ast.fix_missing_locations(wrapper)
ns={k:getattr(c,k) for k in ['normalize_biome_id','structure_ids_for_plausibility','build_plausibility_hint_lines']};exec(compile(wrapper,'canonical_narration','exec'),ns)
pool=[];keys={}
def intern(v):
 k=json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
 if k not in keys:keys[k]=len(pool);pool.append(v)
 return keys[k]
def hit(kind,id,label='',terms=None):return dict(entry_id=id,kind=kind,label_ja=label,score=10.0,matched_terms=terms or [],observed=False)
def plau(topics,explicit,biome,label,stance='hypothesis'):
 expected=c.build_plausibility_hint_lines(structure_ids=explicit,topic_hits=topics,current_biome_id=biome,current_biome_label=label)
 frame=SimpleNamespace(world=SimpleNamespace(biome=biome))
 projection=ns['project'](SimpleNamespace(_biome_label=lambda _:label),frame,stance,topics)
 return [intern(topics),intern(explicit),intern(biome),intern(label),intern(stance),intern(c.structure_ids_for_plausibility(topics)),intern(expected),intern(projection)]
def tactic(ids,visual):
 event=SimpleNamespace(visual_threats=[VisualThreat.model_validate(v) for v in visual])
 expected=NarrationMixin._player_chat_mob_tactics(fake,event,extra_types=ids)
 return [intern(ids),intern(visual),intern(c.collect_dogido_tactics_for_mobs(ids)),intern(expected)]
plaus=[];tactics=[]
for sid,entry in c.structure_entries().items():
 topics=[hit('structure',sid,entry.get('label') or '')]
 biomes=[None,'','unknown','minecraft:plains',' MINECRAFT:PLAINS ',' minecraft:plains ','old-growth-pine-taiga',*entry.get('biomes',[])]
 for biome in biomes:
  for label in [None,'',' 独自の場所 ']:plaus.append(plau(topics,[sid,sid,' '+sid+' ','unknown'],biome,label))
 for mob in c.structure_related_mobs(sid):
  moblabel=(c.mob_entry(mob) or {}).get('label') or mob
  for terms in [[],[moblabel],[entry.get('label') or ''],['前哨','旗','前哨基地'],[entry.get('label') or '',moblabel]]:
   for mid in [mob,'minecraft:'+mob,' minecraft:'+mob+' ']:plaus.append(plau([hit('mob',mid,moblabel,terms)],[],biomes[-1],'現在地'))
for stance in ['none','saw','clarify','hypothesis',' HYPOTHESIS ']:
 for query in ['前哨基地','ピリジャー','村','海底神殿','森の洋館','旗','ゾンビ','いいんじゃないかな']:
  topics=c.find_catalog_topics(query)
  plaus.append(plau(topics,[],None,None,stance))
for id in c.all_mob_entries():
 for ids in [[id],['minecraft:'+id],[' MINECRAFT:'+id+' '],[id,id,'unknown']]:
  tactics.append(tactic(ids,[]))
 for direction in [None,'front','front_right','right','back_right','back','back_left','left','front_left']:
  tactics.append(tactic([], [{'type':id,'distance':3.0,'direction':{'horizontal':direction}}]))
for first,second in [('creeper','zombie'),('zombie','creeper'),('unknown','creeper'),('minecraft:creeper','zombie'),('','creeper')]:
 for d1,d2 in [(None,5),(5,None),(None,None),(5,5),(999,1000),(1000,None),(0,0),(1,2)]:
  tactics.append(tactic(['creeper'],[{'type':first,'distance':d1,'direction':{'horizontal':'left'}},{'type':second,'distance':d2,'direction':{'horizontal':'right'}}]))

sections=[{'items':{'base':{'label':'基礎','dogido_tactics':{'notes':' 注記そのまま ','forbidden_advice':['禁止',' 禁止 ','',None,True],'safe_hints':['安全',' 安全 ']}},
 'other':{'label':'別','dogido_tactics':{'notes':'後','forbidden_advice':['別禁止'],'safe_hints':['安全','別案']}},
 'scalar':{'label':'字','dogido_tactics':{'forbidden_advice':'あい','safe_hints':{'先':'unused','後':'unused'}}},
 'empty':{},'no_tactics':{'label':'材料なし'},'bad_tactics':{'label':'無効','dogido_tactics':True}}},{'items':{}},{'items':{}}]
structures={'groups':{'g':{'label':'群','structures':{
 'one':{'japanese':'秘密基地','related_mobs':['base','base',' minecraft:base ','other'],'biomes':['plains','old-growth-taiga',None,'']},
 'two':{'japanese':'基地','related_mobs':['base'],'biomes':['taiga']},
 'empty':{'japanese':'空'},'invalid_biomes':{'japanese':'変','biomes':'plains'},
 'pillager_outpost':{'japanese':'前哨基地','related_mobs':['base'],'biomes':['plains']}}}}}
synthetic_p=[];synthetic_t=[]
with patch.object(c,'_mob_catalog_sections',return_value=dict(zip(['hostile','neutral','passive'],sections))),patch.object(c,'load_entry_catalog',return_value=structures):
 c.structures_for_mob_index.cache_clear()
 for topics in [[],[hit('structure','one')],[hit('mob','base','基礎',['基地'])],[hit('mob','base','基地',['基地'])],
                [hit('mob','base','基礎',['秘'])],[hit('structure','one'),hit('structure','two'),hit('structure','one')]]:
  for biome in [None,'','plains','minecraft:plains',' minecraft:plains ','old-growth-taiga','unknown']:
   synthetic_p.append(plau(topics,['empty','invalid_biomes','pillager_outpost',' one ','one','unknown'],biome,' '))
 for ids in [[],['base'],['other','base'],['base','other'],['base','base','minecraft:base'],['scalar'],['empty','no_tactics','bad_tactics'],['unknown'],[' MINECRAFT:BASE ']]:
  for visual in [[],[{'type':'witch','distance':None}],[{'type':'base','distance':1.0}]]:synthetic_t.append(tactic(ids,visual))
c.structures_for_mob_index.cache_clear()
paths=['dogido_server/entry_catalog.py','dogido_server/state_machine/mixins/narration.py','dogido_server/state_machine/mixins/world_analysis.py','dogido-rust/src/threat_catalog.json']
(OUT/'fixtures.json').write_text(json.dumps({'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},'pool':pool,'plausibility':plaus,'tactics':tactics,'synthetic':{'documents':{'mobs':sections,'structures':structures},'plausibility':synthetic_p,'tactics':synthetic_t}},ensure_ascii=False,separators=(',',':'))+'\n')
print(f'{len(plaus)+len(synthetic_p)} plausibility cases; {len(tactics)+len(synthetic_t)} tactics cases')
