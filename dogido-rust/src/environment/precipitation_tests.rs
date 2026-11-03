use super::*;
use serde_json::{Value, json};

// 移植時fixtureの気候、天候、Yの全組合せを使う。現在地の降水を照合し、旧地表観測は期待値から外す。
// Minecraftの実降雪や画像認識はここでは扱わない。
fn check_context(context: &PrecipitationContext, expected: &Value, index: usize) {
    let mut canonical = expected["context"].clone();
    for key in ["surface_snow_observed", "snow_evidence"] {
        canonical.as_object_mut().unwrap().remove(key);
    }
    assert_eq!(json!(context), canonical, "case {index}");
    assert_eq!(json!(context.to_prompt_details()),
        json!({"precipitation_kind":expected["context"]["precipitation_kind"]}), "case {index}");
}

// 気候と高度で局所種別が決まる。晴れに雪の注釈が入らず、温度・閾値も会話へ渡らないことをJSONで照合。
#[test]
fn precipitation_and_prompt_facts_match_fixture() {
    let cases = include_str!("../../fixtures/precipitation.jsonl");
    for (index, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line).unwrap();
        let input: Input = serde_json::from_value(case["input"].clone()).unwrap();
        let context = resolve_precipitation_context(&input).unwrap();
        check_context(&context, &case["expected"], index);
    }
    assert_eq!(cases.lines().count(), 1271);
}
#[test]
fn frame_adapter_uses_catalog_and_weather_independently_of_legacy_resources() {
    let cases = include_str!("../../fixtures/precipitation-frame.jsonl");
    for (index, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line).unwrap();
        let event: GameEvent = serde_json::from_value(case["event"].clone()).unwrap();
        let climate: Climate = serde_json::from_value(case["climate"].clone()).unwrap();
        let before = serde_json::to_value(&event).unwrap();
        let context = from_event(&event, &climate).unwrap();
        check_context(&context, &case["expected"], index);
        assert_eq!(serde_json::to_value(&event).unwrap(), before);
    }
    assert_eq!(cases.lines().count(), 896);
}
#[test]
fn impossible_altitude_keeps_canonical_error_instead_of_creating_snow_evidence() {
    for (value, class) in [
        (f64::NAN, "ValueError"),
        (f64::INFINITY, "OverflowError"),
        (f64::NEG_INFINITY, "OverflowError"),
    ] {
        let result = resolve_precipitation_context(&Input {
            current_y: Some(value),
            ..Default::default()
        });
        assert!(result.unwrap_err().to_string().starts_with(class));
    }
}
#[test]
fn large_finite_altitude_does_not_saturate_to_i64_or_lose_integer_threshold_order() {
    for (y, threshold, expected) in [
        (i64::MAX as f64, i64::MAX, true),
        (i64::MIN as f64, i64::MIN + 1, false),
        (f64::MAX, i64::MAX, true),
        (f64::MIN, i64::MIN, false),
    ] {
        let context = resolve_precipitation_context(&Input {
            current_y: Some(y),
            snow_start_y: Some(threshold),
            weather: "rain".into(),
            ..Default::default()
        })
        .unwrap();
        assert_eq!(context.snowfall_zone, Some(expected));
        assert_eq!(context.current_y.as_ref().unwrap().as_f64().unwrap(), y);
    }
}
