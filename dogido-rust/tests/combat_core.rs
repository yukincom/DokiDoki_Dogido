//! 模擬観測と時計で、戦闘判断が選んだ発話・会話可否・中断指定を検査する。実音声・実機は対象外。
use dogido_rust::{
    combat::{
        core::Engine,
        model::{Mode, Settings},
    },
    events::GameEvent,
    threats,
};
use serde_json::{Value, json};

fn event(visual: Value, audio: Value) -> GameEvent {
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-09-25T00:00:00Z",
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"name":"fixture","dimension":"minecraft:overworld","health":20,"position":{"x":0,"z":0}},
        "world":{"sky_visible":true,"ceiling_height":20,"time_phase":"night","biome":"plains","local_light":10},
        "visual_threats":visual,"auditory_threats":audio})).unwrap()
}
fn visual(kind: &str, id: &str, d: f64) -> Value {
    json!({"type":kind,"entity_id":id,"distance":d,"direction":{"horizontal":"front","vertical":"same"}})
}
fn sound(id: &str) -> Value {
    json!({"label":"zombie","source_id":id,"spoken_name_allowed":true,"direction":{"horizontal":"right"},"distance_band":"close"})
}
fn changed(e: &GameEvent, f: impl FnOnce(&mut Value)) -> GameEvent {
    let mut v = serde_json::to_value(e).unwrap();
    f(&mut v);
    GameEvent::parse(v).unwrap()
}
// 本人が実際に交戦している試験だけに使う。視認や音だけの模擬観測とは区別する。
fn engaged(e: GameEvent) -> GameEvent {
    changed(&e, |v| v["combat"]["player_engaged"] = json!(true))
}

fn tick(engine: &mut Engine, e: &GameEvent, at: u64) -> dogido_rust::combat::core::Decision {
    engine.observe(
        e,
        at,
        true,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    )
}

// 見込み: 視認中は経過時間や方位で再通知せず、同種でも別IDなら知らせる。
// 合格: 模擬の完全観測・部分通知で各IDに短い発話1件、悲鳴・停止なし。完全観測から消えた後だけ再び対象になる。
#[test]
fn ranged_sighting_tracks_individuals_across_time_direction_and_partial_audio() {
    use dogido_rust::combat::{core::Decision, model::Scope};
    let notice = |d: Decision, id: &str| {
        assert!(d.chat_allowed && !d.stop_audio);
        let [speech] = &d.actions[..] else {
            panic!("expected one notice: {:?}", d.actions)
        };
        assert_eq!(speech.kind, "ranged_sighting");
        assert!(matches!(&speech.scope, Scope::Visual(ids) if ids == &[id]));
        assert_eq!(speech.text, "スケルトンおるで。");
        assert!(!speech.interrupt && speech.cue_id.is_none() && speech.leaf.is_none());
    };
    let mut engine = Engine::default();
    let a = event(json!([visual("skeleton", "a", 2.0)]), json!([]));
    notice(tick(&mut engine, &a, 0), "a");
    let turned = changed(&a, |v| {
        v["visual_threats"][0]["direction"]["horizontal"] = "back".into()
    });
    let partial = event(json!([]), json!([sound("elsewhere")]));
    assert!(
        engine
            .observe(
                &partial,
                500,
                false,
                false,
                &Settings::default(),
                &threats::Settings::default()
            )
            .actions
            .is_empty()
    );
    assert!(tick(&mut engine, &turned, 180_000).actions.is_empty());
    let two = changed(&turned, |v| {
        v["visual_threats"]
            .as_array_mut()
            .unwrap()
            .push(visual("skeleton", "b", 6.0))
    });
    notice(tick(&mut engine, &two, 180_001), "b");
    assert!(tick(&mut engine, &two, 360_000).actions.is_empty());
    let only_b = event(json!([visual("skeleton", "b", 6.0)]), json!([]));
    assert!(tick(&mut engine, &only_b, 361_000).actions.is_empty());
    notice(tick(&mut engine, &two, 362_000), "a");
}

