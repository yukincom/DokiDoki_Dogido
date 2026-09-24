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
    settings=Settings(_env_file=None,llm_enabled=False,audio_enabled=False,decision_policy="legacy")
    shared={k:getattr(settings,k) for k in ("panic_distance","rear_warning_distance","recent_damage_window_ms","hostile_comment_cooldown_ms","multi_hostile_comment_cooldown_ms","panic_scream_cooldown_ms")}
    expected=[]
    for case in cases:
        case["settings"]=shared
        machine=DogidoStateMachine(settings)
        expected.append([[{"text":a.text,"cue_id":a.cue_id} for a in machine.process(GameEvent.model_validate(s["event"])).actions
                          if a.layer in {"callout","panic_cue"}] for s in case["steps"]])
    result=subprocess.run([str(ROOT/"dogido-rust/target/debug/examples/check_threats")],input="".join(json.dumps(c,ensure_ascii=False)+"\n" for c in cases),text=True,capture_output=True,check=True)
    actual=[json.loads(line) for line in result.stdout.splitlines()]
    failures=[{"index":i,"case":cases[i],"python":p,"rust":r} for i,(p,r) in enumerate(zip(expected,actual,strict=True)) if p!=r]
    report={"cases":len(cases),"matched":len(cases)-len(failures),"failures":failures,"scope":"single visual / cue / fuse; not full combat parity"}
    path=ROOT/"dogido-rust/reports/threat-parity.json"
    path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    if failures:
        print(json.dumps(failures[:4],ensure_ascii=False,indent=2)); raise SystemExit(f"FAIL {len(failures)}/{len(cases)}; {path}")
    print(f"PASS {len(cases)} Python/Rust warning sequences (no model/audio/server)")

if __name__=="__main__":main()
