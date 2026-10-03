use super::*;

fn event(types: &[&str]) -> GameEvent {
    GameEvent::parse(json!({
        "schema_version":"2026-05-24", "adapter":"fixture", "observed_at":"2026-09-24T00:00:00Z", "sequence":1,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld"}, "world":{"time_phase":"night","biome":"plains"},
        "visual_threats":types.iter().enumerate().map(|(i,k)| json!({"type":k,"entity_id":format!("{k}-{i}"),"distance":8.0,
            "direction":{"horizontal":"front","vertical":"same"}})).collect::<Vec<_>>()
    })).unwrap()
}
fn change(e: &GameEvent, f: impl FnOnce(&mut Value)) -> GameEvent {
    let mut v = serde_json::to_value(e).unwrap();
    f(&mut v);
    GameEvent::parse(v).unwrap()
}
fn observe(p: &mut Specials, e: &GameEvent, now: u64) {
    p.observe(e, now, true, &Settings::default());
}

#[test]
fn dragon_reveal_then_crystal_decreases_coalesce_and_clear() {
    let s = Settings::default();
    let mut p = Specials::default();
    let mut e = change(&event(&["ender_dragon"]), |v| {
        v["combat"] = json!({"dragon_phase":"holding_pattern","end_crystal_count":10});
    });
    observe(&mut p, &e, 0);
    assert!(p.dragon(&e, 0, &s).is_none());
    let reveal = p.boss_visual(&e, 0, true, &s).unwrap();
    assert_eq!(reveal.text, "くるで！");
    assert!(reveal.cue_id.is_none());
    observe(&mut p, &e, 1000);
    assert!(p.dragon(&e, 1000, &s).unwrap().text.contains("10本"));
    e = change(&e, |v| v["combat"]["end_crystal_count"] = 9.into());
    observe(&mut p, &e, 2000);
    assert_eq!(
        p.dragon(&e, 2000, &s).unwrap().text,
        "クリスタルあと9本や！"
    );
    for (time, count) in [(3000, 8), (4000, 6)] {
        e = change(&e, |v| v["combat"]["end_crystal_count"] = count.into());
        observe(&mut p, &e, time);
        assert!(p.dragon(&e, time, &s).is_none());
    }
    observe(&mut p, &e, 6000);
    assert_eq!(
        p.dragon(&e, 6000, &s).unwrap().text,
        "クリスタルあと6本や！"
    );
    e = change(&e, |v| v["combat"]["end_crystal_count"] = 0.into());
    observe(&mut p, &e, 10000);
    assert!(p.dragon(&e, 10000, &s).unwrap().text.contains("全部割れた"));
    observe(&mut p, &e, 20000);
    assert!(p.dragon(&e, 20000, &s).is_none());
    assert!(p.boss_visual(&e, 20000, true, &s).is_none());
}

#[test]
fn dragon_phase_only_perch_once_per_landing_charge_cooldown_and_priority() {
    let s = Settings::default();
    let mut p = Specials::default();
    let mut e = change(&event(&[]), |v| {
        v["combat"] = json!({"dragon_phase":"landing","end_crystal_count":5})
    });
    observe(&mut p, &e, 0);
    assert_eq!(p.dragon(&e, 0, &s).unwrap().kind, "dragon_perch");
    observe(&mut p, &e, 1);
    assert_eq!(p.dragon(&e, 1, &s).unwrap().kind, "dragon_crystal_hint");
    observe(&mut p, &e, 2);
    assert!(p.dragon(&e, 2, &s).is_none());
    e = change(&e, |v| {
        v["combat"]["dragon_phase"] = "charging_player".into()
    });
    observe(&mut p, &e, 100);
    assert_eq!(p.dragon(&e, 100, &s).unwrap().kind, "dragon_charge");
    observe(&mut p, &e, 8099);
    assert!(p.dragon(&e, 8099, &s).is_none());
    observe(&mut p, &e, 8100);
    assert_eq!(p.dragon(&e, 8100, &s).unwrap().kind, "dragon_charge");
    e = change(&e, |v| {
        v["combat"]["dragon_phase"] = "sitting_flaming".into()
    });
    observe(&mut p, &e, 9000);
    assert_eq!(p.dragon(&e, 9000, &s).unwrap().kind, "dragon_perch");
    let empty = event(&[]);
    observe(&mut p, &empty, 29000);
    assert!(p.dragon_context(&empty, 29000));
    observe(&mut p, &empty, 29001);
    assert!(!p.dragon_context(&empty, 29001));
}

