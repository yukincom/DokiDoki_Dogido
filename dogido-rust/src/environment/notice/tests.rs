use super::*;
use crate::{
    conversation_observation::{self, State},
    events::GameEvent,
};
fn frame(mobs: &[&str], blocks: &[&str], biome: &str) -> Context {
    let e = GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture",
        "observed_at":"2026-10-08T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","position":{"x":0,"y":64,"z":0}},
        "world":{"biome":biome,"sky_visible":true,"time_phase":"day","weather":"clear"},
        "nearby_composition":{"mobs":mobs,"blocks":blocks,"mobs_truncated":false,"blocks_truncated":false},
        "smell_observation":{"status":"none"}})).unwrap();
    conversation_observation::project(&e, None, 32.0, 7.0, 5000).unwrap()
}
#[test]
fn kinds_not_instances_order_or_count_and_all_biomes_are_observed() {
    let a = frame(
        &["cow", "pig", "cow"],
        &["minecraft:stone", "minecraft:dirt"],
        "plains",
    );
    let b = frame(
        &["pig", "cow"],
        &["minecraft:dirt", "minecraft:stone"],
        "plains",
    );
    assert!(changes(&snapshot(&a), &b).is_empty());
    for biome in ["forest", "desert", "dark_forest", "plains"] {
        let c = frame(&[], &[], biome);
        assert_eq!(c.observations["location_biome"]["id"], biome);
    }
    let c = frame(&["sheep"], &["minecraft:oak_log"], "desert");
    let delta = changes(&snapshot(&a), &c);
    for kind in ["nearby_mob_types", "nearby_block_types", "location_biome"] {
        assert!(delta.iter().any(|c| c.kind == kind));
    }
}
#[test]
fn periodic_samples_compare_once_and_do_not_carry_failed_or_skipped_changes() {
    let mut state = State::default();
    state.observe(frame(&["cow"], &["minecraft:stone"], "plains"));
    state.sample_notice(1000, 180000, true);
    assert!(state.noticing_context().is_none());
    state.observe(frame(&["sheep"], &["minecraft:stone"], "plains"));
    state.sample_notice(180999, 180000, true);
    assert!(state.noticing_context().is_none());
    state.sample_notice(181000, 180000, true);
    let offered = state.noticing_context().unwrap();
    assert_eq!(offered.changes.len(), 1);
    assert_eq!(offered.changes[0].kind, "nearby_mob_types");
    // 発話できない回でも、次の同じ表で古い変化を繰り返さない。
    state.finish_notice_opportunity();
    state.sample_notice(361000, 180000, true);
    assert!(state.noticing_context().is_none());
    state.observe(frame(&["sheep"], &["minecraft:oak_log"], "plains"));
    state.sample_notice(541000, 180000, true);
    let current = state.noticing_context().unwrap();
    assert_eq!(current.changes.len(), 1);
    assert_eq!(current.changes[0].kind, "nearby_block_types");
    // 周期間の出入りと部分通知は次回の比較基準にならない。
    state.observe(frame(&["pig"], &[], "forest"));
    state.sample_notice(541001, 180000, false);
    assert!(state.noticing_context().is_none());
    state.observe(frame(&["sheep"], &["minecraft:oak_log"], "plains"));
    state.sample_notice(721000, 180000, true);
    assert!(state.noticing_context().is_none());
}
#[test]
fn same_smell_direction_is_not_a_new_arrival() {
    let mut before = frame(&[], &[], "plains");
    before.observations["smell"] = json!({"status":"present","smell_id":"zombie","direction_estimate":{"cardinal":"east"},"description":"東"});
    let mut after = before.clone();
    after.observations["smell"]["direction_estimate"]["cardinal"] = json!("west");
    after.observations["smell"]["description"] = json!("西");
    assert!(changes(&snapshot(&before), &after).is_empty());
    after.observations["smell"] = json!({"status":"suppressed","suppression_reason":"rain"});
    assert!(
        changes(&snapshot(&before), &after)
            .iter()
            .any(|c| c.kind == "smell")
    );
}
#[test]
fn visible_blocks_never_create_a_smell_and_unknown_differs_from_empty() {
    let c = frame(
        &[],
        &["minecraft:sweet_berry_bush", "minecraft:bee_nest"],
        "forest",
    );
    assert_eq!(c.observations["smell"]["status"], "none");
    let mut unknown = c.clone();
    unknown.observations["nearby_block_types"] = Value::Null;
    assert!(
        changes(&snapshot(&unknown), &c)
            .iter()
            .any(|c| c.kind == "nearby_block_types")
    );
}
#[test]
fn actual_precipitation_and_underground_biome_stay_separate() {
    for (y, expected) in [(64, "rain"), (250, "snow")] {
        let mut e = json!({"schema_version":"2026-05-24","adapter":"fixture","observed_at":"2026-10-08T00:00:00Z","event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"}});
        e["player"] = json!({"dimension":"minecraft:overworld","position":{"y":y}});
        e["world"] = json!({"biome":"taiga","weather":"rain","sky_visible":true});
        let c = conversation_observation::project(
            &GameEvent::parse(e.clone()).unwrap(),
            None,
            32.0,
            7.0,
            5000,
        )
        .unwrap();
        assert_eq!(c.observations["weather"]["precipitation_kind"], expected);
        e["world"]["sky_visible"] = json!(false);
        e["world"]["overhead_cover_type"] = json!("stone");
        let c =
            conversation_observation::project(&GameEvent::parse(e).unwrap(), None, 32.0, 7.0, 5000)
                .unwrap();
        assert_eq!(c.observations["location_biome"]["id"], "taiga");
        assert_eq!(c.observations["biome_scene_visible"], false);
        assert!(c.observations["weather"].is_null());
    }
}

#[test]
fn partial_notifications_never_start_or_advance_periodic_sampling() {
    let mut state = State::default();
    state.observe(frame(&[], &[], "plains"));
    state.sample_notice(0, 180000, false);
    state.observe(frame(&["cow"], &[], "plains"));
    state.sample_notice(180000, 180000, true);
    assert!(state.noticing_context().is_none()); // 初めての全snapshot
    state.observe(frame(&["sheep"], &[], "plains"));
    state.sample_notice(360000, 180000, false);
    assert!(state.noticing_context().is_none());
    state.sample_notice(360001, 180000, true);
    assert!(
        state
            .noticing_context()
            .unwrap()
            .changes
            .iter()
            .any(|c| c.kind == "nearby_mob_types")
    );
}
