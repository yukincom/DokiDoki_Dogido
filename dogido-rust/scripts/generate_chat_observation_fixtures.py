#!/usr/bin/env python3
"""Persistent canonical Python state traces; synthetic events only, no model/service.

The combat owner is not reimplemented: outcome_updates are captured at the canonical
kill-name update boundary. Full machine.process supplies all memory updates and
post-outcome removal. A fresh machine is created per TRACE, never per observation.
"""
from __future__ import annotations
import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import random
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BASE = datetime(2026, 9, 29, tzinfo=timezone.utc)


def event(ms=0, sequence=None, name="status_snapshot", **sections):
    value = {
        "schema_version": "2026-05-24", "adapter": "golden-synthetic", "sequence": sequence,
        "observed_at": (BASE + timedelta(milliseconds=ms)).isoformat(),
        "event": {"name": name, "source_kind": "system", "priority_hint": "background", "certainty": "high"},
        "player": {"dimension": "minecraft:overworld"},
        "world": {"biome": "plains", "time_phase": "day", "weather": "clear", "sky_visible": True},
        "combat": {"hostile_outcomes": []},
    }
    for key, item in sections.items():
        if isinstance(item, dict) and isinstance(value.get(key), dict): value[key].update(item)
        else: value[key] = item
    return value


def step(ms=0, *, read=False, **fields):
    return {"read_only": read, "event": event(ms, **fields)}


def visual(kind="zombie", entity="z1", direction="front", distance=4):
    return {"type": kind, "entity_id": entity, "distance": distance, "direction": {"horizontal": direction}}


def sound(kind="zombie", source="s1", direction="left", band="close", **extra):
    return {"label": kind, "source_id": source, "direction": {"horizontal": direction}, "distance_band": band, **extra}


def ambient(kind="cow", direction="right", band="far", **extra):
    return {"type": kind, "direction": {"horizontal": direction}, "distance_band": band, **extra}


def death(kind="zombie", entity="z1", outcome="player_kill", evidence="server_death_event"):
    return {"type": kind, "entity_id": entity, "outcome": outcome, "evidence": evidence}


