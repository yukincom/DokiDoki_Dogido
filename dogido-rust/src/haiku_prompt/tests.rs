use super::*;
#[test]
fn six_prepare_kinds_match_canonical_helper_messages() {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    for (index, row) in fixtures.as_array().unwrap().iter().enumerate() {
        let request: StructuredRequest = serde_json::from_value(row["request"].clone()).unwrap();
        let actual = messages(&request).unwrap();
        assert_eq!(
            json!(actual),
            row["messages"],
            "case {index} {} {}",
            row["name"],
            request.kind
        );
    }
}
#[test]
fn unknown_kind_is_rejected_without_io() {
    let request = StructuredRequest {
        kind: "player_chat".into(),
        details: Map::new(),
        route: "chat".into(),
        temperature: 0.0,
        max_tokens: Some(512),
        fallback_value: json!({}),
    };
    assert!(messages(&request).is_err());
}