#[test]
fn warden_reveal_precedes_tactics_golems_precede_extreme_and_attack() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = change(
        &event(&["warden"]),
        |v| v["combat"] = json!({"warden_recently_hurt":true,"warden_nearby_iron_golem_count":2,"warden_ranged_trap_active":true}),
    );
    observe(&mut p, &e, 0);
    assert!(p.warden_tactic(&e, 0, &s).is_none());
    let reveal = p.boss_visual(&e, 0, true, &s).unwrap();
    assert_eq!(reveal.cue_id, Some("boss_reveal_scream"));
    assert!(reveal.text.contains("逃げろ"));
    observe(&mut p, &e, 1);
    assert_eq!(
        p.warden_tactic(&e, 1, &s).unwrap().kind,
        "warden_golem_army"
    );
    observe(&mut p, &e, 2);
    assert_eq!(
        p.warden_tactic(&e, 2, &s).unwrap().kind,
        "warden_extreme_tactics"
    );
    observe(&mut p, &e, 3);
    assert!(p.warden_tactic(&e, 3, &s).is_none());
    let hurt = change(&e, |v| v["combat"] = json!({"warden_recently_hurt":true}));
    observe(&mut p, &hurt, 4);
    assert_eq!(
        p.warden_tactic(&hurt, 4, &s).unwrap().kind,
        "warden_attack_start"
    );
    observe(&mut p, &hurt, 5);
    assert!(p.warden_tactic(&hurt, 5, &s).is_none());
}

#[test]
fn warden_recent_context_and_combat_reset_do_not_invent_presence() {
    let s = Settings::default();
    let mut p = Specials::default();
    let input = change(&event(&[]), |v| {
        v["combat"] = json!({"warden_end_crystal_bombardment_active":true})
    });
    observe(&mut p, &input, 0);
    assert!(p.warden_tactic(&input, 0, &s).is_none());
    observe(&mut p, &event(&["warden"]), 1000);
    observe(&mut p, &input, 1001);
    assert_eq!(
        p.warden_tactic(&input, 1001, &s).unwrap().kind,
        "warden_extreme_tactics"
    );
    let ended = change(&event(&[]), |v| v["event"]["name"] = "combat_ended".into());
    observe(&mut p, &ended, 2000);
    observe(&mut p, &input, 12000);
    assert!(p.warden_tactic(&input, 12000, &s).is_none());
    observe(&mut p, &event(&["warden"]), 13000);
    observe(&mut p, &input, 13001);
    assert_eq!(
        p.warden_tactic(&input, 13001, &s).unwrap().kind,
        "warden_extreme_tactics"
    );
}

#[test]
fn beam_ignores_biome_and_uses_own_freshness_and_cooldown() {
    let s = Settings::default();
    let mut p = Specials::default();
    let beam = change(
        &event(&[]),
        |v| v["world"] = json!({"biome":"deep_dark","ominous_sound_kind":"warden_sonic_boom","ominous_sound_recent_ms":2500}),
    );
    observe(&mut p, &beam, 0);
    let a = p.sonic_boom(&beam, 0, &s).unwrap();
    assert!(a.interrupt);
    assert_eq!(a.protect_ms, 1500);
    assert!(p.sonic_boom(&beam, 3999, &s).is_none());
    assert!(p.sonic_boom(&beam, 4000, &s).is_some());
    let stale = change(&beam, |v| {
        v["world"]["ominous_sound_recent_ms"] = 2501.into()
    });
    assert!(p.sonic_boom(&stale, 8000, &s).is_none());
    let audio = change(
        &event(&[]),
        |v| {
            v["auditory_threats"] =
                json!([{"label":"warden","sound_event":"minecraft:entity.warden.sonic_boom"}])
        },
    );
    assert!(p.sonic_boom(&audio, 8000, &s).is_some());
}

