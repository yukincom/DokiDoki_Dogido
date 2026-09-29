#!/usr/bin/env python3
"""Canonical persistent death-name hook, with explicit replay-suppression deviations.
No model or service. Native combat modes/speech are not made equivalent by this oracle:
we capture the Python mode at its update boundary and test the pure hook with that mode.
"""
import argparse
from collections import Counter
import hashlib
import json
import logging
from pathlib import Path
import sys
from generate_chat_observation_fixtures import event, visual, death
ROOT=Path(__file__).resolve().parents[2]

def step(ms,name="status_snapshot",mode=None,**fields):
    return {"event":event(ms,name=name,**fields),"force_mode":mode}

def traces():
    for result,name in [("player_kill","hostile_defeated"),("explosion_death","hostile_defeated"),("other_death","hostile_defeated"),("creeper_detonation","creeper_detonated")]:
        for ident in ["id",None," id "]:
            outcome=death("creeper" if result=="creeper_detonation" else "minecraft:ZOMBIE",ident,result,"explosion_packet" if result=="creeper_detonation" else "server_death_event")
            yield {"name":f"fresh-replay-{result}-{ident}","steps":[step(0,name,combat={"hostile_outcomes":[outcome]}),step(5000,name,combat={"hostile_outcomes":[outcome]}),step(6000,"combat_ended",combat={"hostile_outcomes":[outcome]}),step(15000,name,combat={"hostile_outcomes":[outcome]}),step(16000,"player_died"),step(17000,name,combat={"hostile_outcomes":[outcome]})]}
    for name in ["hostile_defeated","creeper_detonated","combat_ended","status_snapshot"]:
        mixed=[death("zombie","z"),death("creeper","c","creeper_detonation","explosion_packet"),death("witch","w","other_death"),death("skeleton","s","explosion_death")]
        yield {"name":"mixed-"+name,"steps":[step(0,name,combat={"hostile_outcomes":mixed}),step(1000,"hostile_defeated",combat={"hostile_outcomes":mixed}),step(2000,"combat_ended",combat={"hostile_outcomes":mixed}),step(3000,"combat_ended",combat={"hostile_outcomes":mixed})]}
    yield {"name":"same-id-two-kinds-ordered","steps":[step(0,"hostile_defeated",combat={"hostile_outcomes":[death("witch","id"),death("zombie","id"),death("witch","other")]}),step(1000,"combat_ended",combat={"hostile_outcomes":[death("skeleton","id"),death("cow","new")]})]}
    yield {"name":"name-update-before-aftermath-safe","steps":[step(0,visual_threats=[visual()]),step(1000,"combat_ended",visual_threats=[visual("skeleton","live")],combat={"hostile_outcomes":[death()],"hostiles_within_10":1}),step(2000,"combat_ended",combat={"hostile_outcomes":[death()]})]}
    for mode in ["normal","alert","panic","suppressed_panic","aftermath"]:
        for extra in [{},{"combat_active_hint":True},{"recent_damage_ms":200},{"recent_damage_ms":201}]:
            yield {"name":f"legacy-{mode}-{extra}","settings":{"recent_damage_window_ms":200},"steps":[step(0,mode="normal",visual_threats=[visual("ZOMBIE","one"),visual("skeleton","two"),visual("creeper",None)],combat={"hostile_outcomes":None}),step(1000,mode=mode,visual_threats=[visual("skeleton","two")],combat={"hostile_outcomes":None,**extra}),step(2000,mode=mode,combat={"hostile_outcomes":None,**extra}),step(3000,mode=mode,combat={"hostile_outcomes":None,**extra})]}
    yield {"name":"modern-empty-no-legacy-and-next-update","steps":[step(0,visual_threats=[visual()],combat={"hostile_outcomes":None,"combat_active_hint":True}),step(1000,combat={"hostile_outcomes":[],"combat_active_hint":True}),step(2000,combat={"hostile_outcomes":None,"combat_active_hint":True})]}
    yield {"name":"partial-legacy-uses-raw-arrays","steps":[step(0,visual_threats=[visual()],combat={"hostile_outcomes":None}),step(1000,"hostile_audio_detected",mode="alert",combat={"hostile_outcomes":None}),step(2000,"hostile_audio_detected",mode="alert",combat={"hostile_outcomes":None})]}
    yield {"name":"legacy-combat-ended-flushes-current-ids","steps":[step(0,visual_threats=[visual()],combat={"hostile_outcomes":None}),step(1000,"combat_ended",visual_threats=[visual("skeleton","s")],combat={"hostile_outcomes":None,"combat_active_hint":True}),step(2000,mode="alert",combat={"hostile_outcomes":None})]}
    for dims in [["overworld","the_nether"],["overworld",None,"the_nether"],["overworld","minecraft:overworld"],[None,"the_nether"]]:
        steps=[step(0,player={"dimension":dims[0]},visual_threats=[visual()],combat={"hostile_outcomes":None})]
        steps += [step((i+1)*1000,mode="alert",player={"dimension":d},combat={"hostile_outcomes":None}) for i,d in enumerate(dims[1:])]
        yield {"name":"legacy-dimension-"+str(dims),"steps":steps}
    yield {"name":"global-replay-across-dimension","steps":[step(0,"hostile_defeated",combat={"hostile_outcomes":[death()]}),step(1000,player={"dimension":"the_nether"}),step(2000,"hostile_defeated",player={"dimension":"the_nether"},combat={"hostile_outcomes":[death()]}),step(3000,"hostile_defeated",player={"dimension":"overworld"},combat={"hostile_outcomes":[death()]})]}
    yield {"name":"legacy-reobservation-is-new-disappearance","steps":[step(0,visual_threats=[visual()],combat={"hostile_outcomes":None}),step(1000,mode="alert",combat={"hostile_outcomes":None}),step(2000,visual_threats=[visual()],combat={"hostile_outcomes":None}),step(3000,mode="alert",combat={"hostile_outcomes":None})]}

