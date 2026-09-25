#!/usr/bin/env python3
"""Produce offline reference cases from the selected Python checkout. No model/audio/server."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--python-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
sys.path.insert(0,str(a.python_root))
from dogido_server.models import GameEvent, VehicleState
from dogido_server.config import Settings
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.dialogue.light_source_comment_planner import LightSourceCommentContext,_parse_plan
from dogido_server.player_activity import player_vehicle_fact,vehicle_label
from dogido_server.entry_catalog import item_entries,all_mob_entries
from dogido_server.smell import smell_speech,event_smell_observation
from dogido_server.state_machine.villager_schedule import resolve_villager_schedule,project_villager_speech_facts
from dogido_server.state_machine.ambient_mob_catalog import AmbientMobReactionContext,ambient_mob_fallback_candidates
from datetime import datetime,timedelta,timezone
settings=Settings(_env_file=None,llm_enabled=False,audio_enabled=False,memory_enabled=False,main_language_dialogue_enabled=False,main_language_web_enabled=False)
m=DogidoStateMachine(settings)
base={'schema_version':'2026-05-24','adapter':'fixture','observed_at':'2026-09-25T00:00:00Z','event':{'name':'status_snapshot','source_kind':'system','priority_hint':'background','certainty':'high'}}
def event(extra):
    return GameEvent.model_validate(dict(base,**extra))
cases=[]
for text in ['この匂いなに？','なんかくさい','匂いする？','香りは何','ここって匂うね','臭っ','いい匂いだね','くさい？','この句の匂いという比喩','猫がいる','匂いについての言葉']:
    cases.append({'op':'scent_query','text':text,'expected':m._player_chat_mentions_scent(text)})
for obs in [None,{'status':'none'},{'status':'suppressed','suppression_reason':'submerged'},{'status':'suppressed','suppression_reason':'rain'}]:
    e=event({'smell_observation':obs});r=smell_speech(event_smell_observation(e));cases.append({'op':'smell','event':e.model_dump(mode='json'),'expected':{'text':r.text,'cue_id':r.cue_id}})
for ident,category,valence,source,specificity in [('bread','food','pleasant','hotbar','source'),('zombie','decay','unpleasant','entity','source'),('flower','flower','mixed','mixed','category'),('mixed','mixed','mixed','mixed','mixed')]:
    obs={'status':'present','smell_id':ident,'category':category,'valence':valence,'source_kind':source,'specificity':specificity,'effective_strength':3}
    try:e=event({'smell_observation':obs})
    except Exception:continue
    r=smell_speech(event_smell_observation(e));cases.append({'op':'smell','event':e.model_dump(mode='json'),'expected':{'text':r.text,'cue_id':r.cue_id}})
for prev,cur,lit,severe,before,recovered,recent in [(0,1,True,False,False,False,False),(1,2,True,False,False,False,False),(0,64,True,False,False,False,False),(0,1,False,True,True,False,False),(31,32,True,False,True,True,False),(0,1,True,False,False,False,True)]:
    c=LightSourceCommentContext(prev,cur,lit,severe,lit,before,recovered,recent)
    cases.append({'op':'light_context','previous':prev,'current':cur,'context':{'surroundings_reasonably_lit':lit,'severe_darkness':severe,'nearby_light_present':lit,'dark_push_context_before':before,'dark_push_recovered':recovered},'recent':recent,'expected':{'allowed_actions':list(c.allowed_actions()),'facts':c.basis_rows()}})
for kind,temper,caution,inventory in [('cow','friendly',None,{}),('cat','friendly',None,{}),('bee','neutral',None,{'campfire':1}),('enderman','neutral','gaze',{}),('villager','friendly',None,{}),('goat','neutral',None,{})]:
    e=event({'passive_mobs':[{'type':kind,'temperament':temper,'caution_reason':caution}],'inventory':inventory});lines=m._ambient_mob_fallback_candidates(e,e.passive_mobs)
    cases.append({'op':'mob_candidates','event':e.model_dump(mode='json'),'expected':lines})
for profession in [None,'none','nitwit','farmer','unknown','mod_job']:
    for baby in [False,True]:
        for time in [None,0,1999,2000,6000,9000,10000,11000,12000,23999]:
            cases.append({'op':'villager_schedule','profession':profession,'baby':baby,'time':time,'expected':resolve_villager_schedule(time,is_baby=baby,profession=profession)})
for from_ in ['clear','rain','thunder']:
    for to in ['clear','rain','thunder']:
        for cold in [False,True]:
            for dry in [False,True]:
                scene,line=m._weather_transition_scene(from_,to,cold,dry)
                cases.append({'op':'weather_scene','from':from_,'to':to,'cold':cold,'dry':dry,'expected':scene})
vehicles=['boat','oak_boat','bamboo_raft','chest_boat','minecart','horse','pig','camel','nautilus','mod:unknown']
for v in vehicles:
    vehicle=VehicleState(vehicle_id=v,activity='moving');cases.append({'op':'vehicle','vehicle':vehicle.model_dump(mode='json'),'expected':player_vehicle_fact(vehicle)})
for seq in [
    [('bread',0),('bread',1000),('cake',2000),('cake',3000),('cake',121000)],
    [('bread',0),('bread',1000),('none',120000),('bread',121000),('none',122000),('none',123000),('bread',124000),('bread',125000)],
    [('bread',0),('cake',1000),('bread',2000),('bread',3000)],
]:
    machine=DogidoStateMachine(settings);steps=[]
    for ident,now in seq:
        obs={'status':'none'} if ident=='none' else {'status':'present','smell_id':ident,'category':'food','valence':'pleasant','source_kind':'hotbar','specificity':'source','effective_strength':3}
        e=event({'smell_observation':obs});at=datetime(2026,9,25,tzinfo=timezone.utc)+timedelta(milliseconds=now)
        machine._update_smell_presence(e);result=machine._smell_observation_actions(e,at)
        steps.append({'event':e.model_dump(mode='json'),'now':now,'expected':[r.text for r in result]})
    cases.append({'op':'smell_sequence','steps':steps})
for kinds in [['cat','cat','cow','cat'],['villager','cat','villager']]:
    machine=DogidoStateMachine(settings);steps=[]
    for i,kind in enumerate(kinds):
        now=[0,30000,30001,120000][i];e=event({'world':{'time_of_day':3000},'passive_mobs':[{'type':kind}]});at=datetime(2026,9,25,tzinfo=timezone.utc)+timedelta(milliseconds=now)
        line=machine._emit_ambient_mob_comment_line(e,at)
        steps.append({'event':e.model_dump(mode='json'),'now':now,'expected':[line] if line else []})
    cases.append({'op':'mob_sequence','steps':steps})
a.output.write_text('[\n'+',\n'.join(json.dumps(case,ensure_ascii=False,separators=(',',':')) for case in cases)+'\n]\n')
# A compile-time label projection; fallback behavior is tested against player_activity.py.
labels={key:vehicle_label(key) for key in set(item_entries())|set(all_mob_entries()) if any(token in key for token in ('boat','raft','minecart'))}
a.output.with_name('vehicle_labels.json').write_text(json.dumps(labels,ensure_ascii=False,indent=2)+'\n')
print(f'{len(cases)} pure Python reference cases')
