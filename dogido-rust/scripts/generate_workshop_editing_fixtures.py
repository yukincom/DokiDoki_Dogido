"""Canonical Python local-edit/read fixtures; optional dictionary is a closed mock."""
from copy import deepcopy
from dataclasses import asdict
import itertools,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import workshop_oracle as helper
import combat_input_oracle
from dogido_server.haiku import workshop as w, verse as v, materials as m
from dogido_server import tts_reading

ROOT=Path(__file__).parents[1]
base=json.loads((ROOT/'src/workshop_prompt/fixtures.json').read_text())['projection_cases'][0]['frame']
readings={}
MAP={'桜':'さくら','葉':'は','黒':'くろ','斧':'おの','朝':'あさ','色':'いろ','夏':'なつ','月':'つき','窓':'まど','石炭':'せきたん','石':'いし','上五':'かみご','下五':'しもご','中七':'なかしち','句':'く','意味':'いみ','表現':'ひょうげん','変':'か','川柳':'せんりゅう','海':'うみ','剣':'けん','未':'未'}
def neutral(text):
    result=str(text or '')
    for a,b in MAP.items():result=result.replace(a,b)
    readings[text]=result
    return result
def forbidden():raise AssertionError('real dictionary forbidden')
tts_reading._get_unidic_tagger=forbidden
tts_reading.hiraganize_japanese_text=neutral
w.hiraganize_japanese_text=neutral
v.hiraganize_japanese_text=neutral
rows=[]
def add(op,frame):
    readings.clear(); frame=deepcopy(frame)
    if op=='parse':expected=asdict(w.parse_player_line_replacement(frame['text']))
    elif op=='material':expected=w.grounded_material_for_question(helper.snapshot_for(frame),frame['text'])
    elif op=='fragment':expected=w.mentioned_workshop_edit_fragment(helper.snapshot_for(frame),frame['text'],frame['replacement'])
    elif op=='normalize':expected=w.normalize_player_haiku_line(frame['text'])
    elif op=='short_materials':expected=m.short_material_entries(frame)
    elif op=='combat_fallback':expected=combat_input_oracle.Worker().handle(dict(frame,op='fallback'))
    else:expected=helper.handle(dict(frame,op=op))
    rows.append({'op':op,'input':frame,'readings':dict(readings),'expected':expected})
texts=['','こんにちは','上五をさくらいろにして','「さくらのは」を「さくらいろ」にして','桜の葉を夏の葉に変えて','さくらのはよりさくらいろの方がいい','さくらから夏に変えてください','上五と中七をなつに変えて','さくらを変えてからあさに変えて','さくらを変えてないけどあさに変えて','葉を夏の葉に変えてみて','うみでいいんじゃない','さくらいろの方がいいとは思わん','さくらいろにしたらどう','さくらいろにしたら困る','上五は「さくらいろ」の方がいい','『さくら』を『なつ』にするのはどう？','「桜」を「夏」に変えるのはどうかな？','「さくらのは」を「さくらいろ」にしてと言われた','「さくらのは」を「さくらいろ」にしたらどうなる？']
for prefix,value,verb,tail in itertools.product(['','上五を','真ん中のパートなら、','最初の句を','さくらのはより'],['さくらいろ','「さくらいろ」','夏の葉','あさ とか'],['に変えて','に変えてください','にしてみて','にしたらどう','の方がいい','でいいんじゃない'],['','。','？','とは言ってない']):texts.append(prefix+value+verb+tail)
for text in dict.fromkeys(texts):add('parse',{'text':text})
for text in ['桜の葉','「桜の葉」','『夏の葉』！',' さくら いろ ','\nさくら\n','さくら\nいろ','カタカナ','abc','未確定','ー','。','\x1cさくら\x1f'] :add('normalize',{'text':text})
for verse in ['さくらのは\nくろいおのへと\nあさのいろ','桜の葉\n黒い斧へと\n朝の色','さくらのは くろいおのへと あさのいろ','はる\nさくらのは\nあさ','未確定\n未確定\n未確定','さくら','さくら\r\nくろい\r\nあさ','「桜の葉」\n黒い斧へと\n朝の色']:
 for source in ['formal','conversational']:add('whole_verse',{'text':verse,'source':source})
