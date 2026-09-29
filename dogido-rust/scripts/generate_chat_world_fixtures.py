"""Compare native current material to canonical Python, with no service construction."""
from pathlib import Path
import copy, hashlib, itertools, json, random, tempfile
from types import SimpleNamespace
from unittest.mock import patch
from dogido_server import entry_catalog as c
from dogido_server.models import GameEvent
from dogido_server.state_machine.mixins.narration import NarrationMixin
from dogido_server.state_machine.mixins.common import CommonMixin
from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin, MATERIAL_TOKEN_LABELS
from dogido_server.state_machine import constants
from dogido_server.state_machine.mixins import world_analysis as wa
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'dogido-rust/src/world_catalog'
class Canonical(NarrationMixin,CommonMixin,WorldAnalysisMixin):
 def __init__(self):
  self.state=SimpleNamespace(current_structure=None)
  self.settings=SimpleNamespace(home_bed_prompt_distance=12.0)
fake=Canonical()
assert fake._item_label.__func__ is NarrationMixin._item_label
pool=[];keys={}
def intern(x):
 key=json.dumps(x,ensure_ascii=False,sort_keys=False,separators=(',',':'))
 if key not in keys:keys[key]=len(pool);pool.append(x)
 return keys[key]
def frame(world=None,player=None,**other):
 return {'schema_version':'2026-05-24','adapter':'golden-synthetic','observed_at':'2026-09-29T00:00:00Z','sequence':1,'event':{'name':'status_snapshot','source_kind':'system','priority_hint':'background','certainty':'high'},'player':player or {},'world':world or {},**other}
def parsed(data):return GameEvent.model_validate(data)
def label_case(id):
 return [intern(id),intern({'item':fake._item_label(id),'block':fake._block_label(id),'biome':fake._biome_label(id),'structure':fake._structure_label(id)})]
labels=[]
for id in dict.fromkeys([*constants.ITEM_LABELS,*constants.BLOCK_LABELS,*constants.BIOME_LABELS,*constants.STRUCTURE_LABELS]):
 for x in [id,'minecraft:'+id,'minecraft:minecraft:'+id,' '+id.upper()+' ',id+':']:
  labels.append(label_case(x))
for id in [None,'','air','minecraft:air','other:stone','MINECRAFT:stone','minecraft:','minecraft:minecraft:','白い_雲','  白い雲　','foo_log','foo_planks','foo_wool','foo_bed','foo_leaves','_log','oak_log','unknown_item','\x1cstone\u3000']:
 labels.append(label_case(id))
materials=[[intern(x),intern(fake._material_label(x))] for x in [*MATERIAL_TOKEN_LABELS,'',' ','FOO_BAR','foo_bar',' ネコ ','\x1coak\u3000']]
climates=[]
for id in [None,'','unknown',*constants.BIOME_ENTRIES]:
 for x in ([id] if id is None else [id,'minecraft:'+id,' '+id.upper()+' ',' minecraft:'+id+' ']):
  e=fake._biome_entry(x)or{}
  climates.append([intern(x),intern({'biome_temperature':fake._biome_temperature(x),'biome_downfall':fake._biome_downfall(x),'snow_start_y':fake._biome_snow_start_y(x),'biome_group_id':str(e.get('group_id')or'')}),intern(fake._biome_entry(x))])
looks=[]
for kind,name in itertools.product(['block','entity','ENTITY',' entity ',''],['','stone','minecraft:oak_log','minecraft:creeper','creeper','unknown_mob','air','villager:farmer','桜の木']):
 e=parsed(frame(look_target={'kind':kind,'name':name}));looks.append([intern(e.look_target.model_dump(mode='json')),intern(fake._look_target_label(e))])
looks.append([intern(None),intern('')])
look_queries=[]
for text in ['','　','これ何','それ何だろう','あれ何だった','この花を見て','見てるだけ','指差してないよ','これ？','こ れ','そ　れ?','あれ','それ','これは綺麗','あれは元気かな？','これは違うの？','何かな','\x1cこれ\u3000','今日は元気？']:
 look_queries.append([intern(text),intern(fake._player_chat_wants_look_answer(text))])
inventories=[]
sets=[{}, {'stone':0,'dirt':-1},{'minecraft:stone':64,'dirt':64,'torch':5},{'minecraft:minecraft:torch':1,'minecraft:torch':1,'torch':1},{'foo_bar':3,'桜':2},{f'unknown_{i}':i+1 for i in range(30)},{id:i+1 for i,id in enumerate(constants.ITEM_LABELS)}, {'air':4,'minecraft:air':4,'STONE':4,'':1}]
for items in sets:
 for maximum in [-30,-1,0,1,2,18,10000]:
  e=parsed(frame(inventory=items));inventories.append([intern(e.inventory),maximum,intern(fake._player_chat_inventory_summary(e,max_items=maximum))])
