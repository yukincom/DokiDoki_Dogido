#!/usr/bin/env python3
"""Freeze only pure player_chat prompt text and synthetic comparison cases."""
import argparse
import ast
import itertools
import json
from pathlib import Path
import sys
import types
from chat_prompt_helper import FIELDS

MARK = lambda name: '@@DOGIDO_' + name + '@@'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-root', type=Path, required=True)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(args.source_root))
    # Load the exact pure source modules, without llm/dialogue package initializers
    # that import optional model/server dependencies. No functions are replaced.
    for name in ('dogido_server.llm', 'dogido_server.dialogue'):
        module = types.ModuleType(name)
        module.__path__ = [str(args.source_root / name.replace('.', '/'))]
        sys.modules[name] = module
    from dogido_server.llm import player_chat_prompts as p
    from dogido_server.llm.character_mode import system_prompt_for_mode
    from dogido_server.llm.types import LeafGenerationRequest
    from dogido_server.dialogue.chat_policy import _POLICY_LINES
    tree = ast.parse((args.source_root/'dogido_server/llm/player_chat_prompts.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_player_chat_messages')
    prompt = next(n.value for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='user_prompt' for t in n.targets))
    parts = []
    for bit in prompt.values:
        if isinstance(bit, ast.Constant): parts.append({'text':bit.value})
        else:
            v=bit.value
            if isinstance(v,ast.Name): key=v.id
            elif isinstance(v,ast.Call) and isinstance(v.func,ast.Name): key=v.func.id.lstrip('_')
            else: raise ValueError(ast.unparse(v))
            parts.append({'field':key})
    data = {'systems':{m:system_prompt_for_mode(m) for m in ('peace','battle','tension','workshop')},
            'policies':_POLICY_LINES, 'template':parts, 'spirit':p._dogido_chat_spirit()}
    data['inventory'] = p._inventory_section({'asks_inventory':True,'inventory_summary':MARK('inventory'),'held_item_label':MARK('held')})
    data['hearing'] = p._hearing_block({'hearing_summary':MARK('summary'),'hearing_named_mobs':[MARK('named')],'hearing_source_labels':[MARK('source')]})
    data['observation'] = p._observation_block({'observation_summary':MARK('summary')},'')
    data['threat'] = p._observation_block({},MARK('threat'))
    data['look'] = p._observation_block({'look_target_label':MARK('look')},'')
    data['topic'] = p._topic_block({'catalog_topic_hints':MARK('topic')})
    data['plausibility'] = p._plausibility_block({'plausibility_hints':MARK('plausibility')})
    data['history'] = p._history_section({'conversation_history':MARK('history')})
    data['digest'] = p._digest_section({'event_digest':MARK('digest')})
    data['world'] = p._world_observation_rules({'world_observation_available':False})
    data['grounding_rules'] = {status:p._grounding_section({'player_chat_plan_action':'a','entity_grounding_status':status})[0]
        for status in ('not_applicable','observed','not_observed','unknown','ambiguous')}
    data['grounding_header'] = p._grounding_section({'player_chat_plan_action':'a'})[1].splitlines()[0]+'\n'
    data['conversation_repair'] = p._conversation_repair_section({'conversation_repair':{}}).replace('{}',MARK('repair'),1)
    data['priority'] = {}
    for home,safe,evidence,progress in itertools.product((False,True),(False,True),(False,True),('approaching','leaving','at_home','unknown')):
        key=f'{int(home)}{int(safe)}{int(evidence)}:{progress}'
        data['priority'][key]=p._current_turn_priority_section({'player_turn_plan':'return_home' if home else 'none',
            'safety_priority':'seek_safe_place' if safe else 'none','player_turn_plan_evidence':MARK('evidence') if evidence else '',
            'home_progress':progress})
    data['weather'] = {}
    for fact,context in itertools.product((False,True),repeat=2):
        data['weather'][f'{int(fact)}{int(context)}']=p._weather_block({'weather_label':MARK('weather'),
            'weather_fact':MARK('fact') if fact else '', 'weather_context':MARK('context') if context else ''})
    data['time']=p._time_block({'time_phase':MARK('time')})
    data['workshop']={}
    for verse,materials in itertools.product((False,True),repeat=2):
        data['workshop'][f'{int(verse)}{int(materials)}']=p._haiku_workshop_block({'haiku_workshop_open':'yes',
            'haiku_workshop_text':MARK('verse') if verse else '', 'haiku_workshop_materials':MARK('materials') if materials else ''})
    data['combat']={}
    for notes,hints in itertools.product((False,True),repeat=2):
        data['combat'][f'{int(notes)}{int(hints)}']=p._combat_safety_rules({'nearby_hostile_types':['zombie'],
            'mob_tactics_notes':[MARK('notes')] if notes else [],'safe_hints':[MARK('hints')] if hints else []},'peace')
    data['combat_without_nearby']=p._combat_safety_rules({},'battle')
    data['repair_feedback']=p._PLAYER_CHAT_REPAIR_FEEDBACK
    repair_messages=p.build_player_chat_messages(LeafGenerationRequest(kind='player_chat',fallback_text='',details={'player_chat_repair':{'candidate':'a','reason':'empty_output'}}))
    data['repair_user']=repair_messages[-1]['content'].replace(p._PLAYER_CHAT_REPAIR_FEEDBACK['empty_output'],MARK('feedback'),1)
    data['empty_candidate']=p.build_player_chat_messages(LeafGenerationRequest(kind='player_chat',fallback_text='',details={'player_chat_repair':{}}))[-2]['content']
    base={'user_text':'その話の続きを聞かせて。','player_name':'Player_1','biome':'草原','time_phase':'day','weather':'clear',
        'conversation_history':'user: これは合成の会話や。\nassistant: そうなんやな。','event_digest':'- 合成の出来事',
        'player_chat_plan_action':'continue_conversation','reply_stance':'none','character_mode':'peace'}
    variants=[{},base]
    for mode in ('peace','peaceful','calm','平和','battle','combat','panic','fight','バトル','tension','alert','caution','緊張','workshop','haiku_workshop','ワークショップ','共同編集者','unknown','',None):
        variants.append({**base,'character_mode':mode})
    for mode,active,visual,dark in itertools.product(('normal','panic','suppressed_panic','aftermath','alert','other'),(False,True),(False,True),(False,True)):
        variants.append({**base,'character_mode':None,'mode':mode,'combat_active':active,'has_visual_threats':visual,'danger_darkness_high':dark})
    for stance in ('none','saw','hypothesis','clarify','unknown',' SAW ',''):
        for policy in ('','固定の合成方針'):
            variants.append({**base,'reply_stance':stance,'reply_policy':policy})
    for status in ('not_applicable','observed','not_observed','unknown','ambiguous','other',''):
        variants.append({**base,'player_chat_plan_action':'check_entity_presence','entity_grounding_status':status,
            'entity_query':'前哨基地','entity_candidate_labels':['前哨基地','村'],'entity_observed_labels':['前哨基地']})
    for home,safe,progress in itertools.product(('none','return_home'),('none','seek_safe_place'),('approaching','leaving','at_home','unknown','other')):
        variants.append({**base,'player_turn_plan':home,'safety_priority':safe,'home_progress':progress,'player_turn_plan_evidence':'家へ帰る'})
    for sky,fact,context in itertools.product((None,False,True),(False,True),(False,True)):
        variants.append({**base,'include_sky_context':sky,'weather_label':'雨','weather_fact':'いまは雨','weather_context':'雪は観測していない',
                         **({} if fact else {'weather_fact':''}),**({} if context else {'weather_context':''})})
    variants += [{**base,**v} for v in [
        {'place_context':'屋内','structure_label':'村'}, {'place_context':'','structure_label':'村'},
        {'biome':'','time_phase':'','weather':''}, {'world_observation_available':False},
        {'asks_inventory':False,'inventory_summary':'石64','held_item_label':'石'},
        {'asks_inventory':True,'inventory_summary':'石64','held_item_label':'石'},
        {'asks_inventory':True,'inventory_summary':'','held_item_label':'石'},
        {'asks_inventory':True,'inventory_summary':'石64','held_item_label':''},
        {'hearing_named_mobs':['ゾンビ']}, {'hearing_source_labels':['鐘']}, {'hearing_summary':'鐘が鳴った'},
        {'hearing_named_mobs':' ゾンビ ','hearing_source_labels':['鐘','',' 水 ']},
        {'observation_summary':'視認: ヒツジ','look_target_label':'ヒツジ'},
        {'observation_summary':'視線先: ヒツジ','look_target_label':'ヒツジ'},
        {'threat_summary':'視認: ゾンビ','look_target_label':'ヒツジ'}, {'look_target_label':'ヒツジ'},
        {'catalog_topic_hints':'合成候補','plausibility_hints':'草原にあることがある'},
        {'nearby_hostile_types':['zombie'],'mob_tactics_notes':['項目'+str(i) for i in range(6)],'safe_hints':['助言'+str(i) for i in range(7)]},
        {'mob_tactics_notes':['項目'],'safe_hints':['助言'],'combat_active':True},
        {'nearby_hostile_types':'zombie','mob_tactics_notes':'項目','safe_hints':'助言'},
        {'conversation_repair':{'target_turn_id':'synthetic:reply','target_quote':'「一緒」','replacement_quote':'別々\nやで'}},
        {'conversation_repair':{}},
        {'user_text':'\x1c\u3000引用「合成」\n二行目\u3000\x1f','player_name':'\u3000 Player_1 \x1f'},
        {'user_text':MARK('weather')+'{field} {{player_name}}','conversation_history':MARK('history')},
        {'player_chat_plan_focus':'この合成値はpromptに入らない','forbidden_advice':['この合成値も入らない']},
    ]]
    for verse,materials in itertools.product((False,True),repeat=2):
        variants.append({**base,'haiku_workshop_open':'yes','haiku_workshop_text':'合成の句' if verse else '', 'haiku_workshop_materials':'合成材料' if materials else ''})
    variants.append({**base,**{k:None for k in ('user_text','player_name','place_context','reply_policy','hearing_named_mobs','conversation_history')}})
    cases=[]
    for details in variants:
        for repair in [None,*({'candidate':'  合成の候補や。\n二行目  ','reason':r} for r in data['repair_feedback']),{'candidate':'','reason':'unknown'},{}]:
            d=details.copy()
            if repair is not None:d['player_chat_repair']=repair
            req=LeafGenerationRequest(kind='player_chat',fallback_text='固定fallback',details=d,temperature=.65)
            cases.append({'details':{k:v for k,v in d.items() if k in FIELDS},'messages':p.build_player_chat_messages(req)})
    pool=[]; indices={}
    def intern(value):
        encoded=json.dumps(value,ensure_ascii=False,separators=(',',':'))
        if encoded not in indices:
            indices[encoded]=len(pool);pool.append(value)
        return indices[encoded]
    fixture={'pool':pool,'cases':[[intern(c['details']),[intern(m) for m in c['messages']]] for c in cases]}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in [('data.json',data),('fixtures.json',fixture)]:
        (args.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
    print(f'{len(cases)} synthetic prompt cases')
if __name__=='__main__':main()