// 見込み: 導火が先なら遠隔敵の初回を消費しない。交戦中も同じ遠隔敵だけを時間で読み直さない。
// 合格: 模擬戦闘で導火→ガストの一言→発話なし。IDのない視認・音だけでは初回通知を作らない。
#[test]
fn ranged_sighting_yields_to_fuse_and_requires_a_visual_id() {
    let mut engine = Engine::default();
    let ghast = engaged(event(json!([visual("ghast", "g", 12.0)]), json!([])));
    let fused = changed(&ghast, |v| {
        let mut creeper = visual("creeper", "c", 4.0);
        creeper["fuse_active"] = true.into();
        v["visual_threats"].as_array_mut().unwrap().push(creeper);
    });
    assert_eq!(tick(&mut engine, &fused, 0).actions[0].kind, "creeper_fuse");
    let d = tick(&mut engine, &ghast, 1000);
    assert_eq!(d.actions.len(), 1);
    assert_eq!(d.actions[0].text, "ガストおるで。");
    assert!(!d.stop_audio);
    assert!(tick(&mut engine, &ghast, 180_000).actions.is_empty());
    let no_id = changed(&ghast, |v| {
        v["visual_threats"][0]["entity_id"] = Value::Null
    });
    assert!(tick(&mut engine, &no_id, 181_000).actions.is_empty());
    let heard = event(json!([]), json!([{"label":"skeleton","source_id":"s"}]));
    assert!(tick(&mut engine, &heard, 182_000).actions.is_empty());
}

#[test]
fn burning_start_waits_for_current_speech_and_survives_partial_events() {
    let mut engine = Engine::default();
    let s = Settings::default();
    let ws = threats::Settings::default();
    let seen = event(json!([visual("skeleton", "s", 13.54)]), json!([]));
    assert!(
        tick(&mut engine, &seen, 0)
            .actions
            .iter()
            .any(|a| a.kind == "ranged_sighting")
    );
    let burning = changed(&seen, |v| v["visual_threats"][0]["on_fire"] = true.into());
    // 実機で発見音声の終了133ms前に燃焼が始まった順序を再現する。
    assert!(
        engine
            .observe(&burning, 4954, true, true, &s, &ws)
            .actions
            .is_empty()
    );
    let partial = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = "hostile_audio_detected".into();
        v["event"]["source_kind"] = "auditory".into();
    });
    assert!(
        engine
            .observe(&partial, 5000, false, true, &s, &ws)
            .actions
            .is_empty()
    );
    let ready = tick(&mut engine, &burning, 5087);
    let reaction = ready
        .actions
        .iter()
        .find(|a| a.kind == "newly_burning_visual")
        .expect("先の台詞が終わったら、まだ視認中で燃えている個体へ一度反応する");
    assert_eq!(
        reaction.leaf.as_ref().unwrap().details["hostile"],
        "スケルトン"
    );
    assert!(!ready.stop_audio);
    for at in [6000, 26000] {
        assert!(
            tick(&mut engine, &burning, at)
                .actions
                .iter()
                .all(|a| a.kind != "newly_burning_visual")
        );
    }
}

#[test]
fn pending_burning_is_discarded_when_extinguished_lost_or_dimension_changes() {
    let s = Settings::default();
    let ws = threats::Settings::default();
    for reason in ["extinguished", "lost", "dimension"] {
        let mut engine = Engine::default();
        let seen = event(json!([visual("skeleton", "s", 13.54)]), json!([]));
        tick(&mut engine, &seen, 0);
        let burning = changed(&seen, |v| v["visual_threats"][0]["on_fire"] = true.into());
        engine.observe(&burning, 4954, true, true, &s, &ws);
        let gone = match reason {
            "extinguished" => seen.clone(),
            "lost" => event(json!([]), json!([])),
            _ => changed(&event(json!([]), json!([])), |v| {
                v["player"]["dimension"] = "minecraft:the_nether".into();
            }),
        };
        engine.observe(&gone, 5000, true, true, &s, &ws);
        for at in [5087, 26000] {
            assert!(
                tick(&mut engine, &gone, at)
                    .actions
                    .iter()
                    .all(|a| a.kind != "newly_burning_visual"),
                "{reason}"
            );
        }
    }
}