for variant in ['plain','surface','pending','generated_pending','hard','duplicate']:
 f=deepcopy(base);view=f['workshop'];view['materials']={}
 if variant=='surface':
  for row,surface in zip(view['current_lines'],['桜の葉','黒い斧へと','朝の色']):row['surface_text']=surface
 if variant in ['pending','generated_pending']:
  lines=deepcopy(view['current_lines']);lines[0].update(surface_text='さくらいろ',reading_text='さくらいろ',provenance='player_explicit',source_atom_ids=[],source_atoms=[])
  view['pending']={'id':'fixture','base':deepcopy(view['current_lines']),'lines':lines,'selected_line':0,'generated_basis':{'present':True} if variant=='generated_pending' else None}
 if variant=='hard':view['materials']={'forbidden_fragments':['さくらいろ'],'allowed_readings':['つるはし'],'forbidden_readings':['しろ']}
 if variant=='duplicate':view['current_lines'][2].update(surface_text='さくらのは',reading_text='さくらのは')
 for text in texts[:21]+['「くろい」を「しろい」にして','くろいよりしろいの方がいい','「桜」を「夏」にして','桜から夏に変えて','上五の桜を夏に変えて','『さくらのは』を『さくらいろ』にするのはどう？']:
  frame=dict(f,text=text,interpreted_text=text)
  for op in ['fragment_candidate','explicit_discussion','knowledge_route','combat_fallback']:add(op,frame)
 for fragment,replacement,index in itertools.product(['','さくらのは','桜の葉','さくら','桜','ないもの'],['さくらいろ','夏の葉','なつ','くろいおのへと','未確定'],[None,0,1]):
  frame=dict(f,text=f'上五の{fragment}を{replacement}に変えて',proposal={'target_fragment':fragment,'replacement_text':replacement,'line_index':index})
  add('player_edit',frame);add('discussion_candidate',frame)
 for text in ['さくらのはって何？','桜の葉って何？','くろいおのへとって何？','海って何？','この句って何？','黙って、桜の葉って何？','/桜の葉とは','さくらのはって何？ 桜の葉を剣に持ち替えて']:
  add('knowledge_route',dict(f,text=text,interpreted_text=text));add('material',dict(f,text=text))
 for old,new in [('桜','夏'),('さくら','なつ'),('さくらのは','さくらいろ'),('あさのいろ','なつのいろ')]:
  for text in [f'{old}から{new}に変えて',f'上五の{old}を{new}に変えて',f'「{old}」を「{new}」にして']:
   add('fragment',dict(f,text=text,replacement=new))
# Stored source and poetic basis; never infer unassigned atoms as line sources.
for sourcekind in ['direct','derived','links','short','unassigned','tie','hidden']:
 f=deepcopy(base);view=f['workshop'];view['materials']={}
 for line in view['current_lines']:line['source_atoms']=[];line['source_atom_ids']=[]
 if sourcekind in ['direct','derived','tie']:
  view['current_lines'][0]['source_atoms']=[{'atom_id':'s','kind':'catalog_label','text':'窓'}]
 if sourcekind=='derived':
  view['current_lines'][0]['source_atoms']=[{'atom_id':'d','kind':'poetic_interpretation','text':'黒い石','basis_atom_ids':['s']}]
  view['materials']['source_atoms']=[{'atom_id':'s','kind':'catalog_label','text':'石炭','source_ref':'item:coal','hard_forbidden_readings':[],'shareable':False,'claim_class':'world_fact','claim_scopes':['visible_world'],'basis_atom_ids':[]}]
 if sourcekind=='links':view['materials']['fragment_links']=[{'surface':'さくらのは','material':'窓','source':'motif'}]
 if sourcekind=='short':view['materials']={'motifs':['さくら','あさ'],'interpretation':'桜と朝の風景'}
 if sourcekind=='unassigned':view['materials']['source_atoms']=[{'atom_id':'s','kind':'catalog_label','text':'窓'}]
 if sourcekind=='tie':view['current_lines'][1]['source_atoms']=deepcopy(view['current_lines'][0]['source_atoms'])
 if sourcekind=='hidden':view['materials']={'biome':'plains','material_visibility':{'biome':False}}
 for text in ['窓って何？','まどって何？','さくらのはの窓？','さくらのはのせきたん？','黒い石は石炭？','さくらとは','さくらのはって何？','くろいおのへとって何？','海って何？','  ']:
  add('material',dict(f,text=text));add('knowledge_route',dict(f,text=text,interpreted_text=text))
for label in ['朝','ただ','夜の空','プレイヤーの持つ葉。','ている','向き合っている','長い長い長い長い長い長い長い長い名詞','桜の葉と黒い斧','ああ…','minecraft:stone']:
 for key in ['motifs','held_item','inventory_items','nearby_blocks','dropped_items','passive_mobs','biome_ja','structure_ja','place_ja','interpretation']:
  value=[label] if key in ['motifs','inventory_items','nearby_blocks','dropped_items','passive_mobs'] else label
  add('short_materials',{key:value,'biome':'plains','structure':'village_plains','time_phase':'evening'})
for stem in ['plains','minecraft:plains','cherry_grove','unknown']:
 add('short_materials',{'biome':stem,'material_visibility':{'biome':False},'motifs':['草原','草原'],'interpretation':'草原と空、ただ'} )
out=ROOT/'src/workshop_editing/fixtures.json';out.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n');print(len(rows),'cases',out)
