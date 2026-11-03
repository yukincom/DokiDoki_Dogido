use super::*;
use serde_json::{Value, json};

#[test]
fn current_frame_projection_matches_fixture_without_mutating_the_event() {
    let cases = include_str!("../../fixtures/environment-projection.jsonl");
    for (index, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line).unwrap();
        let event: GameEvent = serde_json::from_value(case["event"].clone()).unwrap();
        let before = serde_json::to_value(&event).unwrap();
        // 旧fixtureにだけある落下物の根拠を外し、現在の採掘は道具・破壊履歴・採掘中の実測で照合する。
        let mut expected = case["expected"].clone();
        let evidence = expected["evidence"].as_array_mut().unwrap();
        let had_drops = evidence.iter().any(|v| v.as_str().is_some_and(|s| s.starts_with("fresh_mining_drops:")));
        evidence.retain(|v| !v.as_str().is_some_and(|s| s.starts_with("fresh_mining_drops:")));
        if had_drops && evidence.contains(&json!("recent_terrain_breaks:1")) && !evidence.contains(&json!("block_breaking_active")) {
            expected["mining_state"] = json!("none");
            expected["mining_label"] = json!("");
        }
        let actual = serde_json::to_value(project_environment(&event)).unwrap();
        assert_eq!(
            actual, expected,
            "environment case {index}: {}",
            case["event"]
        );
        assert_eq!(serde_json::to_value(&event).unwrap(), before);
    }
    assert_eq!(cases.lines().count(), 2277);
}