#[test]
fn wither_elder_reveal_once_then_tactical_words_and_omen_kind_change() {
    let s = Settings::default();
    for kind in ["wither", "elder_guardian"] {
        let mut p = Specials::default();
        let e = event(&[kind]);
        observe(&mut p, &e, 0);
        assert_eq!(
            p.boss_visual(&e, 0, true, &s).unwrap().cue_id,
            Some("boss_reveal_scream")
        );
        observe(&mut p, &e, 1);
        assert!(p.boss_visual(&e, 1, true, &s).is_none());
        observe(&mut p, &e, 60000);
        assert!(p.boss_visual(&e, 60000, true, &s).unwrap().cue_id.is_none());
    }
    let mut p = Specials::default();
    let arena = change(&event(&[]), |v| {
        v["world"]["boss_omen_kind"] = "ender_dragon_arena".into()
    });
    assert!(p.omen(&arena, 0, &s).is_some());
    assert!(p.omen(&arena, 29999, &s).is_none());
    assert!(p.omen(&arena, 30000, &s).is_some());
    let summon = change(&arena, |v| {
        v["world"]["boss_omen_kind"] = "ender_dragon_summon".into()
    });
    assert!(p.omen(&summon, 30001, &s).unwrap().text.contains("腹括る"));
    let build = change(&arena, |v| {
        v["world"]["boss_omen_kind"] = "wither_assembly".into()
    });
    assert_eq!(
        p.omen(&build, 30002, &s).unwrap().text,
        "えっ・・・何しとるん？"
    );
}

#[test]
fn ominous_context_shared_cooldown_stage_and_leaf_metadata() {
    let s = Settings::default();
    let mut p = Specials::default();
    let surface = change(
        &event(&[]),
        |v| v["world"] = json!({"biome":"plains","ominous_sound_kind":"sculk_sensor","ominous_sound_recent_ms":0}),
    );
    observe(&mut p, &surface, 0);
    assert!(p.ominous(&surface, 0, &s).is_none());
    assert!(!p.ominous_presence(0, &s));
    let deep = change(&surface, |v| {
        v["world"]["biome"] = "deep_dark".into();
        v["world"]["ominous_sound_recent_ms"] = 1001.into();
    });
    observe(&mut p, &deep, 1001);
    let first = p.ominous(&deep, 1001, &s).unwrap();
    let leaf = first.leaf.unwrap();
    assert_eq!(leaf.kind, "deep_dark_ominous_sound");
    assert_eq!(leaf.details["ominous_stage"], 1);
    let shriek = change(&deep, |v| {
        v["world"]["ominous_sound_kind"] = "sculk_shrieker".into()
    });
    observe(&mut p, &shriek, 2000);
    assert!(p.ominous(&shriek, 2000, &s).is_some());
    for now in [20000, 40000, 60000, 80000, 100000, 120000] {
        observe(&mut p, &shriek, now);
        assert!(p.ominous(&shriek, now, &s).is_none());
    }
    observe(&mut p, &shriek, 122000);
    assert_eq!(
        p.ominous(&shriek, 122000, &s)
            .unwrap()
            .leaf
            .unwrap()
            .details["ominous_stage"],
        2
    );
    let audio = change(&shriek, |v| {
        v["event"]["name"] = "hostile_audio_detected".into()
    });
    observe(&mut p, &audio, 240002);
    assert!(p.ominous(&audio, 240002, &s).is_none());
    observe(&mut p, &surface, 240003);
    assert!(!p.ominous_presence(240003, &s));
    let heartbeat = change(&surface, |v| {
        v["world"]["ominous_sound_kind"] = "warden_heartbeat".into()
    });
    observe(&mut p, &heartbeat, 250000);
    assert_eq!(
        p.ominous(&heartbeat, 250000, &s).unwrap().text,
        "なんやこの音"
    );
}

#[test]
fn boss_presence_stops_ominous_and_mining_fatigue_uses_edges() {
    let s = Settings::default();
    let mut p = Specials::default();
    observe(&mut p, &event(&["warden"]), 0);
    let omen = change(
        &event(&[]),
        |v| {
            v["world"] =
                json!({"ominous_sound_kind":"warden_heartbeat","ominous_sound_recent_ms":0})
        },
    );
    observe(&mut p, &omen, 1);
    assert!(p.ominous(&omen, 1, &s).is_none());
    let fatigue = change(&event(&[]), |v| {
        v["player"]["active_status_effects"] = json!(["minecraft:mining_fatigue"])
    });
    observe(&mut p, &fatigue, 100);
    assert!(p.mining_fatigue(&fatigue, 100, &s).is_some());
    p.observe(&event(&[]), 1000, false, &s);
    observe(&mut p, &fatigue, 200000);
    assert!(p.mining_fatigue(&fatigue, 200000, &s).is_none());
    observe(&mut p, &event(&[]), 200001);
    observe(&mut p, &fatigue, 200002);
    assert!(p.mining_fatigue(&fatigue, 200002, &s).is_some());
}

