use super::*;
use serde_json::{Value, json};

fn output(context: &PrecipitationContext) -> Value {
    json!({"context":context,"snow_can_be_scene_material":context.snow_can_be_scene_material(),
        "prompt_line":context.prompt_line(),"prompt_details":context.to_prompt_details()})
}
fn check_public_details(context: &PrecipitationContext) {
    let details = serde_json::to_value(context.to_prompt_details()).unwrap();
    assert_eq!(details.as_object().unwrap().len(), 6);
    for hidden in [
        "current_y",
        "biome_temperature",
        "snow_start_y",
        "biome_downfall",
        "snowfall_zone",
    ] {
        assert!(details.get(hidden).is_none(), "{details}");
    }
}
#[test]
fn precipitation_and_prompt_facts_match_python() {
    let cases = include_str!("../../fixtures/precipitation.jsonl");
    for (index, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line).unwrap();
        let input: Input = serde_json::from_value(case["input"].clone()).unwrap();
        let context = resolve_precipitation_context(&input).unwrap();
        assert_eq!(
            output(&context),
            case["expected"],
            "precipitation case {index}: {}",
            case["input"]
        );
        check_public_details(&context);
    }
    assert_eq!(cases.lines().count(), 1271);
}
#[test]
fn frame_adapter_reuses_catalog_values_and_only_observed_blocks() {
    let cases = include_str!("../../fixtures/precipitation-frame.jsonl");
    for (index, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line).unwrap();
        let event: GameEvent = serde_json::from_value(case["event"].clone()).unwrap();
        let climate: Climate = serde_json::from_value(case["climate"].clone()).unwrap();
        let before = serde_json::to_value(&event).unwrap();
        let context = from_event(&event, &climate).unwrap();
        assert_eq!(
            output(&context),
            case["expected"],
            "frame case {index}: {}",
            case["event"]
        );
        assert_eq!(serde_json::to_value(&event).unwrap(), before);
        check_public_details(&context);
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
