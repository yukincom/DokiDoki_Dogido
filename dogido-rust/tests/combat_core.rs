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

#[test]
fn quiet_needs_three_commands_and_resets_only_after_aftermath() {
    let mut engine = Engine::default();
    let e = event(json!([visual("zombie", "z", 4.0)]), json!([]));
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
    let e = event(json!([]), json!([sound("heard")]));
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
    let e = event(json!([]), json!([sound("heard")]));
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
    let e = event(json!([]), json!([sound("heard")]));
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
    let e = event(json!([visual("zombie", "z", 8.0)]), json!([]));
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
fn all_ordinary_hostiles_and_water_use_regular_fallback() {
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
        let e = event(json!([visual(kind, "enemy", 8.0)]), json!([]));
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
fn rain_does_not_consume_regular_warning_and_water_skeleton_gasps() {
    let mut engine = Engine::default();
    let rain = changed(
        &event(json!([visual("zombie", "z", 8.0)]), json!([])),
        |v| {
            v["world"]["time_phase"] = "day".into();
            v["world"]["weather"] = "rain".into();
        },
    );
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