#[test]
fn neutral_enderman_requires_recent_calm_observation_and_per_species_cooldown() {
    let s = Settings::default();
    let mut p = Specials::default();
    let angry = event(&["enderman"]);
    observe(&mut p, &angry, 0);
    assert!(p.neutral_hostile(&angry, 0, &s).is_none());
    let calm = change(&event(&[]), |v| {
        v["passive_mobs"] = json!([{"type":"enderman","temperament":"neutral"}])
    });
    observe(&mut p, &calm, 1);
    observe(&mut p, &angry, 2);
    assert!(
        p.neutral_hostile(&angry, 2, &s)
            .unwrap()
            .text
            .contains("エンダーマン")
    );
    observe(&mut p, &angry, 60001);
    assert!(p.neutral_hostile(&angry, 60001, &s).is_none());
    observe(&mut p, &angry, 60002);
    assert!(p.neutral_hostile(&angry, 60002, &s).is_some());
    observe(&mut p, &angry, 120002);
    assert!(p.neutral_hostile(&angry, 120002, &s).is_none());
}

#[test]
fn flying_prefers_above_excludes_dragon_and_partial_events_do_not_rearm() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = change(&event(&["ender_dragon", "ghast", "phantom"]), |v| {
        v["visual_threats"][1]["distance"] = 4.0.into();
        v["visual_threats"][2]["distance"] = 12.0.into();
        v["visual_threats"][2]["direction"]["vertical"] = "above".into();
    });
    observe(&mut p, &e, 0);
    let first = p.flying(&e, 0, true, &s).unwrap();
    assert_eq!(first.text, "上からファントムきたで！");
    assert_eq!(first.cue_id, Some("spot_hostile_gasp"));
    p.observe(&event(&[]), 100, false, &s);
    observe(&mut p, &e, 1000);
    assert!(p.flying(&e, 1000, true, &s).is_none());
    observe(&mut p, &event(&[]), 2000);
    observe(&mut p, &e, 3000);
    assert!(p.flying(&e, 3000, false, &s).unwrap().cue_id.is_none());
    let far = change(&event(&["ghast"]), |v| {
        v["visual_threats"][0]["distance"] = 16.1.into()
    });
    let mut p = Specials::default();
    observe(&mut p, &far, 0);
    assert!(p.flying(&far, 0, true, &s).is_none());
}

#[test]
fn rear_requires_exact_event_back_melee_range_and_global_cooldown() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = change(&event(&["zombie"]), |v| {
        v["event"]["name"] = "threat_approaching".into();
        v["visual_threats"][0]["distance"] = 3.0.into();
        v["visual_threats"][0]["direction"]["horizontal"] = "back".into();
    });
    for invalid in [
        change(&e, |v| v["event"]["name"] = "status_snapshot".into()),
        change(&e, |v| {
            v["visual_threats"][0]["direction"]["horizontal"] = "back_left".into()
        }),
        change(&e, |v| v["visual_threats"][0]["distance"] = 3.1.into()),
        change(&e, |v| v["visual_threats"][0]["type"] = "skeleton".into()),
    ] {
        observe(&mut p, &invalid, 0);
        assert!(p.rear_ambush(&invalid, 0, true, &s).is_none());
    }
    observe(&mut p, &e, 1);
    let first = p.rear_ambush(&e, 1, true, &s).unwrap();
    assert_eq!(first.cue_id, Some("ushiro_scream"));
    assert!(first.text.ends_with("うしろ！うしろ〜！"));
    assert_eq!(first.protect_ms, 2000);
    let another = change(&e, |v| {
        v["visual_threats"][0]["entity_id"] = "second".into()
    });
    observe(&mut p, &another, 60000);
    assert!(p.rear_ambush(&another, 60000, true, &s).is_none());
    observe(&mut p, &another, 60001);
    assert!(
        p.rear_ambush(&another, 60001, false, &s)
            .unwrap()
            .cue_id
            .is_none()
    );
}

