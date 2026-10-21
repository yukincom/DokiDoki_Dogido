use super::*;
use serde_json::Map;
#[test]
fn unchanged_prepare_kinds_match_canonical_helper_messages() {
    let fixtures: Value = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    for (index, row) in fixtures.as_array().unwrap().iter().enumerate() {
        let request: StructuredRequest = serde_json::from_value(row["request"].clone()).unwrap();
        if matches!(
            request.kind.as_str(),
            "haiku_line_grounding" | "haiku_draft" | "haiku_line_regeneration"
        ) {
            continue;
        }
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

#[test]
fn available_materials_reach_generation_without_internal_ids_or_frozen_materials() {
    for kind in ["haiku_draft", "haiku_line_regeneration"] {
        let request=StructuredRequest {kind:kind.into(), details:json!({
            "source_atoms":[{"atom_id":"SECRET-FROZEN-ID","text":"固定済みの古い材料"}],
            "nearby_blocks":["固定済みの古い材料"],"feature_candidates":["固定済みの古い材料"],
            "available_line_materials":[{"line_index":1,"materials":[{"text":"今使える雨粒","claim_class":"factual","claim_scopes":["observed_state"]}]}],
            "failed_line_indices":[1],"current_lines":[{"line_index":0,"text":"くさちのひ","frozen":true}]
        }).as_object().unwrap().clone(),route:"haiku".into(),temperature:0.6,max_tokens:Some(192),fallback_value:json!({})};
        let text = messages(&request)
            .unwrap()
            .iter()
            .map(|m| m.content.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(text.contains("中七で今使える材料"));
        assert!(text.contains("今使える雨粒"));
        assert!(!text.contains("SECRET-FROZEN-ID"));
        assert!(!text.contains("固定済みの古い材料"));
        assert!(!text.contains("atom_id"));
    }
}
#[test]
fn removed_evaluation_prompt_is_unavailable() {
    let request = StructuredRequest {
        kind: "haiku_line_grounding".into(),
        details: Map::new(),
        route: "chat".into(),
        temperature: 0.0,
        max_tokens: Some(512),
        fallback_value: json!({}),
    };
    assert!(messages(&request).is_err());
}