def traces(mob_ids, block_ids):
    first = step(visual_threats=[visual()], passive_mobs=[{"type": "cow"}], auditory_threats=[sound()], ambient_sounds=[ambient("block:fire")])
    yield {"name": "retention-read-only-no-renewal", "steps": [first] + [step(t, read=True) for t in [-1000, 0, 9999.999, 10000, 10000.001, 10001, 11999.999, 12000, 12000.001, 12001, 19999.999, 20000, 20000.001, 20001, 59999.999, 60000, 60000.001, 60001]]}
    yield {"name": "custom-retention", "settings": {"player_chat_visual_retention_ms": 70000, "player_chat_hearing_retention_ms": 5, "player_chat_name_correction_retention_ms": 30000, "weather_sound_recent_ms": 8}, "steps": [first] + [step(t, read=True) for t in [5, 6, 30000, 30001, 60001, 70000, 70001]]}
    yield {"name": "future-out-of-order-and-prune", "steps": [first, step(40000, visual_threats=[visual("skeleton", "s")], passive_mobs=[{"type": "sheep"}], ambient_sounds=[ambient("pig")]), step(1000), step(-5000, read=True), step(41000), step(2000, read=True)]}
    yield {"name": "duplicates-stale-sequences", "steps": [step(0, sequence=1, visual_threats=[visual()]), step(15000, sequence=1, passive_mobs=[{"type": "pig"}]), step(2000, sequence=3, auditory_threats=[sound("skeleton")]), step(50000, sequence=2, visual_threats=[visual("witch")]), step(12001, read=True), step(3000, sequence=4), step(3100, sequence=4), step(23000, read=True)]}
    yield {"name": "partial-sounds-and-unknown", "steps": [first, step(1000, name="hostile_audio_detected", auditory_threats=[sound("unknown", sound_event="minecraft:entity.skeleton.ambient")]), step(1500, name="ambient_mob_detected", ambient_sounds=[ambient("weather:rain")]), step(2000, name="hostile_audio_detected"), step(3000, read=True), step(21001, read=True)]}
    yield {"name": "dimension-filter-not-buffer-clear", "steps": [first, step(1000, player={"dimension": "minecraft:the_nether"}), step(1500, read=True, player={"dimension": "minecraft:the_nether"}), step(2000, player={"dimension": None}), step(2500, player={"dimension": "minecraft:overworld"}), step(3000, player={"dimension": "overworld"}, passive_mobs=[{"type": "pig"}]), step(4000, read=True)]}
    yield {"name": "initial-non-overworld-future", "steps": [step(5000, player={"dimension": "the_nether"}, passive_mobs=[{"type": "pig"}]), step(0, player={"dimension": "the_end"}, passive_mobs=[{"type": "cow"}]), step(1000, read=True, player={"dimension": "the_end"})]}
    yield {"name": "passive-latest-four-tie-current-namespace", "steps": [step(0, passive_mobs=[{"type": x} for x in ["cow", "sheep", "pig", "cat", "wolf", "chicken"]]), step(1000, passive_mobs=[{"type": "cow"}, {"type": "minecraft:pig"}, {"type": " SHEEP "}]), step(2000, passive_mobs=[{"type": "minecraft:cow"}, {"type": " PIG "}]), step(3000, read=True)]}
    yield {"name": "buffer-caps-ties-replacement", "steps": [step(0, visual_threats=[visual("zombie", f"z{i}") for i in range(14)], auditory_threats=[sound("skeleton", f"s{i}", direction=["left", "right"][i % 2]) for i in range(14)]), step(1000, visual_threats=[visual("creeper", "z8"), visual("witch", "new")], auditory_threats=[sound("zombie", "s8"), sound("witch", "new")]), step(2000, read=True)]}
    yield {"name": "current-eight-summary-lines", "steps": [step(0, auditory_threats=[sound(x, x, d) for x,d in zip(["zombie","skeleton","witch","creeper","spider"],["left","right","back","front","left"])], ambient_sounds=[ambient(x) for x in ["cow","pig","sheep","weather:rain","weather:thunder"]]), step(1, read=True)]}
    yield {"name": "weather-actual-packet-only", "steps": [step(0, world={"weather": "thunder"}), step(1, world={"rain_sound_recent_ms": 4000, "thunder_sound_recent_ms": 4001}), step(2, world={"rain_sound_recent_ms": 0, "thunder_sound_recent_ms": 4000}, ambient_sounds=[ambient(" WEATHER:RAIN ")]), step(3, read=True), step(20003, read=True)]}
    for kind, outcome, name, evidence in [("zombie","player_kill","hostile_defeated","server_death_event"), ("skeleton","other_death","hostile_defeated","client_death_state"), ("creeper","creeper_detonation","creeper_detonated","explosion_packet")]:
        for entity in ["dead", None, " dead "]:
            yield {"name": f"death-{kind}-{entity}", "steps": [step(0, visual_threats=[visual(kind,"dead"), visual(kind,"live")], auditory_threats=[sound(kind,"dead"),sound(kind,"live")], ambient_sounds=[ambient(kind)]), step(1000,name=name,combat={"hostile_outcomes":[death(kind,entity,outcome,evidence)]}), step(5000,name=name,combat={"hostile_outcomes":[death(kind,entity,outcome,evidence)]}), step(11001,read=True), step(12000,name="combat_ended",combat={"hostile_outcomes":[death(kind,entity,outcome,evidence)]}), step(13000,read=True)]}
    yield {"name": "legacy-disappearance-name-only", "steps": [step(0, visual_threats=[visual()],combat={"hostile_outcomes":None,"combat_active_hint":True}), step(1000,name="hostile_audio_detected", combat={"hostile_outcomes":None,"combat_active_hint":True}), step(2000,read=True), step(11001,read=True)]}
    yield {"name": "modern-empty-outcomes-no-death", "steps": [step(0,visual_threats=[visual()],combat={"combat_active_hint":True}),step(1000,combat={"combat_active_hint":True}),step(12001,read=True)]}
    for distances in [[10,9.5,9], [10,10.5,11], [10,9.8,9], [10,9.75,9.5], [10,9.75,9], [10,9,10,9,10,9], [10,11,10,9,8,7,6]]:
        yield {"name": "home-"+str(distances), "steps": [step(i*1000,world={"respawn_point_set":True,"respawn_distance":d}) for i,d in enumerate(distances)] + [step(30000,read=True,world={"respawn_point_set":True,"respawn_distance":5}),step(30000,world={"respawn_point_set":True,"respawn_distance":5})]}
    yield {"name": "home-replace-future-reset-and-bed", "settings":{"home_bed_prompt_distance":3}, "steps":[step(t,world={"respawn_point_set":True,"respawn_distance":d,"nearby_bed_count":b},player={"dimension":dim}) for t,d,b,dim in [(0,10,0,"overworld"),(1000,9.5,0,"overworld"),(1000,9,0,"overworld"),(2000,8,0,"overworld"),(1500,7,0,"overworld"),(11500,6,0,"overworld"),(21500.001,5,0,"overworld"),(22000,3,1,"the_nether"),(22500,4,1,"the_nether"),(23000,3,1,None)]]}
    for id in mob_ids:
        yield {"name":"catalog-sound-"+id,"steps":[step(0,auditory_threats=[sound("unknown",sound_event=f"minecraft:entity.{id}.ambient")],ambient_sounds=[ambient(id)]),step(1,read=True)]}
    for start in range(0,len(block_ids),4):
        ids = block_ids[start:start+4]
        yield {"name":"block-sounds-"+str(start),"steps":[step(0,ambient_sounds=[ambient("block:"+id) for id in ids]),step(1,read=True)]}
    for raw in ["environment:"+s for s in ["cave","underwater","basalt_deltas","crimson_forest","nether_wastes","soul_sand_valley","warped_forest","unknown"]] + ["block:"+s for s in ["fire","lava","water","nether_portal","bubble_column","trial_spawner","pointed_dripstone","wooden_door","wooden_trapdoor","wooden_button","wooden_pressure_plate","minecraft:fire","unknown",""]] + ["mystery", " minecraft:zombie ","MINECRAFT:zombie","\u001czombie\u001f"]:
        yield {"name":"sound-resolution-"+raw,"steps":[step(0,ambient_sounds=[ambient(raw,sound_event="entity.zombie.ambient")]),step(1,read=True)]}
    yield {"name":"death-mixed-id-and-type-removal", "steps":[step(0,visual_threats=[visual("zombie","dead"),visual("skeleton","live")],auditory_threats=[sound("zombie","dead"),sound("skeleton","live")]),step(1000,name="hostile_defeated",combat={"hostile_outcomes":[death("zombie","dead"),death("skeleton",None,"explosion_death")]}),step(2000,read=True)]}
    yield {"name":"combat-ended-without-immediate-death", "steps":[step(0,visual_threats=[visual()]),step(1000,name="combat_ended",combat={"hostile_outcomes":[death()]}),step(11000,read=True),step(11001,read=True)]}
    yield {"name":"death-replay-after-player-death", "steps":[step(0,name="hostile_defeated",combat={"hostile_outcomes":[death()]}),step(1000,name="player_died"),step(5000,name="hostile_defeated",combat={"hostile_outcomes":[death()]}),step(10001,read=True),step(15001,read=True)]}
    yield {"name":"name-longer-than-visual-window", "settings":{"player_chat_name_correction_retention_ms":30000}, "steps":[first,step(15000,read=True),step(15000),step(15001,read=True)]}
    for aware in [False,True]:
        values=[step(0,visual_threats=[visual()],passive_mobs=[{"type":"cow"}]),step(12000.999,read=True),step(12001,read=True)]
        for item in values:
            t=datetime.fromisoformat(item["event"]["observed_at"])
            item["event"]["observed_at"]=(t.astimezone(timezone(timedelta(hours=9))) if aware else t.replace(tzinfo=None)).isoformat()
        yield {"name":"time-offset-aware-"+str(aware),"steps":values}
    rng = random.Random(20260929)
    steps = []
    for i in range(200):
        t = i*400 + rng.choice([0,0,0,-1000,30000])
        fields = {"visual_threats":[visual(rng.choice(["zombie","skeleton","witch"]),f"id{rng.randrange(5)}",rng.choice([None,"front","left"])) for _ in range(rng.randrange(3))], "passive_mobs":[{"type":rng.choice(["cow","pig","sheep","minecraft:cat"])} for _ in range(rng.randrange(3))], "ambient_sounds":[ambient(rng.choice(["cow","weather:rain","block:fire","unknown"]),rng.choice([None,"front","right"])) for _ in range(rng.randrange(3))]}
        steps.append(step(t, **fields))
        if i%7==0: steps.append(step(t+10000.999,read=True))
    yield {"name":"deterministic-mixed-sequence","steps":steps}


