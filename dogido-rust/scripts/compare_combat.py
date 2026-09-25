#!/usr/bin/env python3
"""Python正本とRust戦闘Engineへ同じ観測系列を渡す純粋リプレイ。

状態・台詞・cue・断片列を比較する。通信、モデル、音声、サーバーは使わない。
等値対象外の意図的修正も両実装の結果を保存し、独立した契約で検査する。
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.config import Settings
from dogido_server.models import GameEvent
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.constants import HOSTILE_LABELS

BASE = datetime(2026, 9, 25, tzinfo=timezone.utc)
WARNING_KEYS = (
    "panic_distance", "rear_warning_distance", "recent_damage_window_ms",
    "hostile_comment_cooldown_ms", "multi_hostile_comment_cooldown_ms",
    "panic_scream_cooldown_ms", "hostile_mass_callout_threshold", "hostile_query_distance",
    "other_realm_swarm_visual_threshold", "other_realm_audio_generic_threshold",
)
# Pythonではspeech分類用のcue_id。Rustでは本文再生にするためkindに移したメタデータ。
SPEECH_CUES = {"aftermath": "aftermath_relief", "hostile_defeated": "hostile_defeated_reaction",
               "creeper_detonated": "creeper_detonation_reaction"}


def mob(kind="zombie", *, entity=None, distance=8.2, direction="front", **extra):
    return {"type": kind, "entity_id": entity or kind + "-1", "distance": distance,
            "direction": {"horizontal": direction, "vertical": "same"}, "certainty": "high", **extra}


def sound(kind="zombie", *, entity=None, direction="left", band="close", named=True):
    return {"label": kind, "source_id": entity or kind + "-1", "sound_event": f"minecraft:entity.{kind}.ambient",
            "direction": {"horizontal": direction}, "distance_band": band, "certainty": "high", "spoken_name_allowed": named}


def outcome(kind="zombie", result="player_kill", entity=None):
    return {"type": kind, "entity_id": entity or kind + "-1", "outcome": result,
            "evidence": "explosion_packet" if result == "creeper_detonation" else "server_death_event"}


def step(ms, *, name="status_snapshot", visual=(), audio=(), player=None, world=None, combat=None, text=None,
         complete=None, busy=False, meta=None):
    event = {
        "schema_version": "2026-05-24", "adapter": "combat-replay", "observed_at": (BASE + timedelta(milliseconds=ms)).isoformat(),
        "sequence": 1,
        "event": {"name": name, "source_kind": "auditory" if name == "hostile_audio_detected" else "system",
                  "priority_hint": "background", "certainty": "high"},
        "player": {"name": "replay", "dimension": "minecraft:overworld", "health": 20, "yaw": 0,
                   "position": {"x": 0, "y": 70, "z": 0}, **(player or {})},
        "world": {"time_phase": "day", "biome": "plains", "weather": "clear", "local_light": 15,
                  "sky_visible": True, "ceiling_height": 64, "enclosure_score": 0, "overhead_cover_type": "none",
                  "danger_darkness_score": 0, **(world or {})},
        "visual_threats": list(visual), "auditory_threats": list(audio),
        "combat": {"hostile_outcomes": [], **(combat or {})},
        "meta": {"user_text": text, **(meta or {})},
    }
    return {"ms": ms, "event": event, "complete": name not in {"hostile_audio_detected", "ambient_mob_detected"} if complete is None else complete, "busy": busy}


def cases():
    result = []
    def add(name, category, steps, *, intention=None):
        for sequence, item in enumerate(steps, 1):
            item["event"]["sequence"] = sequence
        result.append({"name": name, "category": category, "steps": steps, "intention": intention})

    for kind in ("zombie", "zombie_villager", "skeleton", "spider", "cave_spider", "husk", "stray", "creeper", "charged_creeper"):
        add("single_" + kind, "ordinary", [step(0, visual=[mob(kind)]), step(1000, visual=[mob(kind)])])
    for count in (2, 3, 4, 9):
        add(f"group_{count}", "ordinary", [step(0, visual=[mob(entity=f"z{i}") for i in range(count)])])
    # 通常9種以外・中立から敵対状態になった個体も、同じカタログ全件で検査する。
    for kind in sorted(HOSTILE_LABELS):
        for distance in (4.0, 8.2, 18.0):
            add(f"all_hostiles_{kind}_{distance}", "hostile_catalog", [step(0, visual=[mob(kind, distance=distance)]),
                step(2000, visual=[mob(kind, distance=distance)])])
    for distance in (2.99, 3.0, 3.01, 6.99, 7.0, 7.01):
        for direction in ("front", "back"):
            add(f"ambush_boundary_{distance}_{direction}", "ambush", [step(0, visual=[mob(distance=distance, direction=direction)]),
                step(2000, visual=[mob(distance=distance, direction=direction)])])
    for damage in (0, 1000, 1001, 3000, 3001):
        add(f"damage_boundary_{damage}", "damage", [step(0, visual=[mob("skeleton")], combat={"recent_damage_ms": damage}),
            step(2000, visual=[mob("skeleton")])])
    add("count_current_scan", "query", [step(0, combat={"hostile_scan_distance": 16, "hostiles_within_scan_ground": 3}, text="敵残り何体？")])
    add("count_legacy_scan", "query", [step(0, combat={"hostiles_within_30_ground": 3}, text="敵残り何体？")])
    add("count_clear", "query", [step(0, text="敵何体？")])
    target = mob("creeper", distance=11.6, direction="front_left")
    target["direction"]["cardinal"] = "southeast"
    add("direction_current_target", "query", [step(0, visual=[target], text="敵はどっち？")])
    add("direction_last_named", "query", [step(0, visual=[target]), step(1000, visual=[mob(distance=8), target], text="どっち？")])
    add("direction_yaw", "query", [step(0, visual=[mob(direction="front_left")], text="敵どこ？")])
    direct = step(1000, visual=[target])
    direct["input"] = "どっち？"
    add("direct_input_direction", "query", [step(0, visual=[target]), direct])
    direct = step(1000, visual=[mob()], combat={"hostile_scan_distance": 16, "hostiles_within_scan_ground": 3})
    direct["input"] = "敵残り何体？"
    add("direct_input_count", "query", [step(0, visual=[mob()]), direct])
    add("direction_named_species", "query", [step(0, visual=[mob("skeleton", distance=7.8), mob(distance=9)], text="ゾンビどこ？")],
        intention="explicit_species_query: 名前付き位置質問をコード即答へ追加し、別種へすり替えない")
    add("low_health_rearm", "low_health", [step(i * 2000, visual=[mob()], player={"health": h}) for i, h in enumerate([20, 5, 5, 6, 5])])
    add("low_health_initial", "low_health", [step(0, visual=[mob()], player={"health": 5})])
    add("quiet_three_times", "quiet", [step(i * 1300, visual=[mob(distance=5)], text="静かにして") for i in range(4)])
    add("quiet_expiry", "quiet", [step(i * 1300, visual=[mob(distance=5)], text="うるさい") for i in range(3)] + [step(10_000), step(18_001)])

    for named, moved in [(True, False), (False, False), (True, True)]:
        add(f"auditory_1_4_10_named{named}_moved{moved}", "auditory", [step(i * 1000, name="hostile_audio_detected", audio=[sound(named=named)],
            player={"position": {"x": i if moved else 0, "y": 70, "z": 0}}) for i in range(11)])
    add("auditory_warden_chasing", "auditory", [step(i * 1000, name="hostile_audio_detected", audio=[sound("warden")],
        player={"position": {"x": i, "y": 70, "z": 0}}) for i in range(11)])
    add("auditory_same_visual_is_not_unseen", "auditory", [step(i * 1000, visual=[mob()], audio=[sound()]) for i in range(5)])
    cave = {"sky_visible": False, "ceiling_height": 3, "enclosure_score": .6, "overhead_cover_type": "solid", "biome": "dripstone_caves"}
    add("auditory_occluded", "auditory", [step(0, name="hostile_audio_detected", audio=[sound()], world=cave)])
    add("auditory_other_realm_generic", "auditory", [step(0, name="hostile_audio_detected", audio=[sound(), sound("skeleton")], player={"dimension": "minecraft:the_nether"}, world={"biome": "nether_wastes"})])

    for kind in ("warden", "wither", "ender_dragon", "elder_guardian"):
        world = {"biome": "the_end"} if kind == "ender_dragon" else {}
        player = {"dimension": "minecraft:the_end"} if kind == "ender_dragon" else {}
        add("boss_reveal_" + kind, "boss", [step(0, visual=[mob(kind, distance=12)], player=player, world=world), step(1000, visual=[mob(kind, distance=12)], player=player, world=world)])
    add("warden_sonic_world", "boss", [step(ms, visual=[mob("warden", distance=12)],
        world={"ominous_sound_kind": "warden_sonic_boom", "ominous_sound_recent_ms": age}) for ms, age in [(0, 0), (1000, 0), (4000, 2500), (8000, 2501)]])
    for omen in ("ender_dragon_arena", "ender_dragon_summon", "wither_assembly"):
        add("omen_" + omen, "boss", [step(ms, world={"boss_omen_kind": omen}) for ms in (0, 1000, 30_001)])
    for kind in ("phantom", "ghast", "blaze", "vex"):
        target = mob(kind, distance=8)
        target["direction"]["vertical"] = "above"
        add("flying_" + kind, "flying", [step(0, visual=[target]), step(1000, visual=[target])])
    for weather, biome in [("rain", "plains"), ("rain", "desert"), ("thunder", "desert")]:
        add(f"daylight_{weather}_{biome}", "daylight", [step(0, visual=[mob()], world={"weather": weather, "biome": biome}),
            step(1000, visual=[mob()], world={"weather": weather, "biome": biome})])
    for kind in ("zombie", "skeleton"):
        add("daylight_water_" + kind, "daylight", [step(0, visual=[mob(kind, in_water=True)]), step(1000, visual=[mob(kind, in_water=True)])])
        add("daylight_fire_" + kind, "daylight", [step(0, visual=[mob(kind)]), step(1000, visual=[mob(kind, on_fire=True)]), step(2000, visual=[mob(kind, on_fire=True)])])
    end_args = {"player": {"dimension": "minecraft:the_end"}, "world": {"biome": "the_end"}}
    add("dragon_crystals", "boss", [step(i * 5000, combat={"dragon_phase": "holding_pattern", "end_crystal_count": count}, **end_args) for i, count in enumerate([10, 10, 9, 6, 0])])
    add("dragon_direction", "query", [step(0, visual=[mob("ender_dragon", distance=20)], combat={"dragon_horizontal": "back", "dragon_vertical": "above"}, text="ドラゴンどっち？", **end_args)])
    add("dragon_charge_landing", "boss", [step(i * 9000, visual=[mob("ender_dragon", distance=20)], combat={"dragon_phase": phase, "end_crystal_count": 0}, **end_args)
        for i, phase in enumerate(["holding_pattern", "charging_player", "charging_player", "landing", "sitting_attacking", "holding_pattern"])])
    for field, value in [("warden_recently_hurt", True), ("warden_nearby_iron_golem_count", 2), ("warden_end_crystal_bombardment_active", True), ("warden_tnt_minecart_setup_active", True), ("warden_ranged_trap_active", True)]:
        add("warden_" + field, "boss", [step(0, visual=[mob("warden", distance=12)]), step(2000, visual=[mob("warden", distance=12)], combat={field: value})])
    for kind, flag in [("warden", "warden_defeat_confirmed"), ("ender_dragon", "dragon_defeat_confirmed")]:
        add("boss_defeated_" + kind, "outcome", [step(0, visual=[mob(kind, distance=12)]), step(5000, name="combat_ended", combat={flag: True})])
        add("boss_unconfirmed_" + kind, "outcome", [step(0, visual=[mob(kind, distance=12)]), step(5000, name="combat_ended", combat={flag: False})])

    for death in ("slain by zombie", "fell out of world", "lava"):
        add("death_" + death, "outcome", [step(0, visual=[mob()]), step(1000, name="player_died", meta={"death_cause": death})])
    for result_name in ("player_kill", "other_death", "explosion_death"):
        o = outcome(result=result_name)
        add("defeated_" + result_name, "outcome", [step(0, visual=[mob()]), step(1000, name="hostile_defeated", combat={"hostile_outcomes": [o]}),
            step(2000, name="hostile_defeated", combat={"hostile_outcomes": [o]}), step(6000, name="combat_ended", combat={"hostile_outcomes": [o]})])
        add("aftermath_" + result_name, "outcome", [step(0, visual=[mob()]), step(6000, name="combat_ended", combat={"hostile_outcomes": [o]})])
    for kind in ("creeper", "charged_creeper"):
        for count in (1, 2):
            outcomes = [outcome(kind, "creeper_detonation", f"c{i}") for i in range(count)]
            add(f"detonation_{kind}_{count}", "outcome", [step(0, name="creeper_detonated", combat={"hostile_outcomes": outcomes}), step(5000, name="combat_ended", combat={"hostile_outcomes": outcomes})])
        add("fuse_rearm_" + kind, "fuse", [step(i * 1300, visual=[mob(kind, distance=4, fuse_active=fuse)]) for i, fuse in enumerate([False, True, True, False, True])])
    add("combat_end_disengagement", "outcome", [step(0, visual=[mob()]), step(6000, name="combat_ended"), step(14_001)])
    add("combat_end_stale_active_hint", "outcome", [step(0, visual=[mob()]), step(6000, name="combat_ended", combat={"combat_active_hint": True})])
    for remaining in ("visual", "audio", "count"):
        add("combat_end_remaining_" + remaining, "outcome", [step(0, visual=[mob()]), step(6000, name="combat_ended",
            visual=[mob()] if remaining == "visual" else [], audio=[sound()] if remaining == "audio" else [], combat={"hostiles_within_scan_ground": 1} if remaining == "count" else {})])
    add("dimension_change_flush", "dimension", [step(0, visual=[mob()]), step(1000, player={"dimension": "minecraft:the_nether"}, world={"biome": "nether_wastes"}),
        step(5000, player={"dimension": "minecraft:overworld"}), step(9000)])

    add("partial_sound_keeps_visual", "intentional", [step(0, visual=[mob(distance=5)]), step(1000, name="hostile_audio_detected", audio=[sound()])],
        intention="partial_keeps_visual: 音だけの通知の空visualを敵の消失と見なさない")
    add("busy_audio_milestone", "intentional", [step(i * 1000, name="hostile_audio_detected", audio=[sound()], busy=i < 10) for i in range(11)],
        intention="busy_milestone: 再生busy中に越えた音1/4/10回目を最新一件だけ保留")
    add("legacy_disappearance_is_not_kill", "intentional", [step(0, visual=[mob()], combat={"hostile_outcomes": None}), step(6000, name="combat_ended", combat={"hostile_outcomes": None})],
        intention="no_inferred_kill: 旧adapterの視認消失による撃破推測を復活させない")
    add("suppressed_fuse_preempts_breath", "intentional", [step(i * 1300, visual=[mob("creeper", distance=4, fuse_active=False)], text="うるさい") for i in range(3)]
        + [step(5000, visual=[mob("creeper", distance=4, fuse_active=True)])], intention="fuse_preempts_breath: 抑制呼吸で導火警告を消費しない")
    return result


def canonical_action(text, cue=None, sequence=()):
    return {"text": text or None, "cue_id": cue, "cue_sequence": list(sequence or ())}


def rust_actions(row):
    actions = []
    for speech in (row.get("decision") or {}).get("actions", []):
        plan = speech.get("visual_plan")
        if plan:
            if cue := plan.get("cue"):
                actions.append(canonical_action(cue["text"], cue["id"]))
            if plan["text"]:
                actions.append(canonical_action(plan["text"], sequence=plan.get("cue_sequence")))
        else:
            actions.append(canonical_action(speech["text"], speech.get("cue_id") or SPEECH_CUES.get(speech["kind"]), speech.get("cue_sequence")))
    decision = row.get("decision") or {}
    if decision.get("dimension_changed") and decision.get("stop_audio") and not actions:
        # Pythonの空interrupt actionと、Rustのstop_audioは同じ次元移動flush契約。
        actions.append(canonical_action(None))
    return actions


def intended_checks(case, rust_rows):
    code = case["intention"].split(":", 1)[0]
    speech = [s for row in rust_rows for s in (row.get("decision") or {}).get("actions", [])]
    if code == "partial_keeps_visual":
        return {"visual_panic_survives_audio_only": rust_rows[-1]["mode"] == "panic"}
    if code == "busy_milestone":
        return {"no_speech_while_busy": all(not (row.get("decision") or {}).get("actions") for row in rust_rows[:-1]),
                "latest_audio_milestone_delivered": len(speech) == 1 and speech[0]["kind"] == "auditory_hostile"}
    if code == "no_inferred_kill":
        return {"no_kill_claim": all(not any(word in s["text"] for word in ["倒した", "倒せた", "撃破", "退治"]) for s in speech),
                "no_kill_note": all("倒した" not in note for row in rust_rows for note in row.get("notes", [])),
                "disengagement_leaf": bool(speech) and (speech[-1].get("leaf") or {}).get("details", {}).get("combat_outcome") == "disengaged"}
    if code == "fuse_preempts_breath":
        return {"fuse_delivered": any(s["kind"] == "creeper_fuse" for s in (rust_rows[-1].get("decision") or {}).get("actions", []))}
    if code == "explicit_species_query":
        return {"named_species_direction": any(s["kind"] == "hostile_direction" and "9ブロック" in s["text"] for s in speech)}
    raise ValueError("Unregistered intentional change: " + code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "dogido-rust/target/debug/examples/combat_batch")
    parser.add_argument("--report", type=Path, default=ROOT / "dogido-rust/reports/combat-parity.json")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    settings = Settings(_env_file=None, llm_enabled=False, audio_enabled=False, decision_policy="legacy")
    defaults = json.loads((ROOT / "dogido-rust/src/combat/defaults.json").read_text())
    shared = {key: getattr(settings, key, value) for key, value in defaults.items()}
    warning = {key: getattr(settings, key) for key in WARNING_KEYS}
    all_cases = cases()
    expected = []
    for case in all_cases:
        case["settings"], case["warning_settings"] = shared, warning
        machine = DogidoStateMachine(settings)
        rows = []
        for item in case["steps"]:
            e = deepcopy(item["event"])
            if item.get("input"):
                e["meta"]["user_text"] = item["input"]
            result = machine.process(GameEvent.model_validate(e))
            rows.append({"mode": result.state.mode, "shut_up_count": result.state.shut_up_count,
                         "pending_notes": list(result.state.pending_dialogue_notes),
                         "actions": [canonical_action(a.text, a.cue_id, a.cue_sequence) for a in result.actions],
                         "raw_actions": [asdict(a) for a in result.actions]})
        expected.append(rows)
    run = subprocess.run([str(args.binary)], input="".join(json.dumps(case, ensure_ascii=False) + "\n" for case in all_cases), text=True, capture_output=True, cwd=ROOT)
    if run.returncode:
        raise SystemExit(f"Rust replay failed ({run.returncode}): {run.stderr}")
    actual = [json.loads(line) for line in run.stdout.splitlines()]
    if any(row.get("replay_schema") != 1 or "notes" not in row for rows in actual for row in rows):
        raise SystemExit("Rust replay protocol mismatch; rebuild --example combat_batch before comparing")
    failures, intentional, matched = [], [], []
    for case, python_rows, rust_rows in zip(all_cases, expected, actual, strict=True):
        if len(python_rows) != len(rust_rows):
            raise AssertionError("step count differs: " + case["name"])
        differences = []
        for index, (py, rs) in enumerate(zip(python_rows, rust_rows, strict=True)):
            p = {"mode": py["mode"], "actions": py["actions"]}
            r = {"mode": rs["mode"], "actions": rust_actions(rs)}
            if p != r:
                differences.append({"step": index, "ms": case["steps"][index]["ms"], "python": p, "rust": r})
        record = {"name": case["name"], "category": case["category"], "differences": differences,
                  "python": python_rows, "rust": rust_rows}
        if case["intention"]:
            record.update(reason=case["intention"], checks=intended_checks(case, rust_rows))
            intentional.append(record)
        elif differences:
            failures.append(record)
        else:
            matched.append(case["name"])
    report = {"scope": "Combat Engine decisions only; no playback, HTTP, model or live Minecraft",
              "normalization": "Rust visual_plan split into cue/body; non-playback outcome cue metadata recovered from kind; dimension stop_audio represented as empty flush action; no text changes or failure exclusions",
              "settings": shared, "runtime_only_settings": [key for key in shared if not hasattr(settings, key)],
              "series": len(all_cases), "steps": sum(len(c["steps"]) for c in all_cases),
              "matched": matched, "failures": failures, "intentional_differences": intentional,
              "failed_by_category": dict(Counter(f["category"] for f in failures)), "cases": all_cases}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    summary = {"parity_pass": len(matched), "parity_fail": len(failures), "intentional": len(intentional),
               "intentional_failed": sum(not all(item["checks"].values()) for item in intentional),
               "failed_by_category": report["failed_by_category"], "report": str(args.report)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for failure in failures[:8]:
        print(json.dumps({"name": failure["name"], "differences": failure["differences"][:2]}, ensure_ascii=False))
    raise SystemExit(bool(failures) or bool(summary["intentional_failed"]))


if __name__ == "__main__":
    main()