places=[]
def place(data,structure=None,limit=12.):
 e=parsed(data);fake.state.current_structure=structure;fake.settings.home_bed_prompt_distance=limit
 places.append([intern(data),intern(structure),intern(limit),intern(fake._player_chat_place_context(e))])
for sky,cover,y,ceiling in itertools.product([None,False,True],[None,'unknown','foliage','FOLIAGE','fluid','stone'],[None,-0.5,0.5,48,49,51,100],[None,2.5,3.,8.,12.]):
 place(frame(world={'sky_visible':sky,'overhead_cover_type':cover,'ceiling_height':ceiling,'biome':'forest','local_light':8,'danger_darkness_score':.58},player={'position':{'y':y}}))
for spawn,distance,bed,door,window in itertools.product([None,False,True],[None,0.,12.,12.01],[0,1],[0,1],[None,True]):
 for base in [{},{'is_submerged':True},{'biome':'lush_caves'}]:
  place(frame(world={'sky_visible':False,'respawn_point_set':spawn,'respawn_distance':distance,'nearby_bed_count':bed,'nearby_door_count':door,'nearby_window_present':window,'ceiling_height':2.,**base},player={'position':{'y':40}}))
for biome in [None,'plains','lush_caves','minecraft:lush_caves',' MINECRAFT:LUSH_CAVES ','未知の森','minecraft:forest','forest','FOREST']:
 for structure in [None,'village','minecraft:village',' VILLAGE ','新しい建物']:
  for sky in [None,True,False]:place(frame(world={'biome':biome,'sky_visible':sky,'structure':'desert_pyramid'}),structure)
# Reuse full delivered environment inputs, including actual break/mining evidence and submerged/inside priority.
for line in (ROOT/'dogido-rust/fixtures/environment-projection.jsonl').read_text().splitlines():
 row=json.loads(line);data=row.get('event',row.get('input'))
 if data is not None:place(data,'village')
for y in [-2.5,-1.5,-0.,-.5,.5,1.5,2.5,1e20]:
 for ceiling in [-2.5,-1.5,-.5,-0.,.5,1.5,2.5]:place(frame(player={'position':{'y':y}},world={'ceiling_height':ceiling,'sky_visible':True}))
weather=[]
for biome,w,phase,y in itertools.product([None,'plains','snowy_plains','desert','cherry_grove'],[None,'clear','rain','thunder'],[None,'morning','day','evening','night'],[None,80,180]):
 e=parsed(frame(world={'biome':biome,'weather':w,'time_phase':phase},player={'position':{'y':y}}));weather.append([intern(e.model_dump(mode='json')),intern(fake._player_chat_weather_label(e)),intern(fake._player_chat_weather_fact(e))])
