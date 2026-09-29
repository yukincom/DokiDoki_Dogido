use super::*;
static FIXTURES: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("fixtures.json")).unwrap());
#[test]
fn canonical_schema_semantic_and_helper_validation_match() {
    let mut mismatch = Vec::new();
    for case in array(&FIXTURES["cases"]) {
        let context = &FIXTURES["contexts"][case["context"].as_u64().unwrap() as usize];
        let mut frame = context["frame"].clone();
        frame["payload"] = case["payload"].clone();
        let actual = validate(&frame, &context["details"]);
        if case["error"] == true {
            assert!(actual.is_err(), "{}", case["name"]);
            continue;
        }
        let actual = actual.unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
        if actual != case["expected"] {
            mismatch.push(format!(
                "{} actual={} expected={}",
                case["name"], actual, case["expected"]
            ));
        }
        assert_eq!(frame["payload"], case["payload"]);
    }
    assert!(
        mismatch.is_empty(),
        "{} mismatches:\n{}",
        mismatch.len(),
        mismatch
            .iter()
            .take(12)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
    assert_eq!(array(&FIXTURES["cases"]).len(), 3429);
}
#[test]
fn oversized_request_and_invalid_projection_fail_before_decision() {
    let context = &FIXTURES["contexts"][0];
    let mut frame = context["frame"].clone();
    frame["payload"] = json!({"speech":"a".repeat(1_000_000)});
    assert!(
        validate(&frame, &context["details"])
            .unwrap_err()
            .to_string()
            .contains("too large")
    );
    frame["payload"] = Value::Null;
    for key in [
        "allowed_actions",
        "allowed_purposes",
        "allowed_checks",
        "allowed_problem_types",
        "player_text",
        "original_player_text",
        "working_reading",
        "workshop_context",
    ] {
        let mut d = context["details"].clone();
        d[key] = Value::Null;
        assert!(validate(&frame, &d).is_err(), "{key}");
    }
}