#[test]
fn classic_sha1_matches_known_vectors_and_python_seed() {
    assert_eq!(sha1_first(b""), 0xda);
    assert_eq!(sha1_first(b"abc"), 0xa9);
    assert_eq!(
        sha1_first(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
        0x84
    );
    let s = Settings::default();
    let e = change(&event(&["zombie"]), |v| {
        v["event"]["name"] = "threat_approaching".into();
        v["visual_threats"][0]["distance"] = 2.0.into();
        v["visual_threats"][0]["direction"]["horizontal"] = "back".into();
        v["meta"]["call_name"] = "太郎".into();
    });
    let mut classic = 0;
    for seq in 1..=256 {
        let e = change(&e, |v| v["sequence"] = seq.into());
        let mut p = Specials::default();
        observe(&mut p, &e, 0);
        let result = p.rear_ambush(&e, 0, false, &s).unwrap();
        if result.kind == "ushiro_classic" {
            classic += 1;
            assert!(result.text.starts_with("志村"));
        } else {
            assert!(result.text.starts_with("太郎"));
        }
    }
    assert!((10..50).contains(&classic));
}

#[test]
fn dark_front_ambush_requires_context_fresh_target_and_respects_water_survivor() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = change(&event(&["zombie"]), |v| {
        v["visual_threats"][0]["distance"] = 4.0.into()
    });
    observe(&mut p, &e, 0);
    assert!(p.front_ambush(&e, 0, false, true, true, &s).is_none());
    assert!(p.front_ambush(&e, 0, true, false, true, &s).is_none());
    let a = p.front_ambush(&e, 0, true, true, true, &s).unwrap();
    assert_eq!(a.cue_id, Some("front_spawn_scream"));
    assert_eq!(a.protect_ms, 1600);
    observe(&mut p, &e, 1000);
    assert!(p.front_ambush(&e, 1000, true, true, true, &s).is_none());
    let water = change(&e, |v| {
        v["world"] = json!({"time_phase":"day","sky_visible":true});
        v["visual_threats"][0]["in_water"] = true.into();
    });
    let mut p = Specials::default();
    observe(&mut p, &water, 0);
    assert!(p.front_ambush(&water, 0, true, true, true, &s).is_none());
}

#[test]
fn rain_water_fire_order_metadata_and_count_suffix() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = change(&event(&["zombie", "skeleton"]), |v| {
        v["world"] =
            json!({"time_phase":"day","sky_visible":true,"weather":"rain","biome":"plains"});
        v["visual_threats"][1]["in_water"] = true.into();
    });
    observe(&mut p, &e, 0);
    assert_eq!(p.daylight_rain(&e, 0, &s).unwrap().kind, "daylight_rain");
    observe(&mut p, &e, 1);
    assert!(p.daylight_rain(&e, 1, &s).is_none());
    let water = p.daylight_water(&e, 1, &s).unwrap();
    assert_eq!(water.protect_ms, 5000);
    assert!(water.text.contains("スケルトン1体、ゾンビ1体おるで。"));
    let leaf = water.leaf.unwrap();
    assert_eq!(leaf.kind, "daylight_water");
    assert_eq!(leaf.details["count"], 1);
    assert_eq!(leaf.details["hostiles"], json!(["スケルトン"]));
    assert_eq!(
        leaf.details["mob_states"][0]["environment"]["touching_water"],
        true
    );
    assert_eq!(leaf.temperature, 0.6);
    assert_eq!(
        leaf.details["__speech_suffix"],
        " スケルトン1体、ゾンビ1体おるで。"
    );
    assert!(p.daylight_water(&e, 120000, &s).is_none());
    assert!(p.daylight_water(&e, 120001, &s).is_some());
    let fire = change(&e, |v| {
        v["world"]["weather"] = "clear".into();
        v["visual_threats"][0]["on_fire"] = true.into();
    });
    observe(&mut p, &fire, 120002);
    let a = p.burning(&fire, 120002, &s).unwrap();
    assert_eq!(a.text, "っしゃ！もえろもえろおお！");
    assert_eq!(a.leaf.unwrap().temperature, 0.72);
    p.observe(&event(&[]), 120003, false, &s);
    observe(&mut p, &fire, 140000);
    assert!(p.burning(&fire, 140000, &s).is_none());
    observe(&mut p, &e, 140001);
    observe(&mut p, &fire, 140002);
    assert!(p.burning(&fire, 140002, &s).is_some());
}

