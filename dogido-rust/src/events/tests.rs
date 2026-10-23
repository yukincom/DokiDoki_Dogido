//! イベントと添付操作結果の日時・数値・真偽値を、同じ受信境界で検証する。
//! 現行のJSON型とoffset付き日時を保持し、別型からの補完が必要な旧入力を状態へ入れない。
use super::{BatchEvents, GameEvent};
use chrono::{DateTime, Timelike};
use serde_json::{Value, json};

fn event() -> Value {
    json!({
        "schema_version":"2026-05-24", "adapter":"wire-test",
        "observed_at":"2026-10-23T03:00:00Z",
        "event":{"name":"status_snapshot", "source_kind":"system",
            "priority_hint":"background", "certainty":"high"},
        "command_results":[{"command_id":"one", "command_type":"select_hotbar",
            "status":"failed", "detail_code":"unavailable",
            "executed_at":"2026-10-23T03:00:00Z"}]
    })
}

#[test]
fn event_and_result_times_keep_offsets_fractional_precision_and_output_format() {
    for (text, offset, nanos, rendered) in [
        ("2026-10-23T03:00:00Z", 0, 0, "2026-10-23T03:00:00.000000Z"),
        (
            "2026-10-23T12:00:00.123456789+09:00",
            32400,
            123456789,
            "2026-10-23T12:00:00.123456+09:00",
        ),
        (
            "2026-10-22T22:00:00.100-05:00",
            -18000,
            100000000,
            "2026-10-22T22:00:00.100000-05:00",
        ),
    ] {
        let mut input = event();
        input["observed_at"] = json!(text);
        input["command_results"][0]["executed_at"] = json!(text);
        let accepted = GameEvent::parse(input).unwrap();
        for time in [
            accepted.observed_at,
            accepted.command_results[0].executed_at,
        ] {
            assert_eq!(time, DateTime::parse_from_rfc3339(text).unwrap());
            assert_eq!(time.offset().local_minus_utc(), offset);
            assert_eq!(time.nanosecond(), nanos);
        }
        let output = json!(accepted);
        assert_eq!(output["observed_at"], rendered);
        assert_eq!(output["command_results"][0]["executed_at"], rendered);
    }
}

#[test]
fn legacy_and_missing_offset_times_are_rejected_before_event_or_batch_admission() {
    for invalid in [
        json!("2026-10-23T03:00:00"),
        json!("2026-10-23T03:00:00.123456"),
        json!("2026-10-23"),
        json!("2026-10-23 03:00:00Z"),
        json!(1792724400),
        json!(1792724400.123),
        json!(1792724400000_i64),
        json!("1792724400"),
        json!("1792724400000"),
        json!(false),
        json!(true),
        Value::Null,
    ] {
        for field in ["observed_at", "executed_at"] {
            let mut input = event();
            if field == "observed_at" {
                input[field] = invalid.clone();
            } else {
                input["command_results"][0][field] = invalid.clone();
            }
            assert!(
                GameEvent::parse(input.clone()).is_err(),
                "{field}: {invalid}"
            );
            assert!(
                serde_json::from_value::<BatchEvents>(json!({"events":[event(), input]})).is_err(),
                "batch {field}: {invalid}"
            );
        }
    }
}


fn typed_event() -> Value {
    let mut input = event();
    input["sequence"] = json!(1);
    input["player"] = json!({
        "position":{"x":1,"y":64.5,"z":-2},
        "health":20,"hunger":20,"block_breaking_active":true,
        "hotbar":{"selected_slot":0,"slots":[
            {"slot":0,"count":3,"damage":0,"max_damage":100,"attack_damage":7.5}
        ]}
    });
    input["world"] = json!({"sky_visible":true,"game_paused":false});
    input["meta"] = json!({"debug":false});
    input["inventory"] = json!({"torch":3});
    input["command_results"][0]["selected_slot"] = json!(0);
    input
}

