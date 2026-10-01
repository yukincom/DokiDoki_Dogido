#!/usr/bin/env python3
"""Capture pure environment/precipitation results from Python; no runtime or model.

Defaults are repository-relative after integration. --source-root reads an independent
canonical checkout; --output-dir can keep all generated files in an isolated stage.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from dataclasses import asdict
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]


def event(**sections):
    value = {
        "schema_version": "2026-05-24", "adapter": "golden-synthetic", "sequence": 1,
        "observed_at": "2026-09-29T00:00:00+00:00",
        "event": {"name": "status_snapshot", "source_kind": "system", "priority_hint": "background", "certainty": "high"},
        "player": {"position": {"y": 24}, "held_item": "minecraft:stone_pickaxe"},
        "world": {"biome": "plains", "sky_visible": False, "weather": "rain", "overhead_cover_type": "stone", "depth_below_surface": 12},
    }
    for key, fields in sections.items():
        if key in ("player", "world"):
            value[key].update(fields)
        else:
            value[key] = fields
    return value


def environment_cases():
    # Unknown is different from false. Cave-biome spelling and prefix/trim order matter.
    biomes = [None, "", "plains", "deep_dark", "lush_caves", "minecraft:dripstone_caves", "minecraft: DEEP_DARK ",
              " MINECRAFT:DEEP_DARK ", "MINECRAFT:deep_dark", "minecraft:minecraft:deep_dark", "\x1cdeep_dark\x1f", " CAVES "]
    breaks = [{"name": "stone", "material": "stone", "age_ms": 1000}] * 2
    for sky, submerged, biome in itertools.product([None, False, True], [None, False, True], biomes):
        yield event(world={"sky_visible":sky, "is_submerged":submerged, "biome":biome}, recent_block_breaks=breaks)
    break_sets = [
        [], [{"name":"stone","material":"stone","age_ms":10_000}],
        [{"name":"stone","material":"stone","age_ms":10_001}],
        [{"name":"dirt","material":"earth","age_ms":0}],
        [{"name":"iron_ore","material":"ore","age_ms":0}],
        [{"name":"oak_log","material":"other","age_ms":0}],
        [{"name":"stone","material":"stone","age_ms":10_000}] * 2,
        [{"name":"stone","material":"stone","age_ms":10_000}, {"name":"stone","material":"stone","age_ms":10_001}],
    ]
    drop_sets = [[], *[[{"name":"cobblestone","count":64,"mining_related":True,"age_ms":age}] for age in [None,0,15_000,15_001]],
                 [{"name":"cobblestone","count":64,"mining_related":False,"age_ms":0}],
                 [{"name":"cobblestone","count":1,"mining_related":True,"age_ms":0}]*2]
    for held, active, broken, drops in itertools.product(
            [None,"minecraft:stone_pickaxe","iron_shovel","diamond_axe","MINECRAFT:IRON_PICKAXE","iron_pickaxe "],
            [None,False,True], break_sets,drop_sets):
        yield event(player={"held_item":held,"block_breaking_active":active}, recent_block_breaks=broken,dropped_items=drops,
                    inventory={"minecraft:iron_ore":64})
    interiors = [{}, {"safe_zone_with_door":True}, {"safe_zone_with_door":False}, {"nearby_door_count":1},
                 {"nearby_bed_count":1}, {"nearby_window_present":True}, {"nearby_window_present":False},
                 {"nearby_door_count":-1,"nearby_bed_count":-1}, {"respawn_point_set":True,"respawn_distance":0}]
    for cover, depth, y, interior in itertools.product(
            [None,"stone","earth","ore","solid","wood","foliage","fluid","\x1cSTONE\u3000"],
            [None,0,5,6], [None,48,48.0001],interiors):
        yield event(player={"position":{"y":y}},world={"overhead_cover_type":cover,"depth_below_surface":depth,**interior})
    # Real break evidence wins before the stationary interior check; inventory is never evidence.
    for interior in interiors:
        yield event(world=interior,recent_block_breaks=breaks)
    rng = random.Random(591)
    for _ in range(180):
        yield event(player={"position":{"y":rng.choice([None,-64,48,49,160])},"held_item":rng.choice([None,"diamond_pickaxe","stick"])},
                    world={"sky_visible":rng.choice([None,False,True]),"is_submerged":rng.choice([None,False,True]),
                           "biome":rng.choice(biomes),"depth_below_surface":rng.choice([None,0,5,6,12]),
                           "overhead_cover_type":rng.choice([None,"stone","wood","fluid"]),**rng.choice(interiors)},
                    recent_block_breaks=rng.choice(break_sets),dropped_items=rng.choice(drop_sets))


def precipitation_input(**kwargs):
    return {"current_y":64,"biome_temperature":0.25,"snow_start_y":153,"biome_group_id":"cold",
            "biome_downfall":0.8,"weather":"clear","dimension":"minecraft:overworld","nearby_block_names":[],**kwargs}


def precipitation_cases():
    for y, temperature, threshold, weather in itertools.product(
            [None,-1.5,-0.5,0.5,1.5,64,152.49,152.5,152.5000001,153.5,320],
            [None,-0.5,0.149999999,0.15,0.8], [None,0,153], ["clear","rain","thunder","unknown"]):
        yield precipitation_input(current_y=y,biome_temperature=temperature,snow_start_y=threshold,weather=weather)
    for dimension,group,downfall,weather,blocks in itertools.product(
            [None,"minecraft:overworld","minecraft:the_nether","the_end"], ["", "cold","dry"],
            [None,-1.0,0.0,0.01], ["clear","rain","thunder","unknown"], [[],["minecraft:snow"]]):
        yield precipitation_input(current_y=None,biome_temperature=None,dimension=dimension,biome_group_id=group,
                                  biome_downfall=downfall,weather=weather,nearby_block_names=blocks)
    for weather,temperature,blocks in itertools.product(
            ["clear","rain","thunder","snow","RAIN"," thunder "], [None,-0.5,0.15],
            [[],["snow"],["snow_block"],["powder_snow"],["minecraft:snow"],["MINECRAFT:snow"],["minecraft:SNOW"],
             [" snow "],["minecraft:minecraft:snow"],["snowball"],["snow","snow_block"]]):
        yield precipitation_input(weather=weather,biome_temperature=temperature,snow_start_y=None,nearby_block_names=blocks)
    for field,values in {"dimension":["MINECRAFT:the_nether","minecraft:THE_NETHER"," the_nether ","minecraft:minecraft:the_nether"],
                         "biome_group_id":["DRY"," dry ","Dry"],"weather":["THUNDER"," rain ","", "sleet"]}.items():
        for value in values:
            yield precipitation_input(weather="thunder",**{field:value}) if field != "weather" else precipitation_input(weather=value)
    for y, threshold in itertools.product([-153.5,-152.5,-2.5,2.5,2**53,1e20,-1e20,1e100,-1e100],[-153,0,153]):
        yield precipitation_input(current_y=y,snow_start_y=threshold,weather="rain")


def unique(values):
    seen=set()
    for value in values:
        key=json.dumps(value,ensure_ascii=False,sort_keys=True)
        if key not in seen:
            seen.add(key)
            yield value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",type=Path,default=ROOT)
    parser.add_argument("--output-dir",type=Path,default=ROOT/"dogido-rust/fixtures")
    args=parser.parse_args()
    sys.path.insert(0,str(args.source_root.resolve()))
    from dogido_server.environment_context import project_environment
    from dogido_server.models import GameEvent
    from dogido_server.state_machine.precipitation import resolve_precipitation_context
    from dogido_server.state_machine.mixins.world_analysis import WorldAnalysisMixin
    from dogido_server.state_machine.mixins.common import CommonMixin

    class Probe(WorldAnalysisMixin):
        _weather_value=CommonMixin._weather_value
    probe=Probe()
    def precipitation_result(context):
        return {"context":asdict(context),"snow_can_be_scene_material":context.snow_can_be_scene_material,
                "prompt_line":context.prompt_line(),"prompt_details":context.to_prompt_details()}
    env_rows=[]
    for raw in unique(environment_cases()):
        parsed=GameEvent.model_validate(raw)
        # Rust no longer infers an interior fixture from nearby glass. Retain
        # window input in the case while the independent oracle ignores it.
        parsed=parsed.model_copy(update={"world":parsed.world.model_copy(update={"nearby_window_present":None})})
        env_rows.append({"event":raw,"expected":asdict(project_environment(parsed))})
    rain_rows=[{"input":raw,"expected":precipitation_result(resolve_precipitation_context(**raw))} for raw in unique(precipitation_cases())]
    frame_rows=[]
    for biome,weather,y,resources in itertools.product(
            [None,"unknown","plains","taiga","snowy_plains","frozen_peaks","desert","savanna","badlands","deep_dark","lush_caves","windswept_hills","minecraft:taiga"," TAIGA "],
            [None,"clear","rain","thunder"], [None,64,152.5,153.5],
            [[],[{"type":"block","name":"minecraft:snow"}], [{"type":"item","name":"snow"}],
             [{"type":"BLOCK","name":"powder_snow"},{"type":" block ","name":"snow_block"}]]):
        raw=event(player={"position":{"y":y}},world={"biome":biome,"weather":weather},nearby_resources=resources)
        parsed=GameEvent.model_validate(raw)
        entry=probe._biome_entry(biome) or {}
        climate={"biome_temperature":probe._biome_temperature(biome),"snow_start_y":probe._biome_snow_start_y(biome),
                 "biome_group_id":str(entry.get("group_id") or ""),"biome_downfall":probe._biome_downfall(biome)}
        frame_rows.append({"event":raw,"climate":climate,"expected":precipitation_result(probe._precipitation_context(parsed))})
    args.output_dir.mkdir(parents=True,exist_ok=True)
    counts={}
    for name,rows in [("environment-projection",env_rows),("precipitation",rain_rows),("precipitation-frame",frame_rows)]:
        (args.output_dir/(name+".jsonl")).write_text("".join(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n" for row in rows))
        counts[name]=len(rows)
    source_files=["dogido_server/environment_context.py","dogido_server/state_machine/precipitation.py",
                  "dogido_server/state_machine/mixins/world_analysis.py","data/catalogs/entries/minecraft_biome.json"]
    metadata={"counts":counts,"source_sha256":{p:hashlib.sha256((args.source_root/p).read_bytes()).hexdigest() for p in source_files}}
    (args.output_dir/"environment-projection-meta.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