metrics=[[intern(v),intern(fake._biome_metric_value(v))]for v in [None,0,1,True,False,.5,'1',[],{}, {'bedrock':.3,'java':.8},{'bedrock':.3,'java':None},{'bedrock':.3,'java':'0.8'},{'java':True,'bedrock':.3},{'a':'x','b':2,'c':3},{'java':None,'a':1,'b':2},{'java':None,'b':2,'a':1}]]
# Exercise original recursive entry/ref/variant/duplicate rules using temporary canonical documents.
docs={f'{name}.json':{}for name in c.ITEM_CATALOG_FILES}
docs['minecraft_tools_and_utilities.json']={'top':{'items':{'note_item':{'japanese':'元の名','note':'重要'},'variant_base':{'label':'基本','variants':{'variant':'派生'}},'local':{'source':'a.json','japanese':'ローカル','note':'手元'},'unresolved':{'source':'missing.json','japanese':'残す名'},'empty':{'source':'missing.json'},'cycle':{'source':'cycle.json','label':'輪'},'nested':{'groups':{'nest':{'items':{'deep':'深い名'}}}}},'label':{'label_child':'表の名'}}}
docs['minecraft_combat_items.json']={'top':{'items':{'note_item':{'japanese':'上書きしない'},'variant':{'japanese':'新しい派生'}}}}
docs['a.json']={'top':{'items':{'local':{'source':'b.json','japanese':'中間'}},'groups':{'representative':{'japanese':'代表'}},'label':{'standalone':'単独'}}}
docs['b.json']={'top':{'items':{'local':{'japanese':'正本名','note':'正本注'}}}}
docs['cycle.json']={'top':{'items':{'cycle':{'source':'cycle.json'}}}}
docs['block/a.json']={'direct_labels':{'stone':'石','same':'同じ'},'top':{'items':{'stone':'長い石の名前','same':'別名','base':{'label':'元','variants':{'ignored':'無視'}},'map':{'label':{'red':'赤','blue':'青'}},'ref':{'source':'b.json','japanese':'局所'}},'groups':{'metadata':{'japanese':'代表ブロック'},'refs':{'source':'a.json','refs':['local','representative','standalone','missing']}}}}
docs['block/b.json']={'direct_labels':{'stone':'短','same':'同長'}}
synth_biomes={'groups':{'dry':{'label':'乾燥','description':'乾燥地域','temperature':'group only','biomes':{'x':{'japanese':'甲','temperature':{'bedrock':.3,'java':.8},'downfall':0,'snow_starts_at_y':' +100 '},'y':{'label':'ignored','temperature':True,'downfall':{'bad':'x','bedrock':False},'snow_starts_at_y':3.9},'z':{'japanese':None,'temperature':{'old':.4,'java':'no'},'snow_starts_at_y':True}}},'other':{'biomes':{'x':{'japanese':'乙','temperature':.5}},'extra':['keep']},'ignored':None}}
with tempfile.TemporaryDirectory()as tmp:
 path=Path(tmp)
 for name,doc in docs.items():p=path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(doc,ensure_ascii=False))
 (path/'minecraft_biome.json').write_text(json.dumps(synth_biomes,ensure_ascii=False))
 for fn in [c.load_entry_catalog,c.load_entry_catalog_documents,c.load_named_entry_catalog_documents,c._load_catalog_document]:fn.cache_clear()
 with patch.object(c,'ENTRIES_DIR',path):
  syitems=c.item_labels();syblocks=c.block_labels();sybiomes=c.biome_entries()
  with patch.object(wa,'BIOME_ENTRIES',sybiomes):
   syclimate=[]
   for id in sybiomes:
    entry=fake._biome_entry(id);syclimate.append([id,dict(biome_temperature=fake._biome_temperature(id),biome_downfall=fake._biome_downfall(id),snow_start_y=fake._biome_snow_start_y(id),biome_group_id=str(entry.get('group_id')or''))])
 for fn in [c.load_entry_catalog,c.load_entry_catalog_documents,c.load_named_entry_catalog_documents,c._load_catalog_document]:fn.cache_clear()
# Emit only the source file inventory; labels and reference resolution remain Rust-owned.
source_inventory = sorted((ROOT/'data/catalogs/entries').rglob('*.json'))
shared={'minecraft_biome.json':'BIOMES','minecraft_structure.json':'STRUCTURES','mobs/hostile.json':'HOSTILE','mobs/neutral.json':'NEUTRAL','mobs/passive.json':'PASSIVE'}
lines=['// Generated source inventory only; do not replace with derived label tables.', 'fn bundled_documents() -> serde_json::Map<String, serde_json::Value> {','    let mut out = serde_json::Map::new();']
for path in source_inventory:
 rel=path.relative_to(ROOT/'data/catalogs/entries').as_posix()
 if rel in shared:lines.append(f'    out.insert("{rel}".into(), crate::entry_catalog::{shared[rel]}.clone());')
 else:lines.append(f'    out.insert("{rel}".into(), serde_json::from_str(include_str!("../../../data/catalogs/entries/{rel}")).expect("entry source JSON"));')
lines.extend(['    out','}'])
(OUT/'documents.rs').write_text('\n'.join(lines)+'\n')
rules={'material_labels':MATERIAL_TOKEN_LABELS,'foliage_shade_biomes':sorted(constants.FOLIAGE_SHADE_BIOMES)}
(OUT/'rules.json').write_text(json.dumps(rules,ensure_ascii=False,indent=2)+'\n')
source_paths=['dogido_server/entry_catalog.py','dogido_server/state_machine/mixins/narration.py','dogido_server/state_machine/mixins/world_analysis.py','dogido_server/state_machine/mixins/common.py','dogido_server/state_machine/constants.py',*[str(p.relative_to(ROOT))for p in sorted((ROOT/'data/catalogs/entries').rglob('*.json'))]]
result=dict(source_hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()for p in source_paths},pool=pool,items=constants.ITEM_LABELS,blocks=constants.BLOCK_LABELS,biomes=constants.BIOME_ENTRIES,labels=labels,materials=materials,climates=climates,looks=looks,look_queries=look_queries,inventories=inventories,places=places,weather=weather,metrics=metrics,synthetic=dict(documents=docs,biomes_document=synth_biomes,items=syitems,blocks=syblocks,biomes=sybiomes,climates=syclimate))
(OUT/'fixtures.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
print({k:len(v)for k,v in result.items()if k not in {'source_hashes','pool','synthetic'}})