#[test]
fn pending_burning_keeps_fuse_priority_and_existing_cooldown() {
    let mut engine = Engine::default();
    let fire = changed(
        &event(json!([visual("skeleton", "s", 13.54)]), json!([])),
        |v| {
            v["visual_threats"][0]["on_fire"] = true.into();
        },
    );
    let fuse = changed(&fire, |v| {
        let mut creeper = visual("creeper", "c", 5.0);
        creeper["fuse_active"] = true.into();
        v["visual_threats"].as_array_mut().unwrap().push(creeper);
    });
    assert_eq!(tick(&mut engine, &fuse, 0).actions[0].kind, "creeper_fuse");
    assert!(
        tick(&mut engine, &fire, 2000)
            .actions
            .iter()
            .any(|a| a.kind == "newly_burning_visual")
    );
    let extinguished = changed(&fire, |v| v["visual_threats"][0]["on_fire"] = false.into());
    tick(&mut engine, &extinguished, 3000);
    assert!(
        tick(&mut engine, &fire, 4000)
            .actions
            .iter()
            .all(|a| a.kind != "newly_burning_visual")
    );
    assert!(
        tick(&mut engine, &fire, 22000)
            .actions
            .iter()
            .any(|a| a.kind == "newly_burning_visual")
    );
    assert!(
        tick(&mut engine, &fire, 43000)
            .actions
            .iter()
            .all(|a| a.kind != "newly_burning_visual")
    );
}

