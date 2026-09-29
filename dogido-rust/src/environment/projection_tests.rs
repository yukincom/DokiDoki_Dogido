use super::*;
use serde_json::Value;

#[test]
fn current_frame_projection_matches_python_without_mutating_the_event() {
    let cases = include_str!("../../fixtures/environment-projection.jsonl");
    for (index, line) in cases.lines().enumerate() {
        let case: Value = serde_json::from_str(line).unwrap();
        let event: GameEvent = serde_json::from_value(case["event"].clone()).unwrap();
        let before = serde_json::to_value(&event).unwrap();
        let actual = serde_json::to_value(project_environment(&event)).unwrap();
        assert_eq!(
            actual, case["expected"],
            "environment case {index}: {}",
            case["event"]
        );
        assert_eq!(serde_json::to_value(&event).unwrap(), before);
    }
    assert_eq!(cases.lines().count(), 2277);
}