#[test]
fn daylight_water_zombie_uses_shared_leaf_and_occluded_measurements_are_withheld() {
    let settings = Settings::default();
    let event = change(&event(&["zombie"]), |value| {
        value["world"] = json!({"time_phase":"day","sky_visible":true});
        value["visual_threats"][0]["in_water"] = true.into();
        value["visual_threats"][0]["environment"] =
            json!({"touching_water":true,"submerged_in_water":false});
    });
    let mut specials = Specials::default();
    let leaf = specials
        .daylight_water(&event, 0, &settings)
        .unwrap()
        .leaf
        .unwrap();
    assert_eq!(leaf.kind, "daylight_water");
    assert_eq!(leaf.details["hostiles"], json!(["ゾンビ"]));
    assert_eq!(
        leaf.details["mob_states"][0]["environment"]["submerged_in_water"],
        false
    );
    let occluded = change(&event, |value| {
        value["visual_threats"][0]["environment"] = json!({})
    });
    assert!(
        Specials::default()
            .daylight_water(&occluded, 0, &settings)
            .is_none()
    );
}

#[test]
fn dry_biome_rain_uses_overcast_and_night_does_not_claim_daylight() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = change(
        &event(&["zombie"]),
        |v| {
            v["world"] =
                json!({"time_phase":"day","sky_visible":true,"weather":"rain","biome":"desert"})
        },
    );
    observe(&mut p, &e, 0);
    assert!(
        p.daylight_rain(&e, 0, &s)
            .unwrap()
            .text
            .contains("どんより")
    );
    let night = change(&e, |v| v["world"]["time_phase"] = "night".into());
    observe(&mut p, &night, 120000);
    assert!(p.daylight_rain(&night, 120000, &s).is_none());
    let thunder = change(&e, |v| v["world"]["weather"] = "thunder".into());
    observe(&mut p, &thunder, 120000);
    assert!(
        p.daylight_rain(&thunder, 120000, &s)
            .unwrap()
            .text
            .contains("雷")
    );
}

#[test]
fn warp_flush_latch_authoritative_clear_and_delayed_return() {
    let s = Settings::default();
    let mut p = Specials::default();
    let home = event(&[]);
    assert!(!p.observe(&home, 0, true, &s));
    let nether = change(&event(&["zombie", "zombie", "zombie", "zombie"]), |v| {
        v["player"]["dimension"] = "minecraft:the_nether".into()
    });
    assert!(p.observe(&nether, 100, true, &s));
    observe(&mut p, &nether, 1100);
    assert!(p.warp_mass(&nether, 1100, Mode::Panic, &s).is_some());
    assert!(p.mass_latched());
    let audio = change(&event(&[]), |v| {
        v["player"]["dimension"] = "minecraft:the_nether".into();
        v["event"]["name"] = "hostile_audio_detected".into();
    });
    p.observe(&audio, 1200, false, &s);
    assert!(p.mass_latched());
    observe(&mut p, &nether, 2100);
    assert!(p.warp_mass(&nether, 2100, Mode::Panic, &s).is_none());
    let clear = change(&audio, |v| v["event"]["name"] = "status_snapshot".into());
    observe(&mut p, &clear, 2200);
    assert!(!p.mass_latched());
    observe(&mut p, &nether, 2300);
    assert!(
        p.warp_mass(&nether, 2300, Mode::SuppressedPanic, &s)
            .unwrap()
            .text
            .ends_with("……。")
    );
    assert!(p.observe(&home, 3000, true, &s));
    assert!(p.overworld_return(&home, 6499, &s).is_none());
    let threat = event(&["zombie"]);
    observe(&mut p, &threat, 6500);
    assert!(p.overworld_return(&threat, 6500, &s).is_none());
    observe(&mut p, &home, 6600);
    let back = p.overworld_return(&home, 6600, &s).unwrap();
    assert!(matches!(back.scope, Scope::Safe));
    assert!(p.overworld_return(&home, 6601, &s).is_none());
}

#[test]
fn initial_nether_is_warp_but_initial_overworld_and_expired_window_are_not() {
    let s = Settings::default();
    let mut p = Specials::default();
    let e = event(&["zombie", "zombie", "zombie", "zombie"]);
    observe(&mut p, &e, 0);
    assert!(p.warp_mass(&e, 0, Mode::Panic, &s).is_none());
    let nether = change(&e, |v| v["player"]["dimension"] = "the_nether".into());
    let mut p = Specials::default();
    assert!(!p.observe(&nether, 0, true, &s));
    assert!(p.warp_mass(&nether, 89999, Mode::Panic, &s).is_some());
    let mut p = Specials::default();
    observe(&mut p, &nether, 0);
    assert!(p.warp_mass(&nether, 90000, Mode::Panic, &s).is_none());
}

