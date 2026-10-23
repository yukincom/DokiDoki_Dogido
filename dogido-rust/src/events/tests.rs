//! イベントと添付操作結果の日時を、同じ受信境界で検証する。
//! offset付きの現行送信形式を保持し、日時・単位の推定が必要な旧入力を状態へ入れない。
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
