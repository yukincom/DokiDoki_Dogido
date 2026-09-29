#!/usr/bin/env python3
"""Persistent one-job canonical traces; synthetic conversation only, no LLM/audio/server."""
import argparse, copy, hashlib, json, logging, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);args=p.parse_args()
    assert os.environ.get('PYTHONHASHSEED')=='0'
    sys.path[:0]=[str(args.source_root),str(args.source_root/'dogido-rust/scripts')]
    logging.disable(logging.CRITICAL)
    import haiku_preparation as canonical
    from dogido_server.haiku import verse
    from dogido_server.entry_catalog import item_entries,block_entries
    from dogido_server.state_machine.constants import MOB_LABELS
    fixture=ROOT/'dogido-rust/fixtures';fixture.mkdir(exist_ok=True)
    (fixture/'haiku-preparation-entries.json').write_text(json.dumps({'items':item_entries(),'blocks':block_entries(),'mob_labels':MOB_LABELS},ensure_ascii=False,separators=(',',':'))+'\n')
    rows=[json.loads(s) for s in (fixture/'haiku-context.jsonl').read_text().splitlines()]
    ctor=canonical.DogidoStateMachine;original_reading=verse.hiraganize_japanese_text;count=0
    with (fixture/'haiku-preparation.jsonl').open('w') as f:
      for i,row in enumerate(rows):
        if i%7 and i>10:continue
        for variant in range(4):
          readings={}
          def hiraganize(s):
            value=original_reading(s);readings[s]=value;return value
          verse.hiraganize_japanese_text=hiraganize
          def machine(settings,llm):
            m=ctor(settings,llm=llm);m.state.current_structure=row['current_structure'];return m
          canonical.DogidoStateMachine=machine
          settings={'llm_enabled':variant!=0,'structured_max_tokens':192,'grounding_max_tokens':512,'generation_strategy':['three_slot','whole_poem','one_plus_two','two_plus_one'][variant],'max_regeneration_rounds':6}
          turns=[{'turn_id':f't{k}','player_text':s,'dogido_text':'うん、覚えとくで'} for k,s in enumerate(['牛の話をしたね','草地の読みはくさち','秘密の風景を見た','花が\nきれいだよ','雲を見ている','  昨日の   川柳  ','松明は持ってない'])] if variant>=2 else []
          corrections=[{'surface':surface,**v} for surface,v in row['readings']['by_surface'].items()]
          lessons=[{'note':'言葉の響きを大切に','forbidden_fragments':['かぜ']},{'note':'言葉の響きを大切に'},{'note':'解除','polarity':'loosen'}]
          start={'event':row['event'],'runtime':{'current_structure':row['current_structure'],'inventory_order':list(row['event']['inventory']),'player_name':row['player_name']},'settings':settings,'completed_turns':turns,'reading_corrections':corrections,'lessons':lessons,'dialogue_material':None}
          frames={'op':'haiku_context','event':row['event'],'completed_turns':turns,'reading_corrections':corrections,'lessons':lessons,'settings':{'llm_enabled':settings['llm_enabled'],'haiku_structured_max_tokens':192,'haiku_grounding_max_tokens':512,'haiku_generation_strategy':settings['generation_strategy'],'haiku_max_regeneration_rounds':6}}
          prep=canonical.HaikuPreparation();context=prep.handle(frames)
          result={'accepted':True,'text':context['fixed_text'] or ['','はなのかげ\nゆれるこもれび\nかぜをきく','ここで一句。\nしろいはな　くさちのうえで　かぜをきく','白い花\n草地の上で\n風を聞く'][variant],'line_sources':[],'failure_reason':None,'generation_strategy':'fixed_catalog' if variant==0 else settings['generation_strategy'],'regeneration_rounds':0,'prompt_variant':'source_atoms_slots_v2_kana_normalize'}
          payload={'found':False} if variant==1 else {'found':True,'description':'石のそばに花がある','elements':['石','花'],'focus':['光'],'confidence':.8}
          if variant==3:payload={'found':True,'elements':1}
          inspiration=None;materials=None;scene_payload={'found':False}
          if variant:
            inspiration=prep.handle({'op':'haiku_inspiration','payload':payload})
            atoms=prep._machine._pending_haiku_context.source_atoms
            if atoms and variant==2:scene_payload={'found':True,'clauses':[{'text':'花や石がある','basis_atom_ids':[atoms[0].atom_id],'claim_class':'interpreted','claim_scopes':[]}],'motifs':['白い花'],'focus':['プレイヤー'],'confidence':.8}
            if variant==3:scene_payload={'found':False,'motifs':1}
            materials=prep.handle({'op':'haiku_materials','payload':scene_payload})
            for index,atom in enumerate(materials['input']['source_atoms'][:3]):result['line_sources'].append({'line_index':index,'text':'元の読み','atom_ids':[atom['atom_id']],'sources':[atom]})
          emission=prep.handle({'op':'haiku_emission','result':result})
          output={'start':start,'context':context,'irony_payload':payload,'inspiration':inspiration,'scene_payload':scene_payload,'materials':materials,'result':result,'readings':readings,'emission':emission}
          json.dump(output,f,ensure_ascii=False,separators=(',',':'));f.write('\n');count+=1
    from dogido_server.models import GameEvent
    catalog=json.loads((args.source_root/'data/fallbacks/haiku.json').read_text())
    fallback_cases=[]
    for rule in catalog['rules']+catalog['group_defaults']+[{'biomes':[b]} for b in catalog['defaults']]:
        raw=copy.deepcopy(rows[0]['event']);raw['visual_threats']=[];raw['passive_mobs']=[];raw['nearby_resources']=[]
        raw['world'].update(sky_visible=True,biome=(rule.get('biomes') or catalog['biome_groups'].get((rule.get('biome_groups') or [''])[0]) or ['unknown'])[0],weather=rule.get('weather','clear'),time_phase=rule.get('time_phase','day'),danger_darkness_score=rule.get('danger_darkness_score_min',rule.get('danger_darkness_score_max',0)))
        raw['player']['position']['y']=rule.get('player_y_min',rule.get('player_y_max',64))
        for key,field in [('visual_threat_types_any','visual_threats'),('passive_mob_types_any','passive_mobs')]:
            if rule.get(key):raw[field]=[{'type':rule[key][0]}]
        names=rule.get('nearby_resource_names_any') or [('oak'+rule['nearby_resource_suffixes_any'][0])] if rule.get('nearby_resource_suffixes_any') else rule.get('nearby_resource_names_any') or []
        if names:raw['nearby_resources']=[{'type':'block','name':names[0],'distance':rule.get('nearby_resource_distance_max',1)}]
        variants=[raw]
        for sky in [False,None]:
            other=copy.deepcopy(raw);other['world']['sky_visible']=sky;variants.append(other)
        for delta in [-.001,.001]:
            other=copy.deepcopy(raw)
            if 'player_y_max' in rule or 'player_y_min' in rule:other['player']['position']['y']+=delta
            elif 'danger_darkness_score_max' in rule or 'danger_darkness_score_min' in rule:other['world']['danger_darkness_score']+=delta
            elif names:other['nearby_resources'][0]['distance']+=delta
            else:continue
            variants.append(other)
        for raw in variants:
            e=GameEvent.model_validate(raw);fallback_cases.append({'event':raw,'expected':ctor(canonical._PreparationSettings(_env_file=None,llm_enabled=False,audio_enabled=False,memory_enabled=False,decision_policy='legacy'),llm=None)._fallback_haiku_line(e)})
    (fixture/'haiku-preparation-fallback.json').write_text(json.dumps(fallback_cases,ensure_ascii=False,separators=(',',':'))+'\n')
    dialogue_cases=[]
    event=GameEvent.model_validate(rows[0]['event'])
    for turns in [[],[{'turn_id':'x','player_text':'同じ話','dogido_text':'うん'}]*7,[{'turn_id':str(i),'player_text':s,'dogido_text':'うん'} for i,s in enumerate(['短','  ','花。石！雲？空','犬'*161,'「草地」の風景','改行\nと\u3000空白','𠀀'*180])],[{'turn_id':'','player_text':'花','dogido_text':'うん'}],[{},None],[{'turn_id':'x','player_text':False,'dogido_text':'うん'}]]:
        try:expected=canonical.HaikuPreparation._dialogue_material({'completed_turns':turns},event);error=None
        except ValueError as exc:expected=None;error=type(exc).__name__
        dialogue_cases.append({'turns':turns,'expected':expected,'error':error})
    (fixture/'haiku-preparation-dialogue.json').write_text(json.dumps(dialogue_cases,ensure_ascii=False,separators=(',',':'))+'\n')
    sources=['dogido-rust/scripts/haiku_preparation.py','dogido-rust/scripts/reading_overlay.py','dogido_server/state_machine/mixins/haiku.py','dogido_server/haiku/materials.py','dogido_server/haiku/verse.py','dogido_server/memory_types.py','dogido_server/state_machine/haiku_catalog.py','dogido_server/dialogue/foreground.py','dogido_server/entry_catalog.py','data/fallbacks/haiku.json']
    meta={'cases':count,'fallback_cases':len(fallback_cases),'dialogue_cases':len(dialogue_cases),'python_hash_seed':0,'source_sha256':{s:hashlib.sha256((args.source_root/s).read_bytes()).hexdigest() for s in sources}}
    (fixture/'haiku-preparation-meta.json').write_text(json.dumps(meta,indent=2)+'\n');print(count)
if __name__=='__main__':main()