#[test]
fn native_numbers_booleans_and_optional_nulls_keep_their_meaning() {
    let input = typed_event();
    let accepted = GameEvent::parse(input.clone()).unwrap();
    assert_eq!(accepted.sequence, Some(1));
    assert_eq!(accepted.player.position.x, Some(1.0));
    assert_eq!(accepted.player.position.y, Some(64.5));
    assert_eq!(accepted.player.health, Some(20.0));
    assert_eq!(accepted.inventory["torch"], 3);
    assert_eq!(accepted.player.block_breaking_active, Some(true));
    assert_eq!(accepted.world.sky_visible, Some(true));
    assert_eq!(accepted.world.game_paused, Some(false));
    assert!(!accepted.meta.debug);
    let batch: BatchEvents = serde_json::from_value(json!({"events":[input]})).unwrap();
    assert_eq!(batch.events.len(), 1);

    let mut unknown = typed_event();
    for path in ["/sequence", "/player/health", "/player/block_breaking_active",
        "/command_results/0/selected_slot"] {
        *unknown.pointer_mut(path).unwrap() = Value::Null;
    }
    let accepted = GameEvent::parse(unknown).unwrap();
    assert_eq!(accepted.sequence, None);
    assert_eq!(accepted.player.health, None);
    assert_eq!(accepted.player.block_breaking_active, None);
    assert_eq!(accepted.command_results[0].selected_slot, None);
    assert_eq!(GameEvent::parse(event()).unwrap().player.hunger, None);
}

#[test]
fn legacy_scalar_coercions_are_rejected_before_event_or_batch_admission() {
    let groups = [
        (vec!["/sequence", "/player/hunger", "/player/hotbar/selected_slot",
            "/player/hotbar/slots/0/count", "/inventory/torch",
            "/command_results/0/selected_slot"],
         vec![json!("1"), json!("1_000"), json!("1.00"), json!(1.0),
            json!(true), json!(false), json!(u64::MAX)]),
        (vec!["/player/health", "/player/position/x", "/player/hotbar/slots/0/attack_damage"],
         vec![json!("1.5"), json!(true), json!(false), json!("NaN"), json!("inf")]),
        (vec!["/player/block_breaking_active", "/world/sky_visible", "/world/game_paused", "/meta/debug"],
         vec![json!("true"), json!("on"), json!("OFF"), json!("0"), json!("y"),
            json!(0), json!(1), json!(0.0), json!(1.0)]),
    ];
    for (paths, values) in groups {
        for path in paths {
            for invalid in &values {
                let mut input = typed_event();
                *input.pointer_mut(path).unwrap() = invalid.clone();
                assert!(GameEvent::parse(input.clone()).is_err(), "{path}: {invalid}");
                assert!(serde_json::from_value::<BatchEvents>(json!({"events":[typed_event(), input]})).is_err(),
                    "batch {path}: {invalid}");
            }
        }
    }
}

#[test]
fn non_finite_numbers_and_integer_overflow_never_reach_event_validation() {
    let mut input = typed_event();
    input["player"]["health"] = json!("__NON_FINITE__");
    let encoded = serde_json::to_string(&input).unwrap();
    for invalid in ["1e400", "-1e400", "NaN", "Infinity", "-Infinity"] {
        let raw = encoded.replace("\"__NON_FINITE__\"", invalid);
        assert!(serde_json::from_str::<GameEvent>(&raw).is_err(), "{invalid}");
    }
    for value in [-1e308, 1e308] {
        let mut input = typed_event();
        input["player"]["position"]["x"] = json!(value);
        assert_eq!(GameEvent::parse(input).unwrap().player.position.x, Some(value));
    }
}

#[test]
fn native_types_still_require_valid_ranges_and_consistent_hotbar_slots() {
    for (path, invalid) in [
        ("/sequence", json!(-1)),
        ("/command_results/0/selected_slot", json!(9)),
        ("/player/hotbar/selected_slot", json!(9)),
        ("/player/hotbar/slots/0/count", json!(-1)),
        ("/player/hotbar/slots/0/attack_damage", json!(-0.5)),
    ] {
        let mut input = typed_event();
        *input.pointer_mut(path).unwrap() = invalid;
        assert!(GameEvent::parse(input).is_err(), "{path}");
    }
    let mut duplicate = typed_event();
    let slot = duplicate["player"]["hotbar"]["slots"][0].clone();
    duplicate["player"]["hotbar"]["slots"].as_array_mut().unwrap().push(slot);
    assert!(GameEvent::parse(duplicate).unwrap_err().contains("slot indices must be unique"));
}