#[test]
fn sensor_waits_through_one_second_and_shrieker_replaces_it_without_spending_cooldown() {
    let settings = Settings::default();
    let make = |kind: &str, age: i64| {
        change(&event(&[]), |v| {
            v["world"] = json!({"biome":"deep_dark","ominous_sound_kind":kind,"ominous_sound_recent_ms":age});
        })
    };
    for shriek_delay in [999, 1000] {
        let mut p = Specials::default();
        for age in [0, 500, shriek_delay] {
            let sensor = make("sculk_sensor", age);
            observe(&mut p, &sensor, age as u64);
            assert!(p.ominous(&sensor, age as u64, &settings).is_none());
            assert!(p.ominous_comment_at.is_none());
        }
        let shriek = make("sculk_shrieker", 0);
        observe(&mut p, &shriek, shriek_delay as u64);
        let chosen = p.ominous(&shriek, shriek_delay as u64, &settings).unwrap();
        assert_eq!(
            chosen.leaf.unwrap().details["ominous_kind"],
            "sculk_shrieker"
        );
        let old_sensor = make("sculk_sensor", 1001);
        observe(&mut p, &old_sensor, 1001);
        assert!(p.ominous(&old_sensor, 1001, &settings).is_none());
    }
    let mut p = Specials::default();
    let sensor = make("sculk_sensor", 1001);
    observe(&mut p, &sensor, 1001);
    assert!(p.ominous(&sensor, 1001, &settings).is_some());
}

#[test]
fn ominous_priority_escalates_once_per_level_and_never_duplicates_the_sonic_warning() {
    let settings = Settings::default();
    let mut p = Specials::default();
    let kinds = [
        "sculk_sensor",
        "sculk_shrieker",
        "warden_heartbeat",
        "warden_presence",
    ];
    for (index, kind) in kinds.iter().enumerate() {
        let e = change(&event(&[]), |v| {
            v["world"] = json!({"biome":"deep_dark","ominous_sound_kind":kind,"ominous_sound_recent_ms":1001});
        });
        let now = 2000 + index as u64;
        observe(&mut p, &e, now);
        assert_eq!(
            p.ominous(&e, now, &settings).unwrap().leaf.unwrap().details["ominous_kind"],
            *kind
        );
        assert!(p.ominous(&e, now + 1, &settings).is_none());
        for lower in &kinds[..index] {
            let lower = change(&e, |v| v["world"]["ominous_sound_kind"] = (*lower).into());
            observe(&mut p, &lower, now + 1);
            assert!(p.ominous(&lower, now + 1, &settings).is_none());
        }
    }
    let sonic = change(&event(&[]), |v| {
        v["world"] = json!({"biome":"deep_dark","ominous_sound_kind":"warden_sonic_boom","ominous_sound_recent_ms":0});
    });
    let mut fresh = Specials::default();
    observe(&mut fresh, &sonic, 0);
    assert_eq!(
        fresh.sonic_boom(&sonic, 0, &settings).unwrap().kind,
        "warden_sonic_boom"
    );
    assert!(fresh.ominous(&sonic, 0, &settings).is_none());
}

#[test]
fn ominous_priority_preserves_the_selected_reactions_configured_cooldown() {
    let settings = Settings::merged(
        json!({
            "sculk_ominous_sound_comment_cooldown_ms": 5000,
            "ominous_sound_comment_cooldown_ms": 120000
        })
        .as_object()
        .unwrap(),
    )
    .unwrap();
    for high in ["warden_heartbeat", "warden_presence"] {
        for low in ["sculk_sensor", "sculk_shrieker"] {
            let mut p = Specials::default();
            let make = |kind| {
                change(&event(&[]), |v| {
                    v["world"] = json!({"biome":"deep_dark","ominous_sound_kind":kind,"ominous_sound_recent_ms":1001});
                })
            };
            let higher = make(high);
            p.observe(&higher, 1000, true, &settings);
            assert!(p.ominous(&higher, 1000, &settings).is_some());
            let lower = make(low);
            for now in [6000, 120999] {
                p.observe(&lower, now, true, &settings);
                assert!(p.ominous(&lower, now, &settings).is_none());
            }
            p.observe(&lower, 121000, true, &settings);
            assert!(p.ominous(&lower, 121000, &settings).is_some());
            p.observe(&higher, 121001, true, &settings);
            assert!(p.ominous(&higher, 121001, &settings).is_some());
        }
    }
}