#[test]
fn quiet_needs_three_commands_and_resets_only_after_aftermath() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([visual("zombie", "z", 4.0)]), json!([])));
    assert_eq!(tick(&mut engine, &e, 0).mode, Mode::Panic);
    let s = Settings::default();
    let ws = threats::Settings::default();
    for at in [2000, 4000] {
        assert_eq!(
            engine.input(&e, "静かにして", at, &s, &ws).unwrap().mode,
            Mode::Panic
        );
    }
    let d = engine.input(&e, "うるさい", 6000, &s, &ws).unwrap();
    assert_eq!(d.mode, Mode::SuppressedPanic);
    assert_eq!(d.actions[0].cue_id, Some("suppressed_gasp"));
    assert_eq!(tick(&mut engine, &e, 15000).mode, Mode::SuppressedPanic);
    let empty = event(json!([]), json!([]));
    assert_eq!(tick(&mut engine, &empty, 16000).mode, Mode::Normal);
    assert_eq!(engine.shut_up_count, 3);
    let end = changed(&empty, |v| v["event"]["name"] = "combat_ended".into());
    assert_eq!(tick(&mut engine, &end, 17000).mode, Mode::Aftermath);
    assert_eq!(tick(&mut engine, &empty, 24999).mode, Mode::Aftermath);
    assert_eq!(tick(&mut engine, &empty, 25000).mode, Mode::Normal);
    assert_eq!(engine.shut_up_count, 0);
}
#[test]
fn sound_partial_does_not_clear_visual_or_rearm_fuse() {
    let mut engine = Engine::default();
    let s = Settings::default();
    let ws = threats::Settings::default();
    let e = changed(
        &event(json!([visual("creeper", "c", 5.0)]), json!([])),
        |v| v["visual_threats"][0]["fuse_active"] = true.into(),
    );
    assert_eq!(tick(&mut engine, &e, 0).actions[0].kind, "creeper_fuse");
    let partial = changed(&event(json!([]), json!([sound("heard")])), |v| {
        v["event"]["name"] = "hostile_audio_detected".into();
        v["event"]["source_kind"] = "auditory".into();
    });
    let d = engine.observe(&partial, 2000, false, true, &s, &ws);
    assert_eq!(d.mode, Mode::Panic);
    assert!(!d.chat_allowed);
    assert!(
        tick(&mut engine, &e, 3000)
            .actions
            .iter()
            .all(|a| a.kind != "creeper_fuse")
    );
}
#[test]
fn low_health_rearms_above_five_or_missing_health() {
    let mut engine = Engine::default();
    let e = changed(
        &event(json!([visual("zombie", "z", 8.0)]), json!([])),
        |v| v["player"]["health"] = 5.into(),
    );
    assert_eq!(tick(&mut engine, &e, 0).actions[0].kind, "low_health");
    assert!(
        tick(&mut engine, &e, 2000)
            .actions
            .iter()
            .all(|a| a.kind != "low_health")
    );
    let unknown = changed(&e, |v| v["player"]["health"] = Value::Null);
    tick(&mut engine, &unknown, 3000);
    assert_eq!(tick(&mut engine, &e, 4000).actions[0].kind, "low_health");
}
#[test]
fn auditory_first_fourth_tenth_and_sixty_second_reset() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([]), json!([sound("heard")])));
    for n in 1..=11 {
        let d = tick(&mut engine, &e, n * 2000);
        assert_eq!(
            d.actions.iter().any(|a| a.kind == "auditory_hostile"),
            matches!(n, 1 | 4 | 10),
            "{n}"
        );
        if n == 10 {
            assert!(d.actions[0].text.contains("こっち来んよな"));
        }
    }
    let d = tick(&mut engine, &e, 82000);
    assert!(d.actions[0].text.contains("右でゾンビの声"));
}
#[test]
fn auditory_busy_defers_latest_milestone_without_marking_visual_seen() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([]), json!([sound("heard")])));
    assert!(
        engine
            .observe(
                &e,
                0,
                true,
                true,
                &Settings::default(),
                &threats::Settings::default()
            )
            .actions
            .is_empty()
    );
    assert!(
        tick(&mut engine, &e, 1000).actions[0]
            .text
            .contains("ゾンビの声")
    );
}
#[test]
fn auditory_chasing_uses_horizontal_displacement_and_name_permission() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([]), json!([sound("heard")])));
    for n in 1..10 {
        tick(&mut engine, &e, n * 2000);
    }
    let moved = changed(&e, |v| {
        v["player"]["position"]["x"] = 4.6.into();
        v["auditory_threats"][0]["spoken_name_allowed"] = false.into();
    });
    let d = tick(&mut engine, &moved, 20000);
    assert_eq!(d.actions[0].text, "やっこさん、まだ追ってきよるよ！");
}
#[test]
fn fused_creeper_beats_suppressed_breath() {
    let mut engine = Engine::default();
    let e = event(json!([visual("creeper", "c", 5.0)]), json!([]));
    tick(&mut engine, &e, 0);
    for at in [2000, 4000, 6000] {
        engine.input(
            &e,
            "黙れ",
            at,
            &Settings::default(),
            &threats::Settings::default(),
        );
    }
    let fuse = changed(&e, |v| v["visual_threats"][0]["fuse_active"] = true.into());
    let d = tick(&mut engine, &fuse, 8000);
    assert_eq!(d.actions[0].kind, "creeper_fuse");
    assert!(d.actions[0].interrupt);
}
#[test]
fn skeleton_scream_does_not_consume_low_health_warning() {
    let mut engine = Engine::default();
    let e = changed(
        &event(json!([visual("skeleton", "s", 8.0)]), json!([])),
        |v| {
            v["combat"] = json!({"recent_damage_ms":0});
            v["player"]["health"] = 5.into();
        },
    );
    assert_eq!(
        tick(&mut engine, &e, 0).actions[0].kind,
        "skeleton_damage_ambush"
    );
    let later = changed(&e, |v| v["combat"]["recent_damage_ms"] = 1500.into());
    assert!(
        tick(&mut engine, &later, 1500)
            .actions
            .iter()
            .any(|a| a.kind == "low_health")
    );
}
#[test]
fn stalled_tracks_individuals_and_partial_does_not_reset_it() {
    let mut engine = Engine::default();
    let e = engaged(event(json!([visual("zombie", "z", 8.0)]), json!([])));
    tick(&mut engine, &e, 0);
    let partial = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = "ambient_mob_detected".into()
    });
    engine.observe(
        &partial,
        30000,
        false,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert_eq!(
        tick(&mut engine, &e, 60000).actions[0].kind,
        "stalled_visual"
    );
    let new = changed(&e, |v| v["visual_threats"][0]["entity_id"] = "z2".into());
    assert!(
        tick(&mut engine, &new, 62000)
            .actions
            .iter()
            .all(|a| a.kind != "stalled_visual")
    );
}
#[test]
fn dark_alert_respects_light_and_submersion_without_creating_combat() {
    let mut engine = Engine::default();
    let e = changed(&event(json!([]), json!([])), |v| {
        v["world"]["danger_darkness_score"] = 0.9.into();
        v["world"]["local_light"] = 4.into();
    });
    assert_eq!(tick(&mut engine, &e, 0).mode, Mode::Normal);
    let dark = changed(&e, |v| v["world"]["local_light"] = 3.into());
    assert_eq!(tick(&mut engine, &dark, 1000).mode, Mode::Alert);
    let water = changed(&dark, |v| v["world"]["is_submerged"] = true.into());
    assert_eq!(tick(&mut engine, &water, 2000).mode, Mode::Normal);
}
#[test]
fn engaged_ordinary_hostiles_and_water_use_regular_fallback() {
    for kind in [
        "witch",
        "pillager",
        "endermite",
        "silverfish",
        "slime",
        "magma_cube",
        "drowned",
        "enderman",
    ] {
        let mut engine = Engine::default();
        let e = engaged(event(json!([visual(kind, "enemy", 8.0)]), json!([])));
        assert!(
            tick(&mut engine, &e, 0)
                .actions
                .iter()
                .any(|a| a.text.contains(&dogido_rust::combat::model::label(kind))),
            "{kind}"
        );
    }
}