def main():
    p=argparse.ArgumentParser();p.add_argument("--source-root",type=Path,default=ROOT);p.add_argument("--output-dir",type=Path,default=ROOT/"dogido-rust/fixtures");a=p.parse_args()
    logging.disable(logging.CRITICAL);sys.path.insert(0,str(a.source_root))
    from dogido_server.models import GameEvent
    from dogido_server.config import Settings
    from dogido_server.state_machine.machine import DogidoStateMachine
    def normalized(v):return str(v or "").removeprefix("minecraft:").strip().lower()
    def unique(xs):return list(dict.fromkeys(x for x in xs if x))
    class Oracle(DogidoStateMachine):
        def _update_dialogue_kill_tracking(self,e,now):
            before=dict(self.state.recent_kill_seen_at_by_type); tracked=list(self.state.tracked_hostile_entities.values());self.captured_mode=self.state.mode
            super()._update_dialogue_kill_tracking(e,now)
            changed={k for k,v in self.state.recent_kill_seen_at_by_type.items() if before.get(k)!=v}
            explicit=e.combat.hostile_outcomes is not None
            ordered=unique(normalized(o.type) for o in e.combat.hostile_outcomes or []) if explicit else unique(normalized(v) for v in tracked)
            updated=[k for k in ordered if k in changed]
            self.captured={"confirmed_types":updated if explicit else [],"legacy_disappeared_types":[] if explicit else updated}
    a.output_dir.mkdir(parents=True,exist_ok=True);counts=Counter();n=0;steps_count=0
    with (a.output_dir/"combat-name-updates.jsonl").open("w") as f:
        for trace in traces():
            settings=Settings(_env_file=None,decision_policy="legacy",**trace.get("settings",{}));machine=Oracle(settings,llm=None);seen=set();rows=[]
            for item in trace["steps"]:
                e=GameEvent.model_validate(item["event"])
                if item["force_mode"] is not None: machine.state.mode=item["force_mode"]
                machine.process(e);raw=machine.captured
                expected=dict(raw)
                if e.event.name.value in {"hostile_defeated","creeper_detonated","combat_ended"} and e.combat.hostile_outcomes is not None:
                    fresh=[o for o in e.combat.hostile_outcomes if machine._hostile_outcome_key(o) not in seen]
                    expected["confirmed_types"]=unique(normalized(o.type) for o in fresh)
                    seen.update(machine._hostile_outcome_key(o) for o in fresh)
                deviation="session_replay_suppression" if expected!=raw else ""
                if deviation: assert not set(expected["confirmed_types"])-set(raw["confirmed_types"]),(trace["name"],raw,expected)
                counts[deviation or "canonical_equal"]+=1
                rows.append({"event":item["event"],"previous_mode":machine.captured_mode,"expected_python":raw,"expected":expected,"deviation":deviation});steps_count+=1
            json.dump({"name":trace["name"],"settings":trace.get("settings",{}),"steps":rows},f,ensure_ascii=False,separators=(",",":"));f.write("\n");n+=1
    sources=["dogido_server/state_machine/machine.py","dogido_server/state_machine/mixins/state_updates.py","dogido_server/state_machine/mixins/common.py","dogido_server/state_machine/mixins/narration.py"]
    meta={"traces":n,"steps":steps_count,"comparison":dict(counts),"source_sha256":{s:hashlib.sha256((a.source_root/s).read_bytes()).hexdigest() for s in sources}}
    (a.output_dir/"combat-name-updates-meta.json").write_text(json.dumps(meta,indent=2)+"\n");print(json.dumps(meta))
if __name__=="__main__":main()
