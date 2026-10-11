//! 共有fixtureから通信契約・主体分離・鮮度・前回比較を検査する。モデルやゲーム実行は使わない。
use super::{contracts::*, *};
use serde_json::{Value, json};
use std::time::{Duration, Instant};
const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const WORLD: &str = "minecraft:overworld";
pub(crate) fn fixture() -> Value {
    serde_json::from_str(include_str!("../../fixtures/body-observation-v1.json")).unwrap()
}
fn observation(tick: u64, z: f64) -> Observation {
    let mut v = fixture();
    v["sample"]["server_tick"] = json!(tick);
    v["sample"]["snapshot_revision"] = json!(tick * 2);
    v["sample"]["player_pose"]["position"]["z"] = json!(z);
    serde_json::from_value(v).unwrap()
}
fn accept(s: &mut State, o: &Observation, at: Instant) {
    s.observe(Some(o), Some(OWNER), Some(WORLD), at, Some(0));
}
#[test]
fn body_wire_rejects_missing_wrong_version_unknown_field_and_invalid_pose() {
    let o: Observation = serde_json::from_value(fixture()).unwrap();
    o.validate().unwrap();
    for (pointer, value) in [
        ("/version", json!(2)),
        ("/sample/self_pose/yaw", json!(180)),
        ("/sample/context/owner_id", json!("wrong")),
        ("/sample/self_pose/position/x", json!(30_000_001)),
    ] {
        let mut v = fixture();
        *v.pointer_mut(pointer).unwrap() = value;
        assert!(
            serde_json::from_value::<Observation>(v)
                .unwrap()
                .validate()
                .is_err()
        );
    }
    for pointer in ["sample", "basis", "age_ms"] {
        let mut v = fixture();
        v.as_object_mut().unwrap().remove(pointer);
        assert!(serde_json::from_value::<Observation>(v).is_err());
    }
    let mut v = fixture();
    v["unexpected"] = json!(true);
    assert!(serde_json::from_value::<Observation>(v).is_err());
    let mut v = fixture();
    v["sample"]["player_pose"]["position"]["x"] = json!("NaN");
    assert!(serde_json::from_value::<Observation>(v).is_err());
}
#[test]
fn body_retains_raw_previous_without_prelabeling_departure() {
    let now = Instant::now();
    let mut s = State::default();
    accept(&mut s, &observation(100, 23.0), now);
    assert!(s.project(now).unwrap()["previous"].is_null());
    accept(
        &mut s,
        &observation(110, 28.0),
        now + Duration::from_millis(500),
    );
    let v = s.project(now + Duration::from_millis(500)).unwrap();
    assert_eq!(v["current"]["player"]["position"]["z"], 28.0);
    assert_eq!(v["previous"]["player"]["position"]["z"], 23.0);
    assert_eq!(v["sample_interval_ticks"], 10);
    for hidden in [
        "distance",
        "forward",
        "right",
        "moved_away",
        OWNER,
        "snapshot_revision",
    ] {
        assert!(!v.to_string().contains(hidden));
    }
}
#[test]
fn body_replay_partial_and_late_packet_do_not_refresh_age() {
    let now = Instant::now();
    let mut s = State::default();
    let o = observation(100, 23.0);
    accept(&mut s, &o, now);
    accept(&mut s, &o, now + Duration::from_secs(2));
    s.observe(
        None,
        Some(OWNER),
        Some(WORLD),
        now + Duration::from_secs(2),
        Some(0),
    );
    assert_eq!(
        s.project(now + Duration::from_secs(3)).unwrap()["status"],
        "unavailable"
    );
    accept(
        &mut s,
        &observation(110, 28.0),
        now + Duration::from_secs(4),
    );
    assert!(s.project(now + Duration::from_secs(4)).unwrap()["previous"].is_null());
}
#[test]
fn body_wrong_owner_world_and_unavailable_clear_comparison() {
    let now = Instant::now();
    let o = observation(100, 23.0);
    for owner in [None, Some("22222222-2222-4222-8222-222222222222")] {
        let mut s = State::default();
        s.observe(Some(&o), owner, Some(WORLD), now, Some(0));
        assert_eq!(s.project(now).unwrap()["reason"], "owner_mismatch");
    }
    let mut s = State::default();
    accept(&mut s, &o, now);
    s.observe(
        None,
        Some(OWNER),
        Some("minecraft:the_nether"),
        now,
        Some(0),
    );
    assert_eq!(s.project(now).unwrap()["reason"], "dimension_mismatch");
    accept(&mut s, &observation(110, 25.0), now);
    let unavailable = Observation::Unavailable {
        version: 1,
        reason: "unloaded".into(),
    };
    accept(&mut s, &unavailable, now);
    accept(&mut s, &observation(110, 25.0), now);
    assert_eq!(s.project(now).unwrap()["reason"], "unloaded");
    accept(&mut s, &observation(120, 29.0), now);
    assert!(s.project(now).unwrap()["previous"].is_null());
}
#[test]
fn body_epoch_reset_retired_epoch_and_transport_delay() {
    let now = Instant::now();
    let mut s = State::default();
    accept(&mut s, &observation(100, 23.0), now);
    let mut v = fixture();
    v["sample"]["context"]["epoch"] = json!("44444444-4444-4444-8444-444444444444");
    v["sample"]["server_tick"] = json!(1);
    v["sample"]["snapshot_revision"] = json!(1);
    let new = serde_json::from_value(v).unwrap();
    accept(&mut s, &new, now);
    assert!(s.project(now).unwrap()["previous"].is_null());
    accept(&mut s, &observation(120, 29.0), now);
    assert_eq!(
        s.project(now).unwrap()["current"]["player"]["position"]["z"],
        23.0
    );
    let mut fresh = State::default();
    fresh.observe(Some(&new), Some(OWNER), Some(WORLD), now, Some(2990));
    assert_eq!(fresh.project(now).unwrap()["reason"], "stale");
}
#[test]
fn body_evaluation_rotates_on_self_yaw_and_keeps_height() {
    let Observation::Observed { mut sample, .. } = observation(100, 23.0) else {
        panic!()
    };
    let a = evaluation::relation(&sample);
    assert_eq!(a.distance, 3.0);
    assert_eq!(a.forward, 3.0);
    assert_eq!(a.right, 0.0);
    sample.self_pose.yaw = 90.0;
    let b = evaluation::relation(&sample);
    assert!(b.forward.abs() < 1e-10);
    assert_eq!(b.right, -3.0);
    sample.player_pose.position.y += 4.0;
    assert_eq!(evaluation::relation(&sample).distance, 5.0);
}

#[test]
fn body_generation_limit_does_not_reopen_retired_or_last_context() {
    let now = Instant::now();
    let mut s = State::default();
    for index in 1..=258_u128 {
        let mut v = fixture();
        v["sample"]["context"]["epoch"] = json!(uuid::Uuid::from_u128(index).to_string());
        let o = serde_json::from_value(v).unwrap();
        accept(&mut s, &o, now);
    }
    assert_eq!(s.project(now).unwrap()["reason"], "context_limit");
    let mut v = fixture();
    v["sample"]["context"]["epoch"] = json!(uuid::Uuid::from_u128(257).to_string());
    v["sample"]["server_tick"] = json!(200);
    v["sample"]["snapshot_revision"] = json!(400);
    accept(&mut s, &serde_json::from_value(v).unwrap(), now);
    assert_eq!(s.project(now).unwrap()["reason"], "context_limit");
}
