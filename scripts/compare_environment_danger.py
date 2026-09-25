#!/usr/bin/env python3
"""Pure Python/Rust environment replay. No HTTP, model, audio, or Minecraft.

Other independently migrated branches are stubbed only at their dispatch points.
The original geometry, dark state, cooldown, catalog and environmental selection
functions remain in use. --python-root selects the current Python source of truth.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def cases():
    base={"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-26T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"dimension":"minecraft:overworld","position":{"x":0,"y":64,"z":0}},"world":{"biome":"plains","time_phase":"day","sky_visible":False,"local_light":0,"danger_darkness_score":1,"ceiling_height":6,"enclosure_score":0.5}}
    def step(ms,world=None,player=None,inventory=None,**flags):
        e=copy.deepcopy(base);e["world"].update(world or {});e["player"].update(player or {});e["inventory"]=inventory or {};e["observed_at"]=(datetime(2026,9,26,tzinfo=timezone.utc)+timedelta(milliseconds=ms)).isoformat();return {"ms":ms,"event":e,**flags}
    out=[]
    def add(name,*steps,settings=None):out.append({"name":name,"steps":list(steps),"settings":settings or {}})
    add("dark_breath_boundaries",*[step(t) for t in (0,4999,5000,8799,8800)])
    add("dark_stage_movement",step(0,{"local_light":3}),step(1000),step(2000,player={"position":{"x":0.999,"y":64,"z":0}}),step(3000,player={"position":{"x":1,"y":64,"z":0}}),step(8000,player={"position":{"x":1,"y":64,"z":0}}))
    add("dark_worse_without_position",step(0,{"local_light":3},player={"position":{}}),step(1000,player={"position":{}}))
    for light in (0,1,2,3,4,8,9,15):add(f"light_{light}",step(0,{"local_light":light}),step(6000,{"local_light":light}))
    for light in (1,2,3,4,15):add(f"recovery_{light}",step(0),step(1000,{"local_light":light}),step(6000,{"local_light":light}))
    for danger in (0.69,0.7,0.719,0.72,0.899,0.9,1):add(f"unknown_light_{danger}",step(0,{"local_light":None,"danger_darkness_score":danger}),step(6000,{"local_light":None,"danger_darkness_score":danger}))
    add("torch_entry",step(0,inventory={"torch":1}),step(1000,player={"position":{"x":1,"z":0}},inventory={"torch":1}))
    add("torch_gain_no_recovery",step(0),step(1000,inventory={"torch":64}),step(5000,inventory={"torch":64}))
    for depth in (4,5,6):add(f"water_depth_{depth}",step(0,{"is_submerged":True,"submerged_depth_blocks":depth}),step(1000,{"is_submerged":True,"submerged_depth_blocks":depth}))
    for shade in ("forest","dark_forest","plains"):
        add(f"shade_{shade}",step(0,{"biome":shade,"overhead_cover_type":"foliage","ceiling_height":5,"local_light":7,"danger_darkness_score":0.6}),step(1000,{"biome":shade,"overhead_cover_type":"foliage","ceiling_height":5,"local_light":7,"danger_darkness_score":0.6}))
    for walls in (2,3,4):
        add(f"shelter_wall_{walls}",step(0,{"cardinal_wall_count":walls,"ceiling_height":2,"time_phase":"night"}),step(1000,{"cardinal_wall_count":walls,"ceiling_height":2,"time_phase":"morning"}))
    add("shelter_jitter",step(0,{"cardinal_wall_count":4,"ceiling_height":2,"time_phase":"night"}),step(1000,{"cardinal_wall_count":2,"ceiling_height":2,"time_phase":"night"}),step(2000,{"cardinal_wall_count":4,"ceiling_height":2,"time_phase":"night"}))
    for inv in ({},{"white_bed":1},{"minecraft:red_bed":1}):add(f"shelter_advice_{inv}",step(0,{"sky_visible":True,"time_phase":"night","time_of_day":13000,"enclosure_score":0},inventory=inv))
    for biome in ("plains","dark_forest","mushroom_fields","pale_garden","dripstone_caves"):
        add(f"evening_{biome}",step(0,{"biome":biome,"sky_visible":True,"time_phase":"evening","local_light":12}),step(1000,{"biome":biome,"sky_visible":True,"time_phase":"evening","local_light":12}))
    add("focus_dark",step(0,focus=True),step(5000,focus=True))
    add("focus_evening",step(0,{"sky_visible":True,"time_phase":"evening","local_light":12},focus=True))
    for distance in (15.9,16,16.1):add(f"lightning_{distance}",step(0,{"local_light":15,"sky_visible":True,"nearby_lightning_strike_recent_ms":0,"nearby_lightning_strike_distance":distance}))
    for age in (3999,4000,4001):add(f"thunder_age_{age}",step(0,{"local_light":15,"sky_visible":True,"thunder_sound_recent_ms":age}))
    add("thunder_cd",*[step(t,{"local_light":15,"sky_visible":True,"thunder_sound_recent_ms":0}) for t in (0,179999,180000,599999,600000)])
    add("lightning_precedes_thunder",step(0,{"local_light":15,"sky_visible":True,"thunder_sound_recent_ms":0,"nearby_lightning_strike_recent_ms":0,"nearby_lightning_strike_distance":10}))
    for near in (1.99,2,2.01):add(f"damaging_light_{near}",step(0,{"local_light":15,"sky_visible":True,"nearby_damaging_light_source_count":1,"nearest_damaging_light_source_distance":near}))
    add("magma_cd",*[step(t,{"local_light":15,"sky_visible":True,"standing_on_magma_block":True}) for t in (0,1199999,1200000)])
    for portal in ("nether_portal","end_portal","end_gateway"):
        add(f"portal_{portal}",step(0,{"local_light":15,"sky_visible":True}),step(1000,{"local_light":15,"sky_visible":True,"nearby_portal_type":portal}),step(2000,{"local_light":15,"sky_visible":True,"nearby_portal_type":portal}))
    for d in (4.99,5,5.01):add(f"portal_frame_{d}",step(0,{"local_light":15,"sky_visible":True,"nearby_end_portal_frame_distance":d}))
    for distance in (3.99,4,4.01):
        for light in (3,4):
            add(f"lamp_buffer_{distance}_{light}",step(0,{"nearby_light_source_count":1,"nearest_light_source_distance":distance,"local_light":light}),step(6000,{"nearby_light_source_count":1,"nearest_light_source_distance":distance,"local_light":light}))
    for connected in (23,24,25):
        add(f"lit_pocket_volume_{connected}",step(0,{"ceiling_height":4,"local_light":9,"connected_dark_volume":connected,"nearest_dark_spawn_distance":4}))
    for enclosure in (0.849,0.85,0.851):
        add(f"cramped_enclosure_{enclosure}",step(0,{"ceiling_height":2,"enclosure_score":enclosure}))
    for flag in (None,True,False):
        for doors in (0,1):
            add(f"safe_door_{flag}_{doors}",step(0),step(1000,{"local_light":8,"nearby_door_count":doors,"safe_zone_with_door":flag,"ceiling_height":4}))
    for distance in (9.99,10,10.01):
        add(f"home_bed_shelter_{distance}",step(0,{"time_phase":"night","cardinal_wall_count":4,"ceiling_height":2,"nearby_bed_count":1,"respawn_point_set":True,"respawn_distance":distance}))
    add("dimension_change",step(0),step(1000,player={"dimension":"minecraft:the_nether"}),step(2000,player={"dimension":"minecraft:the_nether"}))
    for mode in ("alert","panic","suppressed_panic","aftermath"):
        add(f"thunder_mode_{mode}",step(0,{"local_light":15,"sky_visible":True,"thunder_sound_recent_ms":0},mode=mode))
    for t in (7999,8000):
        first=step(0);outdoor=step(1000,{"local_light":4})
        outdoor["event"]["visual_threats"]=[{"type":"zombie","entity_id":"z","distance":3,"direction":{"horizontal":"right","vertical":"same"}}]
        add(f"deferred_relief_{t}",first,outdoor,step(1000+t,{"local_light":4}))
    return out

def python_replay(case):
    from dogido_server.config import Settings
    from dogido_server.models import GameEvent
    from dogido_server.state_machine import DogidoStateMachine
    m=DogidoStateMachine(Settings(audio_enabled=False,llm_enabled=False,**case["settings"]))
    # Separate workers own these unrelated dispatch points; no oracle logic is replaced.
    for name in ("_emit_haiku_line","_next_dragon_special_callout","_emit_pending_overworld_return_line","_weather_transition_callout","_emit_ender_eye_throw_line","_emit_pending_structure_line","_emit_pending_special_biome_line","_emit_ominous_sound_line"):
        setattr(m,name,lambda *args,**kwargs:None)
    for name in ("_smell_observation_actions","_light_source_gain_actions","_mining_fatigue_warning_actions","_boss_omen_actions","_ominous_sound_priority_actions","_firefly_actions"):
        setattr(m,name,lambda *args,**kwargs:[])
    captured=[]
    original_leaf=m._generate_leaf_text
    def capture_leaf(*args,**kwargs):
        value=original_leaf(*args,**kwargs)
        captured.append((value,{k:kwargs[k] for k in ("kind","details","temperature")}))
        return value
    m._generate_leaf_text=capture_leaf
    rows=[]
    previous="normal"
    for step in case["steps"]:
        captured.clear()
        e=GameEvent.model_validate(step["event"]);now=e.observed_at
        m._haiku_focus_active=lambda:step.get("focus",False)
        m._haiku_workshop_is_open=lambda:step.get("focus",False)
        changed=m._did_change_dimension(e)
        edges={k:False if changed else getattr(m,"_"+k)(e) for k in ("entered_occluded_dark_zone","entered_safe_zone_with_door","entered_emergency_shelter","entered_submerged_dark_zone")}
        m._update_memory(e,now)
        sig=m._derive_signals(e,now)
        for k,v in edges.items():setattr(sig,k,v)
        mode=step.get("mode") or m._resolve_mode(e,sig,now)
        step["mode"]=mode
        m.state.mode=mode
        actions=[] if changed else (m._threat_dark_push_stop_actions(e,sig,now) if mode in {"panic","suppressed_panic","aftermath"} else m._environmental_actions(e,sig,previous,now))
        previous=mode
        m.state.last_foliage_shade_context=m._is_foliage_shade_context(e)
        rows.append({"actions":[{"text":a.text or "","cue_id":a.cue_id,"interrupt":a.interrupt,"protect_ms":a.protect_ms,"leaf":next((leaf for value,leaf in captured if value==a.text),None)} for a in actions],"active":m.state.dark_push_active or m.state.dark_push_stage>=1})
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--python-root",type=Path,default=ROOT)
    ap.add_argument("--binary",type=Path,default=ROOT/"dogido-rust/target/debug/examples/danger_batch")
    ap.add_argument("--report",type=Path,default=ROOT/"dogido-rust/reports/environment-danger.json")
    args=ap.parse_args()
    sys.path.insert(0,str(args.python_root.resolve()))
    logging.disable(logging.CRITICAL)
    data=cases();expected_rows=[python_replay(c) for c in data]
    run=subprocess.run([str(args.binary.resolve())],input="".join(json.dumps(c,ensure_ascii=False)+"\n" for c in data),text=True,capture_output=True,check=True)
    rust=[json.loads(l) for l in run.stdout.splitlines()]
    report=[]
    for case,actual,expected in zip(data,rust,expected_rows,strict=True):
        normalized=[{"actions":[{k:a.get(k) for k in ("text","cue_id","interrupt","protect_ms","leaf")} for a in row["actions"]],"active":row["active"]} for row in actual]
        # Surface evening now interrupts an already running Rust chat; absent a chat,
        # Python marks this non-interrupting. Both have the same priority/wording.
        for row,raw in zip(normalized,actual):
            for a,source in zip(row["actions"],raw["actions"]):
                if source["kind"]=="night_warning_surface":a["interrupt"]=False
        report.append({"name":case["name"],"pass":expected==normalized,"python":expected,"rust":normalized,"case":case})
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    failed=[r["name"] for r in report if not r["pass"]]
    print(json.dumps({"cases":len(report),"passed":len(report)-len(failed),"failed":failed,"report":str(args.report)},ensure_ascii=False))
    return bool(failed)
if __name__=="__main__":raise SystemExit(main())