#[test]
fn busy_leaf_is_preempted_by_new_rear_enemy_only_once() {
    let mut engine = Engine::default();
    tick(&mut engine, &event(json!([]), json!([])), 0);
    let rear = changed(
        &event(json!([visual("zombie", "z", 2.0)]), json!([])),
        |v| {
            v["event"]["name"] = "threat_approaching".into();
            v["event"]["source_kind"] = "visual".into();
            v["visual_threats"][0]["direction"]["horizontal"] = "back".into();
        },
    );
    let d = engine.observe(
        &rear,
        2000,
        true,
        true,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert!(d.stop_audio);
    assert!(d.actions.iter().any(|a| a.kind.starts_with("ushiro_")));
    assert!(
        engine
            .observe(
                &rear,
                4000,
                true,
                true,
                &Settings::default(),
                &threats::Settings::default()
            )
            .actions
            .is_empty()
    );
}
#[test]
fn stale_visuals_and_transient_sonic_observation_are_not_refreshed_by_partial() {
    let mut engine = Engine::default();
    let e = changed(
        &event(json!([visual("warden", "w", 5.0)]), json!([])),
        |v| {
            v["world"]["ominous_sound_kind"] = "warden_sonic_boom".into();
            v["world"]["ominous_sound_recent_ms"] = 0.into();
        },
    );
    let d = tick(&mut engine, &e, 0);
    assert_eq!(d.actions[0].kind, "warden_sonic_boom");
    assert!(d.actions.iter().any(|a| a.kind == "boss_visual"));
    let partial = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = "ambient_mob_detected".into()
    });
    let d = engine.observe(
        &partial,
        5000,
        false,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert!(d.actions.iter().all(|a| a.kind != "warden_sonic_boom"));
    let d = engine.observe(
        &partial,
        10001,
        false,
        false,
        &Settings::default(),
        &threats::Settings::default(),
    );
    assert_eq!(d.mode, Mode::Normal);
    assert!(!d.chat_allowed);
}
#[test]
fn engaged_rain_preserves_regular_warning_and_water_skeleton_gasps() {
    let mut engine = Engine::default();
    let rain = changed(
        &event(json!([visual("zombie", "z", 8.0)]), json!([])),
        |v| {
            v["world"]["time_phase"] = "day".into();
            v["world"]["weather"] = "rain".into();
        },
    );
    let rain = engaged(rain);
    assert!(
        tick(&mut engine, &rain, 0)
            .actions
            .iter()
            .any(|a| a.kind == "daylight_rain")
    );
    assert!(
        tick(&mut engine, &rain, 2000)
            .actions
            .iter()
            .any(|a| a.kind == "visual_hostile")
    );
    let mut engine = Engine::default();
    let water = changed(
        &event(json!([visual("skeleton", "s", 8.0)]), json!([])),
        |v| {
            v["world"]["time_phase"] = "day".into();
            v["visual_threats"][0]["in_water"] = true.into();
        },
    );
    let d = tick(&mut engine, &water, 0);
    assert_eq!(d.actions[0].cue_id, Some("spot_hostile_gasp"));
    assert_eq!(d.actions[1].kind, "daylight_water");
}

