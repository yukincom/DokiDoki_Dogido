//! 共有fixtureから通信契約・主体分離・鮮度・前回比較を検査する。モデルやゲーム実行は使わない。
use super::{contracts::*, *};
use serde_json::{Value, json};
use std::time::{Duration, Instant};
const OWNER: &str = "11111111-1111-4111-8111-111111111111";
const WORLD: &str = "minecraft:overworld";
pub(crate) fn fixture() -> Value {
    serde_json::from_str(include_str!("../../fixtures/body-observation-v3.json")).unwrap()
}
fn observation(tick: u64, z: f64) -> Observation {
    let mut v = fixture();
    v["sample"]["server_tick"] = json!(tick);
    v["sample"]["senses"]["source_server_tick"] = json!(tick);
    v["sample"]["snapshot_revision"] = json!(tick * 2);
    v["sample"]["player_pose"]["position"]["z"] = json!(z);
    serde_json::from_value(v).unwrap()
}
fn accept(s: &mut State, o: &Observation, at: Instant) {
    s.observe(Some(o), Some(OWNER), Some(WORLD), at, Some(0));
}
#[test]
fn diagnostic_sample_preserves_identity_age_and_does_not_refresh_stale_pose() {
    let now = Instant::now();
    let mut state = State::default();
    assert_eq!(state.diagnostic_sample(now)["status"], "unavailable");
    let mut input = observation(100, 23.0);
    if let Observation::Observed { age_ms, .. } = &mut input {
        *age_ms = 200;
    }
    accept(&mut state, &input, now);
    let later = now + Duration::from_millis(100);
    let before = state.project(later);
    let sample = state.diagnostic_sample(later);
    assert_eq!(sample["age_ms"], 300);
    assert_eq!(sample["context"], fixture()["sample"]["context"]);
    assert_eq!(sample["server_tick"], 100);
    assert_eq!(sample["snapshot_revision"], 200);
    assert_eq!(sample["player_pose"]["position"]["z"], 23.0);
    assert_eq!(state.project(later), before);
    accept(&mut state, &input, later);
    assert_eq!(state.diagnostic_sample(later)["age_ms"], 300);
    let stale = state.diagnostic_sample(now + Duration::from_millis(2800));
    assert_eq!(stale["reason"], "stale");
    assert!(stale.get("context").is_none());
    assert!(stale.get("self_pose").is_none());
}
#[test]
fn body_wire_rejects_missing_wrong_version_unknown_field_and_invalid_pose() {
    let o: Observation = serde_json::from_value(fixture()).unwrap();
    o.validate().unwrap();
    for (pointer, value) in [
        ("/version", json!(1)),
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
    for pointer in ["sample", "pose_basis", "age_ms"] {
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
fn body_v2_requires_extended_pose_and_separate_vision_basis() {
    for (parent, key) in [
        ("/sample/self_pose", "view_yaw"),
        ("/sample/self_pose", "view_pitch"),
        ("/sample/player_pose", "yaw"),
        ("/sample/player_pose", "pitch"),
        ("/sample", "vision"),
        ("/sample/vision", "basis"),
        ("/sample/vision", "range_blocks"),
        ("/sample/vision", "horizontal_fov_degrees"),
        ("/sample/vision", "vertical_fov_degrees"),
        ("/sample/vision", "player_visibility"),
        ("/sample/vision", "reason"),
    ] {
        let mut v = fixture();
        v.pointer_mut(parent)
            .unwrap()
            .as_object_mut()
            .unwrap()
            .remove(key);
        assert!(
            serde_json::from_value::<Observation>(v).is_err(),
            "{parent}/{key}"
        );
    }
    for (pointer, value) in [
        ("/pose_basis", "body_eye_raycast_fov"),
        ("/sample/vision/basis", "server_pose"),
        ("/sample/vision/player_visibility", "unknown"),
        ("/sample/vision/reason", "unknown"),
    ] {
        let mut v = fixture();
        *v.pointer_mut(pointer).unwrap() = json!(value);
        assert!(
            serde_json::from_value::<Observation>(v).is_err(),
            "{pointer}"
        );
    }
    let mut v = fixture();
    v["basis"] = v.as_object_mut().unwrap().remove("pose_basis").unwrap();
    assert!(serde_json::from_value::<Observation>(v).is_err());
    let mut v = fixture();
    v["sample"]["vision"]["server_tick"] = json!(101);
    assert!(serde_json::from_value::<Observation>(v).is_err());
}
#[test]
fn body_v2_angle_and_vision_ranges_reject_nonfinite_and_out_of_bounds_values() {
    // JSONで表せないNaN/無限大も型からのvalidateで拒否する。端の許容値は各規約に合わせる。
    for (field, minimum, maximum, inclusive) in [
        ("yaw", -180.0, 180.0, false),
        ("view_yaw", -180.0, 180.0, false),
        ("view_pitch", -90.0, 90.0, true),
        ("player_yaw", -180.0, 180.0, false),
        ("player_pitch", -90.0, 90.0, true),
        ("range", 0.0, 64.0, true),
        ("horizontal_fov", 0.0, 360.0, true),
        ("vertical_fov", 0.0, 180.0, true),
    ] {
        for (value, valid) in [
            (minimum, minimum < 0.0),
            (minimum + 0.01, true),
            (maximum - 0.01, true),
            (maximum, inclusive),
            (minimum - 0.01, false),
            (maximum + 0.01, false),
            (f64::NAN, false),
            (f64::INFINITY, false),
            (f64::NEG_INFINITY, false),
        ] {
            let mut o = observation(100, 23.0);
            let Observation::Observed { sample, .. } = &mut o else {
                panic!()
            };
            *match field {
                "yaw" => &mut sample.self_pose.yaw,
                "view_yaw" => &mut sample.self_pose.view_yaw,
                "view_pitch" => &mut sample.self_pose.view_pitch,
                "player_yaw" => &mut sample.player_pose.yaw,
                "player_pitch" => &mut sample.player_pose.pitch,
                "range" => &mut sample.vision.range_blocks,
                "horizontal_fov" => &mut sample.vision.horizontal_fov_degrees,
                "vertical_fov" => &mut sample.vision.vertical_fov_degrees,
                _ => unreachable!(),
            } = value;
            assert_eq!(o.validate().is_ok(), valid, "{field}={value}");
        }
    }
}
#[test]
fn body_v2_visibility_reasons_are_closed_and_unavailable_keeps_pose() {
    let reasons = [
        ("visible", "in_view"),
        ("not_visible", "out_of_range"),
        ("not_visible", "outside_fov"),
        ("not_visible", "occluded"),
        ("unavailable", "area_unloaded"),
        ("unavailable", "body_downed"),
        ("unavailable", "owner_unavailable"),
        ("unavailable", "invalid_pose"),
    ];
    for status in ["visible", "not_visible", "unavailable"] {
        for (expected_status, reason) in reasons {
            let mut v = fixture();
            v["sample"]["vision"]["player_visibility"] = json!(status);
            v["sample"]["vision"]["reason"] = json!(reason);
            let o: Observation = serde_json::from_value(v).unwrap();
            assert_eq!(
                o.validate().is_ok(),
                status == expected_status,
                "{status}/{reason}"
            );
            if status != expected_status {
                continue;
            }
            let now = Instant::now();
            let mut s = State::default();
            accept(&mut s, &o, now);
            let projected = s.project(now).unwrap();
            assert_eq!(projected["status"], "observed");
            assert_eq!(projected["pose_basis"], "server_pose");
            assert!(projected.get("basis").is_none());
            assert_eq!(projected["current"]["vision"]["player_visibility"], status);
            assert_eq!(projected["current"]["vision"]["reason"], reason);
            assert_eq!(projected["current"]["self"]["position"]["z"], 20.0);
            assert_eq!(projected["current"]["player"]["position"]["z"], 23.0);
        }
    }
    for version in [1, 2, 3] {
        let o: Observation = serde_json::from_value(
            json!({"version":version,"status":"unavailable","reason":"unloaded"}),
        )
        .unwrap();
        assert_eq!(o.validate().is_ok(), version == 3);
    }
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
    assert_eq!(v["coordinate_system"]["pitch_unit"], "degree");
    assert_eq!(v["coordinate_system"]["pitch_positive"], "down");
    assert_eq!(v["current"]["self"]["view_yaw"], 0.0);
    assert_eq!(v["current"]["self"]["view_pitch"], 0.0);
    assert_eq!(v["current"]["player"]["yaw"], 90.0);
    assert_eq!(v["current"]["player"]["pitch"], 15.0);
    assert_eq!(v["current"]["vision"], fixture()["sample"]["vision"]);
    assert_eq!(v["previous"]["vision"], fixture()["sample"]["vision"]);
    assert_eq!(v["current"]["player"]["position"]["z"], 28.0);
    assert_eq!(v["previous"]["player"]["position"]["z"], 23.0);
    assert_eq!(v["sample_interval_ticks"], 10);
    assert_eq!(v["current"]["position_relation"]["distance_blocks"], 8.0);
    assert_eq!(v["previous"]["position_relation"]["distance_blocks"], 3.0);
    assert_eq!(
        v["current"]["position_relation"]["relative_positions"][0]["horizontal"],
        "front"
    );
    assert_eq!(
        v["current"]["position_relation"]["relative_positions"][1]["horizontal"],
        "right"
    );
    for hidden in ["forward", "moved_away", OWNER, "snapshot_revision"] {
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
    // tickまたはrevisionが古い別内容の視認でも、現在の姿勢・視界と鮮度は更新しない。
    for (tick, revision) in [(99, 202), (101, 199)] {
        let mut late = fixture();
        late["sample"]["server_tick"] = json!(tick);
        late["sample"]["snapshot_revision"] = json!(revision);
        late["sample"]["vision"]["player_visibility"] = json!("not_visible");
        late["sample"]["vision"]["reason"] = json!("occluded");
        accept(
            &mut s,
            &serde_json::from_value(late).unwrap(),
            now + Duration::from_secs(2),
        );
    }
    assert_eq!(
        s.project(now + Duration::from_secs(2)).unwrap()["current"]["vision"]["player_visibility"],
        "visible"
    );
    assert_eq!(
        s.project(now + Duration::from_secs(2)).unwrap()["age_ms"],
        2025
    );
    s.observe(
        None,
        Some(OWNER),
        Some(WORLD),
        now + Duration::from_secs(2),
        Some(0),
    );
    assert_eq!(
        s.project(now + Duration::from_millis(2974)).unwrap()["status"],
        "observed"
    );
    assert_eq!(
        s.project(now + Duration::from_millis(2975)).unwrap()["status"],
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
        version: 3,
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
    v["sample"]["vision"]["player_visibility"] = json!("not_visible");
    v["sample"]["vision"]["reason"] = json!("outside_fov");
    let new = serde_json::from_value(v).unwrap();
    accept(&mut s, &new, now);
    assert!(s.project(now).unwrap()["previous"].is_null());
    accept(&mut s, &observation(120, 29.0), now);
    assert_eq!(
        s.project(now).unwrap()["current"]["player"]["position"]["z"],
        23.0
    );
    assert_eq!(
        s.project(now).unwrap()["current"]["vision"]["reason"],
        "outside_fov"
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

/// 既存ポリシー検査の明示的な身体fixture。製品の入力にはplayerからの補完経路を設けない。
pub(crate) fn with_senses(mut event: Value, tick: u64) -> crate::events::GameEvent {
    let mut body = fixture();
    body["age_ms"] = json!(0);
    let sample = &mut body["sample"];
    let dimension = event["player"]["dimension"]
        .as_str()
        .unwrap_or(WORLD)
        .to_owned();
    event["player"]["dimension"] = json!(dimension);
    sample["context"]["dimension"] = json!(dimension);
    sample["server_tick"] = json!(tick);
    sample["snapshot_revision"] = json!(tick);
    let senses = &mut sample["senses"];
    senses["source_server_tick"] = json!(tick);
    if event["world"].is_object() {
        senses["world"] = event["world"].clone();
        for key in ["air_supply", "respawn_point_set", "respawn_distance"] {
            senses["world"].as_object_mut().unwrap().remove(key);
        }
    }
    for key in [
        "passive_mobs",
        "visual_threats",
        "nearby_resources",
        "dropped_items",
        "ambient_sounds",
        "auditory_threats",
        "nearby_composition",
        "smell_observation",
    ] {
        if !event[key].is_null() {
            senses[key] = event[key].clone();
        }
    }
    for key in ["ambient_sounds", "auditory_threats"] {
        for sound in senses[key].as_array_mut().unwrap() {
            if sound["heard_ago_ms"].is_null() {
                sound["heard_ago_ms"] = json!(0);
            }
        }
    }
    event["body_observation"] = body;
    crate::events::GameEvent::parse(event).unwrap()
}

#[test]
fn senses_contract_requires_closed_version_tick_age_and_limits_without_player_fields() {
    let mut value = fixture();
    value["sample"].as_object_mut().unwrap().remove("senses");
    assert!(serde_json::from_value::<Observation>(value).is_err());
    let mut value = fixture();
    value["sample"]["senses"]
        .as_object_mut()
        .unwrap()
        .remove("hearing_truncated");
    assert!(serde_json::from_value::<Observation>(value).is_err());
    for (key, bad) in [
        ("source_server_tick", json!(101)),
        ("version", json!(2)),
        ("world", json!({"air_supply":100})),
        ("world", json!({"respawn_distance":10})),
        ("world", json!({"untrusted":true})),
        ("passive_mobs", json!(vec![json!({"type":"cow"}); 65])),
        ("ambient_sounds", json!([{"type":"cow"}])),
    ] {
        let mut value = fixture();
        value["sample"]["senses"][key] = bad;
        let parsed: Observation = serde_json::from_value(value).unwrap();
        assert!(parsed.validate().is_err(), "{key}");
    }
    for reason in [
        "area_unloaded",
        "body_downed",
        "invalid_pose",
        "not_sampled",
        "budget_exhausted",
    ] {
        let mut value = fixture();
        value["sample"]["senses"] = json!({"version":1,"status":"unavailable","basis":"body_pose_client_world","source_server_tick":100,"reason":reason});
        serde_json::from_value::<Observation>(value)
            .unwrap()
            .validate()
            .unwrap();
    }
}

#[test]
fn sensory_view_uses_accepted_body_pose_and_preserves_raw_player_ownership() {
    let at = Instant::now();
    let raw = with_senses(
        json!({"schema_version":"2026-05-24","adapter":"test","observed_at":chrono::Utc::now(),
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":WORLD,"position":{"x":90,"y":120,"z":90},"health":17,"held_item":"torch"},
        "inventory":{"torch":3},"combat":{"combat_active_hint":true},"look_target":{"kind":"entity","name":"pig"},
        "world":{"biome":"plains","air_supply":100,"standing_on_magma_block":true,"respawn_point_set":true,"respawn_distance":8},
        "passive_mobs":[{"type":"pig"}]}),
        100,
    );
    let original = json!(raw);
    let mut value = original.clone();
    value["body_observation"]["sample"]["senses"]["world"] =
        json!({"biome":"snowy_taiga","sky_visible":true});
    value["body_observation"]["sample"]["senses"]["passive_mobs"] = json!([{"type":"cow"}]);
    let changed = crate::events::GameEvent::parse(value).unwrap();
    let mut state = State::default();
    accept(&mut state, changed.body_observation.as_ref().unwrap(), at);
    let sensory = state.sensory_event(&raw, at);
    assert_eq!(sensory.world.biome.as_deref(), Some("snowy_taiga"));
    assert_eq!(sensory.passive_mobs[0].r#type, "cow");
    assert_eq!(sensory.observer_y(), Some(64.0));
    assert_eq!(json!(sensory.player), original["player"]);
    assert_eq!(json!(sensory.combat), original["combat"]);
    assert_eq!(json!(sensory.look_target), original["look_target"]);
    assert_eq!(json!(sensory.inventory), original["inventory"]);
    assert_eq!(sensory.player_safety_world().air_supply, Some(100));
    assert_eq!(
        sensory.player_safety_world().standing_on_magma_block,
        Some(true)
    );
    assert!(sensory.world.air_supply.is_none());
    assert_eq!(json!(raw), original);
    let stale = state.sensory_event(&raw, at + Duration::from_secs(3));
    assert!(!stale.body_origin().unwrap().available);
    assert!(stale.world.biome.is_none() && stale.passive_mobs.is_empty());
    assert_eq!(stale.player.health, Some(17.0));
    assert_eq!(stale.player.position.y, Some(120.0));
    let model = state.project(at).unwrap();
    assert_eq!(model.as_object().unwrap().keys().next().unwrap(), "current");
    assert_eq!(
        model["current"].as_object().unwrap().keys().next().unwrap(),
        "vision"
    );
    assert!(model["current"]["senses"].get("world").is_none());
}
