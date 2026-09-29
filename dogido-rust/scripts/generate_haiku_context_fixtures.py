#!/usr/bin/env python3
"""Pure canonical haiku context captures; synthetic observations, no model/service.

Use PYTHONHASHSEED=0: canonical portal tags are a frozenset. Its otherwise random
iteration order is pinned in rules.json rather than claimed stable across Python runs.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import itertools
import json
import logging
import os
from pathlib import Path
import random
import sys
import unicodedata
ROOT=Path(__file__).resolve().parents[2]

def event(**fields):
    e={"schema_version":"2026-05-24","adapter":"golden-synthetic","observed_at":"2026-09-29T00:00:00Z","sequence":1,
       "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
       "player":{"name":"試作","held_item":"minecraft:stone_pickaxe","position":{"y":64},"dimension":"minecraft:overworld"},
       "world":{"biome":"plains","weather":"clear","time_phase":"day","sky_visible":True},
       "inventory":{"minecraft:diamond":3,"minecraft:apple":8,"minecraft:stone":64,"minecraft:oak_log":4}}
    for k,v in fields.items():
        if isinstance(v,dict) and k in ["player","world","event"]: e[k].update(v)
        else:e[k]=v
    for r in e.get("nearby_resources",[]): r.setdefault("type","block")
    return e

def cases():
    for biome,sky,weather,y in itertools.product(["plains","desert","snowy_plains","deep_dark","lush_caves","mushroom_fields"],[None,False,True],["clear","rain","thunder"],[8,64,180]):
        yield event(world={"biome":biome,"sky_visible":sky,"weather":weather},player={"position":{"y":y}}),None
    for current,observed in itertools.product([None,"village","mineshaft","minecraft:stronghold"," 謎の家 "],[None,"","village","minecraft:stronghold"]):
        yield event(world={"structure":observed}),current
    for held in [None,"air","minecraft:apple","minecraft:iron_shovel","minecraft:diamond_pickaxe","minecraft:axe","fishing_rod","crossbow","telescope","minecraft:unknown_tool"]:
        for inventory in [{},{"minecraft:stone":64},{"minecraft:bread":1,"minecraft:diamond":1,"minecraft:red_dye":1},{"apple":1,"minecraft:apple":4,"stone":64},{"minecraft:apple":4,"apple":1,"stone":64}]:
            yield event(player={"held_item":held},inventory=inventory),None
    for seq in [None,0,1,2,3,100,9223372036854775807]:
        for name in [None,"","p","長い名前を十二文字以上並べて比較","🐕😀プレイヤー"]:
            yield event(sequence=seq,player={"name":name},inventory={"apple":1,"diamond":1,"red_dye":1,"book":1,"nether_star":1}),None
    resources=[{"name":n,"distance":d} for n,d in [("crafting_table",1),("stone",2),("oak_log",3),("oak_door",4),("dirt",5),("chest",6),("birch_leaves",7),("stone",None)]]
    for portal in [None,"nether_portal","end_portal","end_gateway","custom_portal"]:
        yield event(world={"nearby_portal_type":portal,"nearby_window_present":True},nearby_resources=resources,dropped_items=[{"name":x,"distance":i,"count":1} for i,x in enumerate(["apple","bread","diamond","stone","emerald"])],passive_mobs=[{"type":x} for x in ["cow","pig","sheep","cat"]]),"village"
    for mobs in [["cow"],["sheep"],["tropical_fish"],["villager"],["minecraft:cow"],["unknown"],["cow","pig","sheep","cat","wolf"],["cow","cow","minecraft:cow","pig"]]:
        yield event(passive_mobs=[{"type":m} for m in mobs],world={"biome":"desert","time_phase":"night"},nearby_resources=[{"name":"birch_leaves","distance":2}]),None
    for vehicle in [None,{"vehicle_id":"minecraft:boat","activity":"rowing"},{"vehicle_id":"minecraft:horse","activity":"running"},{"vehicle_id":"minecraft:minecart","activity":"moving"}]:
        yield event(player={"vehicle":vehicle},world={"sky_visible":False,"nearby_window_present":True},nearby_resources=[{"name":"stone","distance":2}],recent_block_breaks=[{"name":"stone","count":2}]),None
    for biome in ["minecraft:plains"," minecraft:deep_dark ","minecraft:lush_caves"," MINECRAFT:DESERT ","minecraft:minecraft:plains","草地","UNKNOWN"]:
        yield event(world={"biome":biome,"sky_visible":False}),None
    for reading in ['くさち','そうげん',' 草地 ']:
        yield event(world={'biome':'meadow'},_reading_snapshot={'草地':{'reading':reading,'forbidden_readings':['そうち']}}),None
    rng=random.Random(29)
    for i in range(100):
        ids=rng.sample(["diamond","apple","bread","stone","book","red_dye","oak_log","birch_log","birch_planks","iron_shovel","iron_pickaxe","unknown","air","minecraft:apple"],rng.randrange(1,10))
        inventory={k:rng.randrange(0,65) for k in ids}
        yield event(sequence=i,inventory=inventory,player={"held_item":rng.choice(["iron_pickaxe","iron_shovel","air","bread"])},world={"biome":rng.choice(["plains","desert","forest"]),"sky_visible":rng.choice([None,True,False])},passive_mobs=[{"type":rng.choice(["cow","sheep","pig"])} for _ in range(rng.randrange(5))]),None

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path,default=ROOT);parser.add_argument('--output-dir',type=Path,default=ROOT/'dogido-rust'/'fixtures');args=parser.parse_args()
    if os.environ.get('PYTHONHASHSEED')!='0':raise ValueError('run with PYTHONHASHSEED=0 for canonical frozenset order')
    logging.disable(logging.CRITICAL);sys.path.insert(0,str(args.source_root))
    from dogido_server.state_machine.machine import DogidoStateMachine
    from dogido_server.config import Settings
    from dogido_server.models import GameEvent
    from dogido_server.entry_catalog import item_entries,block_entries,biome_entries
    from dogido_server.state_machine.constants import MOB_LABELS
    from dogido_server.state_machine.mixins.haiku import _HAIKU_NOUN_FAMILIES
    from dogido_server.state_machine.haiku_context import IronyContext,SceneContext
    from dogido_server import catalog_readings
    args.output_dir.mkdir(parents=True,exist_ok=True)
    rules={"portal_tags":list(frozenset({"異世界","ワープ","光","不思議"})),"noun_families":[asdict(f) for f in _HAIKU_NOUN_FAMILIES]}
    rules_path=ROOT/'dogido-rust/src/haiku/materials/rules.json';rules_path.parent.mkdir(parents=True,exist_ok=True);rules_path.write_text(json.dumps(rules,ensure_ascii=False,indent=2)+'\n')
    digits={chr(c):str(unicodedata.decimal(chr(c))) for c in range(sys.maxunicode+1) if unicodedata.category(chr(c))=='Nd'}
    (ROOT/'dogido-rust/src/haiku/context-decimal-digits.json').write_text(json.dumps(digits,ensure_ascii=False,separators=(',',':'))+'\n')
    (args.output_dir/'haiku-context-entries.json').write_text(json.dumps({"items":item_entries(),"blocks":block_entries(),"mob_labels":MOB_LABELS},ensure_ascii=False,separators=(',',':'))+'\n')
    machine=DogidoStateMachine(Settings(_env_file=None,decision_policy='legacy',audio_enabled=False,memory_enabled=False,llm_enabled=False),llm=None)
    count=0
    with (args.output_dir/'haiku-context.jsonl').open('w') as f:
        for raw,current in cases():
            reading_values=raw.pop('_reading_snapshot',{})
            catalog_readings.configure_corrections_path(None)
            for surface,correction in reading_values.items():
                catalog_readings.apply_overlay_correction(surface=surface,reading=correction['reading'],wrong_reading=(correction['forbidden_readings'] or [None])[0])
            e=GameEvent.model_validate(raw);machine.state.current_structure=current
            context=machine._haiku_context(e)
            irony=IronyContext(found=True,kind='contrast',description='石のそばに宝がある',elements=('石','ダイヤモンド'),focus=('持ち物',),confidence=.85)
            scene=SceneContext()
            lifted=machine._scene_for_spoken_irony(irony,scene,source_atoms=context.source_atoms)
            row={"event":raw,"readings":{"by_surface":reading_values},"current_structure":current,"player_name":machine._player_call_name(e),"expected":asdict(context),"irony":asdict(irony),"lifted":asdict(lifted),"details":{"irony":context.irony_details(),"scene":context.scene_details(irony),"prompt":context.prompt_details(irony,lifted)}}
            json.dump(row,f,ensure_ascii=False,default=lambda v:list(v),separators=(',',':'));f.write('\n');count+=1
    catalog_readings.configure_corrections_path(None)
    selection=[]
    for id,quantity in itertools.product(list(item_entries())+["dirt","unknown","prefix: apple ","prefix:red_dye","my_torch","rare_flower","minecraft:air"],[1,4,64]):
        selection.append({"id":id,"count":quantity,"weight":machine._haiku_pocket_weight(id,label="",count=quantity),"work_tool":machine._is_haiku_work_tool_item(id)})
    (args.output_dir/'haiku-selection.json').write_text(json.dumps(selection,ensure_ascii=False,separators=(',',':'))+'\n')
    constraints=[]
    for held,motifs,sky,biome in itertools.product(['iron_shovel','diamond_pickaxe','iron_axe','iron_hoe','apple'],[[],['シャベル'],['ツルハシ','斧'],['おの','くわ']],[False,True],['plains','deep_dark','unknown','minecraft:deep_dark']):
        e=GameEvent.model_validate(event(player={'held_item':held},world={'sky_visible':sky,'biome':biome}));scene=SceneContext(motifs=tuple(motifs));
        lessons=[{'note':'音の響きを意識して','forbidden_fragments':['あめ']},{'note':'音の響きを意識して'},{'note':'解除','polarity':' loosen '},{'note':'景色を少し入れる'},{'note':'短い語が好き'},{'note':'四つ目は省略'}]
        machine.haiku_lessons_provider=lambda:lessons
        constraints.append({'event':e.model_dump(mode='json'),'motifs':motifs,'lessons':lessons,'expected':machine._haiku_constraint_details(e,scene)})
    (args.output_dir/'haiku-constraints.json').write_text(json.dumps(constraints,ensure_ascii=False,separators=(',',':'))+'\n')
    for reading in [None,'くさち','そうげん',' 草地 ']:
        catalog_readings.configure_corrections_path(None)
        if reading: catalog_readings.apply_overlay_correction(surface='草地',reading=reading,wrong_reading='そうち')
        for held in ['apple','iron_pickaxe']:
            e=GameEvent.model_validate(event(player={'held_item':held},world={'biome':'meadow'}))
            scene=SceneContext()
            machine.haiku_lessons_provider=lambda:[]
            constraints.append({'event':e.model_dump(mode='json'),'motifs':[],'lessons':[], 'readings':{'by_surface':{'草地':{'reading':reading,'forbidden_readings':['そうち']}}} if reading else {},'expected':machine._haiku_constraint_details(e,scene)})
    catalog_readings.configure_corrections_path(None)
    (args.output_dir/'haiku-constraints.json').write_text(json.dumps(constraints,ensure_ascii=False,separators=(',',':'))+'\n')
    payloads=[None,False,{}, {'found':False},{'found':True,'description':'  石やな  ','elements':['石',None,1,False],'focus':'あい','confidence':2},{'found':True,'confidence':'nan'}, {'found':True,'confidence':-1},{'found':True,'elements':1},{'found':False,'focus':2},{'found':True,'elements':{'石':1,'':2},'confidence':'invalid'}]
    payloads += [{'found':True,'confidence':v} for v in ['0_1','0.8_5','１','０.８','١.٢','1_','_1','1__0',' +Infinity ','NaN','\u001c1','0x1',' 0.5 ','1e-1','١e-١']]
    irony_rows=[]
    for payload in payloads:
        try: expected=asdict(IronyContext.from_mapping(payload));error=None
        except (TypeError,ValueError,OverflowError) as exc:expected=None;error=type(exc).__name__
        irony_rows.append({'payload':payload,'expected':expected,'error':error})
    (args.output_dir/'haiku-irony.json').write_text(json.dumps(irony_rows,ensure_ascii=False,separators=(',',':'))+'\n')
    from dogido_server.haiku.source_atoms import HaikuSourceAtom,PrefaceClause
    atoms=tuple(HaikuSourceAtom(atom_id=f'a{i}',text=t,source_ref=f'observation:{i}',field_path='label',observation_role='observation',kind='observation',claim_class='factual',claim_scopes=('observed_state',)) for i,t in enumerate(['黒い石','丸石','ダイヤモンド','月','石の柱','黒い石']))
    scene_payloads=[None,{}, {'found':False}, {'text':'石がある','basis_atom_ids':['a0'],'claim_class':'factual'}, {'found':True,'clauses':[{'text':'黒い石やな','basis_atom_ids':['a0'],'claim_class':'factual'}],'motifs':['石'],'focus':['ここ'],'confidence':.8}, {'found':True,'clauses':[]}, {'found':True,'clauses':[{'text':'根拠なし','basis_atom_ids':['missing'],'claim_class':'factual'}]}, {'found':False,'motifs':1}]
    scene_rows=[]
    for payload in scene_payloads:
        try: expected=asdict(SceneContext.from_mapping(payload,source_atoms=atoms));error=None
        except (TypeError,ValueError,OverflowError) as exc:expected=None;error=type(exc).__name__
        scene_rows.append({'payload':payload,'atoms':[a.to_prompt_dict() for a in atoms],'expected':expected,'error':error})
    (args.output_dir/'haiku-scene.json').write_text(json.dumps(scene_rows,ensure_ascii=False,separators=(',',':'))+'\n')
    lifted=[]
    for desc,elements,focus in itertools.product(['黒い石やな','石','石の柱とダイヤモンド','丸い月',''],[[],['黒い石'],['黒い石','ダイヤモンド'],['月']],[[],['丸石']]):
        irony=IronyContext(True,'contrast',desc,tuple(elements),tuple(focus),.8)
        scene=SceneContext(True,(PrefaceClause('ここや',('a1','a2','a3'),'factual',('observed_state',)),),('前の材料',),(),.9)
        lifted.append({'atoms':[a.to_prompt_dict() for a in atoms],'irony':asdict(irony),'scene':asdict(scene),'expected':asdict(machine._scene_for_spoken_irony(irony,scene,source_atoms=atoms)),'basis':machine._irony_basis_atom_ids(irony,atoms)})
    (args.output_dir/'haiku-scene-lift.json').write_text(json.dumps(lifted,ensure_ascii=False,separators=(',',':'))+'\n')
    sources=[
        'dogido_server/state_machine/mixins/haiku.py',
        'dogido_server/state_machine/haiku_context.py',
        'dogido_server/haiku/source_atoms.py',
        'dogido_server/catalog_readings.py',
        'dogido_server/entity_voice_catalog.py',
        'dogido_server/entry_catalog.py',
        'dogido_server/minecraft_ids.py',
        'dogido_server/environment_context.py',
        'dogido_server/state_machine/precipitation.py',
        'dogido_server/player_activity.py',
        'dogido-rust/scripts/haiku_preparation.py',
    ]
    sources += [str(p.relative_to(args.source_root)) for p in sorted((args.source_root/'data/catalogs/entries').rglob('*.json'))]
    meta={'contexts':count,'constraints':len(constraints),'irony':len(irony_rows),'selection':len(selection),'scene':len(scene_rows),'scene_lift':len(lifted),'python_hash_seed':0,'unicode_version':unicodedata.unidata_version,'source_sha256':{p:hashlib.sha256((args.source_root/p).read_bytes()).hexdigest() for p in sources}}
    (args.output_dir/'haiku-context-meta.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps({k:v for k,v in meta.items() if k!='source_sha256'}))
if __name__=='__main__':main()