def micros(value):
    epoch = datetime(1970,1,1,tzinfo=value.tzinfo)
    if value.tzinfo is not None: epoch=datetime(1970,1,1,tzinfo=timezone.utc)
    delta=value-epoch
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def snapshot(machine, e):
    s=machine.state
    now=e.observed_at
    settings=machine.settings
    visual=[asdict(m) for m in s.recent_visual_memos if machine._recent_ms(now,m.seen_at)<=settings.player_chat_visual_retention_ms]
    hearing=[asdict(m) for m in s.recent_hearing_memos if machine._recent_ms(now,m.heard_at)<=settings.player_chat_hearing_retention_ms]
    for m in visual: m["seen_at_us"]=micros(m.pop("seen_at"))
    for m in hearing: m["heard_at_us"]=micros(m.pop("heard_at"))
    return {
        "current":{"visual_types":machine._merge_unique_types([v.type for v in e.visual_threats]),"passive_types":machine._player_chat_observed_passive_types(e),"hearing_types":machine._player_chat_hearing_mob_types(e,include_recent=False)},
        "recent":{"visual_memos":visual,"visual_types":machine._player_chat_recent_visual_types(e),"visual_summary":machine._player_chat_recent_visual_summary_line(e),"hearing_memos":hearing,"passive_sightings":[{"mob_type":k,"seconds_ago":v} for k,v in machine._player_chat_recent_passive_sightings(e).items()]},
        "name_context":{"types":machine._player_chat_recent_name_context_types(e)},
        "hearing":{"types":machine._player_chat_hearing_mob_types(e),"named_mobs":machine._player_chat_hearing_named_mobs(e),"source_labels":machine._player_chat_hearing_source_labels(e),"summary":machine._player_chat_hearing_summary(e)},
        "home":{"progress":machine._player_chat_home_progress(e),"samples":[{"at_us":micros(at),"distance":d} for at,d in s.recent_respawn_distance_samples]},
    }


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--source-root",type=Path,default=ROOT); parser.add_argument("--output-dir",type=Path,default=ROOT/"dogido-rust"/"fixtures"); args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    sys.path.insert(0,str(args.source_root))
    from dogido_server.config import Settings
    from dogido_server.entry_catalog import all_mob_entries, block_entries
    from dogido_server.models import GameEvent
    from dogido_server.service import SessionInfo
    from dogido_server.state_machine.machine import DogidoStateMachine
    from dogido_server.state_machine.constants import HOSTILE_LABELS, MOB_LABELS
    class RecordingMachine(DogidoStateMachine):
        def _update_dialogue_kill_tracking(self,event,now):
            super()._update_dialogue_kill_tracking(event,now)
            names=[k for k,t in self.state.recent_kill_seen_at_by_type.items() if t==now]
            self.fixture_outcomes={"confirmed_types":names if event.combat.hostile_outcomes is not None else [],"legacy_disappeared_types":names if event.combat.hostile_outcomes is None else []}
    labels={"mobs":{k:str(v.get("label") or "") for k,v in all_mob_entries().items()},"mob_fallback":MOB_LABELS,"hostiles":HOSTILE_LABELS,"blocks":{k:str(v.get("label") or v.get("japanese") or "") for k,v in block_entries().items()}}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"chat-observation-labels.json").write_text(json.dumps(labels,ensure_ascii=False,indent=2)+"\n")
    count=0; total_steps=0
    with (args.output_dir/"chat-observation.jsonl").open("w") as f:
        for trace in traces(list(labels["mobs"]),list(labels["blocks"])):
            defaults={k:Settings.model_fields[k].default for k in ["player_chat_visual_retention_ms","player_chat_hearing_retention_ms","player_chat_name_correction_retention_ms","weather_sound_recent_ms","home_bed_prompt_distance"]}
            settings=Settings(_env_file=None,decision_policy="legacy",**(defaults|trace.get("settings",{})))
            machine=RecordingMachine(settings,llm=None)
            admission=SimpleNamespace(last_sequence=None,seen_sequences=deque(maxlen=2048),seen_sequence_set=set())
            rows=[]
            for index,step_data in enumerate(trace["steps"]):
                e=GameEvent.model_validate(step_data["event"])
                accepted=not step_data["read_only"]
                if accepted and e.sequence is not None:
                    accepted=not SessionInfo.is_stale_sequence(admission,e.sequence) and not SessionInfo.remember_sequence(admission,e.sequence)
                machine.fixture_outcomes={"confirmed_types":[],"legacy_disappeared_types":[]}
                if accepted: machine.process(e)
                rows.append({**step_data,"accepted":accepted,"outcome_updates":machine.fixture_outcomes,"expected":snapshot(machine,e)})
            json.dump({"name":trace["name"],"settings":trace.get("settings",{}),"steps":rows},f,ensure_ascii=False,separators=(",",":")); f.write("\n")
            count+=1; total_steps+=len(rows)
    sources=["dogido_server/state_machine/machine.py","dogido_server/state_machine/mixins/narration.py","dogido_server/state_machine/mixins/state_updates.py","dogido_server/state_machine/mixins/common.py","dogido_server/state_machine/mixins/inventory.py","dogido_server/config.py","dogido_server/service.py","dogido_server/state_machine/mixins/world_analysis.py","dogido_server/state_machine/constants.py","dogido_server/entry_catalog.py"]
    meta={"traces":count,"steps":total_steps,"source_sha256":{p:hashlib.sha256((args.source_root/p).read_bytes()).hexdigest() for p in sources},"oracle":"persistent DogidoStateMachine.process; canonical service sequence admission; no model"}
    (args.output_dir/"chat-observation-meta.json").write_text(json.dumps(meta,indent=2)+"\n")
    print(json.dumps({"traces":count,"steps":total_steps}))
if __name__=="__main__": main()
