#!/usr/bin/env python3
"""移植済み視認警告の系列をPython状態機械と比較。LLM・音声・サーバーなし。"""
from datetime import datetime, timedelta, timezone
import itertools
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dogido_server.config import Settings
from dogido_server.models import GameEvent
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.constants import HOSTILE_LABELS,RANGED_HOSTILES,HOSTILE_EFFECTIVE_RANGE

def event(ms, *, distance=8.2, direction="left", kind="zombie", entity="z1", fuse=None, approaching=False, biome="plains", damage=None):
    return {"schema_version":"2026-05-24","adapter":"fixture","observed_at":(datetime(2026,9,24,tzinfo=timezone.utc)+timedelta(milliseconds=ms)).isoformat(),
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"name":"試験"},"world":{"time_phase":"night","biome":biome,"sky_visible":True,"danger_darkness_score":0.0},
        "combat":{"recent_damage_ms":damage},"visual_threats":[{"type":kind,"entity_id":entity,"distance":distance,
            "direction":{"horizontal":direction,"vertical":"same"},"approaching":approaching,"fuse_active":fuse}]}

def series(items):
    return {"steps":[{"ms":ms,"event":event(ms,**kw)} for ms,kw in items]}

def main():
    logging.disable(logging.CRITICAL)
    cat=json.loads((ROOT/"dogido-rust/src/threat_catalog.json").read_text())
    assert cat=={"labels":HOSTILE_LABELS,"ranged":sorted(RANGED_HOSTILES),"effective_range":HOSTILE_EFFECTIVE_RANGE},"catalog drift: re-export and review"
    cases=[]
    for kind,direction,distance,approaching in itertools.product(
        ["zombie","zombie_villager","skeleton","spider","cave_spider","husk","stray","creeper","charged_creeper"],
        [None,"front","left","back_left","back"], [3.0,3.01,6.0,6.01,7.0,7.01,8.2,None], [False,True]):
        cases.append(series([(0,dict(kind=kind,direction=direction,distance=distance,approaching=approaching))]))
    cases += [series([(0,{}),(100,{}),(59_999,{}),(60_000,{})]),
              series([(0,{}),(29_999,{"entity":"z2"}),(30_000,{"entity":"z2"})]),
              series([(0,{"distance":3}),(100,{"distance":3}),(200,{"distance":3})]),
              series([(0,{"distance":8}),(100,{"distance":3})]),
              series([(ms,{"kind":"creeper","distance":3.5,"fuse":f}) for ms,f in [(0,False),(100,True),(200,True),(300,False),(400,True),(1700,False),(1800,True)]]),
              series([(0,{"biome":"deep_dark","distance":4})]),
              series([(0,{"distance":8.2,"damage":3000})]),
              series([(0,{"distance":8.2,"damage":3001})])]
    # 長い連続視認の停滞警告は未移植。個体cooldownは消失後の再観測で比較する。
    cases[-8]["steps"][1]["event"]["visual_threats"]=[]
    # 群れの初見・混合・包囲・体数境界、並び替え、単独からの増加。
    group_cases=[]
    def group(ms, types, distances=None, directions=None, **kw):
        e=event(ms,**kw)
        e['player']['dimension']='minecraft:overworld'
        e['visual_threats']=[event(ms,kind=t,entity=f'{t}{i}',distance=(distances or [8.2]*len(types))[i],direction=(directions or ['left']*len(types))[i])['visual_threats'][0] for i,t in enumerate(types)]
        return {'ms':ms,'event':e}
    for types in ([t]*n for t in ['zombie','skeleton','spider','creeper'] for n in [2,3,4,8,9,12]):
        group_cases.append({'steps':[group(0,types)]})
    for types in [['zombie','zombie','skeleton'],['zombie','spider'],['zombie','creeper','skeleton'],['zombie']*2+['skeleton','creeper']]:
        for distance in [3.01,8.2,10,10.01,18,None]:
            for direction in ['front','back_left',None]:
                group_cases.append({'steps':[group(0,types,[distance]*len(types),[direction]*len(types))]})
    group_cases += [
        {'steps':[group(ms,['zombie']*2) for ms in [0,100,29999,30000]]},
        {'steps':[group(0,['zombie']), group(7000,['zombie']*4), group(7100,['zombie']*4)]},
        {'steps':[group(0,['zombie']), group(7000,['zombie']*2), group(7100,['zombie']*2)]},
        {'steps':[group(0,['zombie']*2,[8,8]), group(2000,['zombie']*3,[8,8,2]),group(2100,['zombie']*3,[8,8,2])]},
        {'steps':[group(0,['zombie']*2+['skeleton','creeper'],[2,6,9.5,6.2],['front','left','right','back_right'])]},
        {'steps':[group(0,['zombie','skeleton'],[8,8],biome='deep_dark')]},
    ]
    for kinds in [['zombie']*2,['zombie','skeleton']]:
        group_cases.append({'steps':[group(0,kinds,[18,18])]})
    # 導火は群れより先。
    c=group(0,['zombie','creeper','zombie']); c['event']['visual_threats'][1]['fuse_active']=True
    group_cases.append({'steps':[c]})
    for size in [1,2]:
        group_cases.append({'steps':[group(0,['zombie']*size),group(1000,[]),group(2000,['zombie']*2)]})
    group_cases.append({'steps':[group(0,['zombie','skeleton'],[6,18])]})
    for micros,sequence in [(0,0),(123,4),(1234,7),(5678,8)]:
        c=group(micros,['zombie']*9);c['event']['sequence']=sequence
        group_cases.append({'steps':[c]})
    cases += group_cases
    settings=Settings(_env_file=None,llm_enabled=False,audio_enabled=False,decision_policy="legacy")
    shared={k:getattr(settings,k) for k in ("panic_distance","rear_warning_distance","recent_damage_window_ms","hostile_comment_cooldown_ms","multi_hostile_comment_cooldown_ms","panic_scream_cooldown_ms")}
    expected=[]
    for case in cases:
        case["settings"]=shared
        machine=DogidoStateMachine(settings)
        expected.append([[{"text":a.text,"cue_id":a.cue_id,"cue_sequence":list(a.cue_sequence or ())} for a in machine.process(GameEvent.model_validate(s["event"])).actions
                          if a.layer in {"callout","panic_cue"}] for s in case["steps"]])
    result=subprocess.run([str(ROOT/"dogido-rust/target/debug/examples/check_threats")],input="".join(json.dumps(c,ensure_ascii=False)+"\n" for c in cases),text=True,capture_output=True,check=True)
    actual=[json.loads(line) for line in result.stdout.splitlines()]
    failures=[{"index":i,"case":cases[i],"python":p,"rust":r} for i,(p,r) in enumerate(zip(expected,actual,strict=True)) if p!=r]
    report={"cases":len(cases),"matched":len(cases)-len(failures),"failures":failures,"scope":"ordinary single/group visual / cue / fuse; not full combat parity"}
    path=ROOT/"dogido-rust/reports/threat-parity.json"
    path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    if failures:
        print(json.dumps(failures[:4],ensure_ascii=False,indent=2)); raise SystemExit(f"FAIL {len(failures)}/{len(cases)}; {path}")
    print(f"PASS {len(cases)} Python/Rust warning sequences (no model/audio/server)")

if __name__=="__main__":main()
