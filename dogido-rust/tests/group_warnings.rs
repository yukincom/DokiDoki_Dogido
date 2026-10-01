use dogido_rust::{
    events::GameEvent,
    threats::{Policy, Settings},
};
use serde_json::{Value, json};

fn changed(e: &GameEvent, update: impl FnOnce(&mut Value)) -> GameEvent {
    let mut v = serde_json::to_value(e).unwrap();
    update(&mut v);
    GameEvent::parse(v).unwrap()
}

fn scene(types: &[&str]) -> GameEvent {
    serde_json::from_value(json!({
        "schema_version":"2026-05-24", "adapter":"fixture", "observed_at":"2026-09-24T00:00:00Z",
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"name":"fixture"}, "world":{"time_phase":"night","biome":"plains"},
        "visual_threats":types.iter().enumerate().map(|(i,t)|json!({"type":t,"entity_id":format!("{t}{i}"),
            "distance":8.2,"direction":{"horizontal":"front","vertical":"same"}})).collect::<Vec<_>>()
    })).unwrap()
}

#[test]
fn moving_same_count_beyond_entry_range_does_not_interrupt() {
    let s = Settings::default();
    let e = scene(&["zombie", "zombie"]);
    let w = Policy::default().observe(&e, 0, false, &s).unwrap();
    let e = changed(&e, |v| {
        for t in v["visual_threats"].as_array_mut().unwrap() {
            t["distance"] = 18.0.into();
        }
    });
    assert!(w.applicable(&e, &s));
    let next = changed(&e, |v| {
        v["visual_threats"].as_array_mut().unwrap().pop();
    });
    assert!(!w.applicable(&next, &s));
    let refreshed = w.relocated(&next, &s, true).unwrap();
    assert_eq!(refreshed.kind, "visual_hostile");
    assert!(!refreshed.text.contains("2体"));
    assert!(refreshed.cue.is_none());
}

#[test]
fn overwhelmed_ties_preserve_selected_individual_but_follow_real_direction_changes() {
    let s = Settings::default();
    let e = changed(&scene(&["zombie", "zombie", "skeleton", "skeleton"]), |v| {
        v["visual_threats"][3]["direction"]["horizontal"] = "left".into();
    });
    let w = Policy::default().observe(&e, 0, false, &s).unwrap();
    assert_eq!(w.group_support, vec!["skeleton2"]);
    let e = changed(&e, |v| {
        v["visual_threats"].as_array_mut().unwrap().reverse();
    });
    assert!(w.applicable(&e, &s));
    let e = changed(&e, |v| {
        v["visual_threats"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|t| t["entity_id"] == "skeleton2")
            .unwrap()["direction"]["horizontal"] = "right".into();
    });
    assert!(!w.applicable(&e, &s));
    assert!(
        w.relocated(&e, &s, true)
            .unwrap()
            .text
            .contains("右にスケルトン")
    );
}

#[test]
fn generic_massive_speech_survives_timestamp_and_count_changes() {
    let s = Settings::default();
    let e = scene(&["zombie"; 9]);
    let w = Policy::default().observe(&e, 0, false, &s).unwrap();
    let next = changed(&scene(&["zombie"; 10]), |v| {
        v["sequence"] = 12.into();
    });
    assert!(w.applicable(&next, &s));
    assert!(!w.applicable(&scene(&["zombie"; 3]), &s));
}

#[test]
fn increase_is_not_reused_after_members_shrink_and_same_cue_is_not_replayed() {
    let s = Settings::default();
    let mut policy = Policy::default();
    policy.observe(&scene(&["zombie"]), 0, false, &s).unwrap();
    let w = policy
        .observe(&scene(&["zombie"; 3]), 7000, false, &s)
        .unwrap();
    assert_eq!(w.kind, "hostile_increase");
    let next = scene(&["zombie"; 2]);
    assert!(!w.applicable(&next, &s));
    let update = w.relocated(&next, &s, true).unwrap();
    assert_eq!(update.text, "ゾンビ2体おるで。");
    assert!(update.cue.is_none());
}