#[test]
fn darkness_keeps_chat_allowed_on_complete_and_passive_partial_observations() {
    let mut engine = Engine::default();
    let settings = Settings::default();
    let warnings = threats::Settings::default();
    let dark = changed(&event(json!([]), json!([])), |v| {
        v["world"]["local_light"] = json!(3);
        v["world"]["danger_darkness_score"] = json!(0.97);
        v["combat"] = json!({"combat_active_hint":false,"recent_damage_ms":356650});
    });
    for (at, complete) in [(0, true), (1000, false), (2000, false), (3000, true)] {
        let observed = if complete {
            dark.clone()
        } else {
            changed(&dark, |v| {
                v["event"]["name"] = json!("ambient_mob_detected");
                v["event"]["source_kind"] = json!("visual");
                v["passive_mobs"] = json!([{"type":"dogido","distance":2.0}]);
            })
        };
        let decision = engine.observe(&observed, at, complete, true, &settings, &warnings);
        assert_eq!(decision.mode, Mode::Alert);
        assert!(decision.chat_allowed, "complete={complete}, at={at}");
        assert!(!decision.stop_audio);
        assert!(decision.actions.is_empty());
    }
    for danger in ["visual", "auditory", "damage", "hint"] {
        let mut engine = Engine::default();
        engine.observe(&dark, 0, true, true, &settings, &warnings);
        let threat = changed(&dark, |v| match danger {
            "visual" => v["visual_threats"] = json!([visual("zombie", "enemy", 12.0)]),
            "auditory" => v["auditory_threats"] = json!([sound("enemy")]),
            "damage" => v["combat"]["recent_damage_ms"] = json!(0),
            _ => v["combat"]["combat_active_hint"] = json!(true),
        });
        assert_eq!(
            engine
                .observe(&threat, 1000, false, true, &settings, &warnings)
                .chat_allowed,
            danger != "damage",
            "{danger}"
        );
    }
}

// 見込み: 普通の敵を見聞きしただけなら、個体数でpanicになっても会話が続く。
// 合格: 実戦闘エンジンへ完全観測・音の部分通知・離脱を渡し、警告発話・音声停止・会話保留が全てない。
#[test]
fn ordinary_sight_count_and_sound_stay_observations_until_engagement() {
    let mut engine = Engine::default();
    let one = changed(
        &event(json!([visual("zombie", "z1", 2.)]), json!([])),
        |v| {
            v["combat"] = json!({"combat_active_hint":true, "player_engaged":false});
        },
    );
    let two = changed(&one, |v| {
        v["visual_threats"]
            .as_array_mut()
            .unwrap()
            .push(visual("zombie", "z2", 6.))
    });
    let heard = changed(&one, |v| {
        v["event"]["name"] = json!("hostile_audio_detected");
        v["visual_threats"] = json!([]);
        v["auditory_threats"] = json!([sound("unseen")]);
    });
    let clear = changed(&event(json!([]), json!([])), |v| {
        v["event"]["name"] = json!("combat_ended")
    });
    let rainy = changed(&one, |v| {
        v["world"]["time_phase"] = json!("day");
        v["world"]["weather"] = json!("rain");
    });
    let behind = changed(&one, |v| {
        v["visual_threats"][0]["direction"]["horizontal"] = json!("back")
    });
    for (i, (frame, full)) in [
        (&one, true),
        (&behind, true),
        (&rainy, true),
        (&two, true),
        (&heard, false),
        (&clear, true),
        (&clear, true),
    ]
    .into_iter()
    .enumerate()
    {
        let d = engine.observe(
            frame,
            i as u64 * 100,
            full,
            false,
            &Settings::default(),
            &threats::Settings::default(),
        );
        assert!(
            d.chat_allowed && !d.stop_audio && d.actions.is_empty(),
            "{i}: {d:?}"
        );
    }
}
