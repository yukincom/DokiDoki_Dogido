use dogido_rust::planner::{
    self, PreparedPlan,
    handoff::{Handoff, Input, Output},
};
use serde_json::{Value, json};
fn fixture() -> Value {
    serde_json::from_str(include_str!("../fixtures/planner/grounding.json")).unwrap()
}
fn prepared(case: &Value) -> PreparedPlan {
    serde_json::from_value(json!({"schema_version":1,"model":"fixture","enable_thinking":false,
        "details":{"allowed_actions":["continue_conversation"],"history":[],"current":{"turn_id":"current","role":"user","text":"ヤギいる？"},
            "observations":{"observed_entities":case["prompt_observations"]},"routing_hints":{}},
        "fallback":{"action":"continue_conversation","focus":"返答","entity_query":"","evidence":[{"turn_id":"current","quote":"ヤギいる？"}],
        "confidence":0,"source":"fallback","status":"fallback"}})).unwrap()
}
#[test]
fn chat_grounding_fixture_covers_all_actions_and_current_evidence() {
    for (i, case) in fixture()["cases"].as_array().unwrap().iter().enumerate() {
        let input: Input = serde_json::from_value(case["input"].clone()).unwrap();
        let mut state = Handoff::default();
        state.record_plan(&prepared(case), &input.plan).unwrap();
        let actual = state.resolve(input, true).unwrap();
        let expected: Output = serde_json::from_value(case["expected"].clone()).unwrap();
        assert_eq!(actual, expected, "case {i}");
    }
}
#[test]
fn chat_grounding_parse_fallback_matches_fixture_for_reports_and_corrections() {
    for (i, case) in fixture()["parsed"].as_array().unwrap().iter().enumerate() {
        let details = serde_json::from_value(case["details"].clone()).unwrap();
        let fallback: planner::Plan = serde_json::from_value(case["fallback"].clone()).unwrap();
        let mut actual = planner::parse_model_plan(&case["payload"], &details)
            .unwrap_or_else(|| fallback.clone());
        actual.presence_challenged = fallback.presence_challenged;
        let expected: planner::Plan = serde_json::from_value(case["expected"].clone()).unwrap();
        assert_eq!(actual, expected, "parse {i}");
    }
}
#[test]
fn chat_grounding_one_plan_one_resolution_and_cancelled_turn_cannot_publish() {
    let f = fixture();
    let case = f["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| {
            c["expected"]["fixed_reply"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
        })
        .unwrap();
    let input: Input = serde_json::from_value(case["input"].clone()).unwrap();
    let request = prepared(case);
    let mut state = Handoff::default();
    assert!(state.resolve(input.clone(), true).is_err());
    state.record_plan(&request, &input.plan).unwrap();
    assert!(state.record_plan(&request, &input.plan).is_err());
    assert!(state.validate_leaf(&json!({})).is_err());
    assert!(state.validate_result(&json!("未検査の返事")).is_err());
    assert!(state.resolve(input.clone(), false).is_err());
    assert!(state.validate_result(&json!("取消後の返事")).is_err());
    let output = state.resolve(input.clone(), true).unwrap();
    assert!(state.resolve(input, true).is_err());
    assert!(state.validate_leaf(&json!({})).is_err());
    assert!(state.validate_result(&json!("いるはずや")).is_err());
    state.validate_result(&json!(output.fixed_reply)).unwrap();
    // No persisted state exists outside this per-render object.
    assert!(Handoff::default().validate_leaf(&json!({})).is_ok());
}
#[test]
fn chat_grounding_projection_and_leaf_are_bound_to_accepted_plan() {
    let f = fixture();
    let case = &f["cases"][0];
    let input: Input = serde_json::from_value(case["input"].clone()).unwrap();
    let mut state = Handoff::default();
    state.record_plan(&prepared(case), &input.plan).unwrap();
    let mut wrong = input.clone();
    wrong.plan.focus = "別の計画".into();
    assert!(state.resolve(wrong, true).is_err());
    let mut wrong = input.clone();
    wrong.source = "assistant_history".into();
    assert!(state.resolve(wrong, true).is_err());
    let mut wrong = input.clone();
    wrong.observed_entities.push(planner::handoff::Observation {
        entity_id: "goat".into(),
        label: "ヤギ".into(),
    });
    assert!(state.resolve(wrong, true).is_err());
    let mut extra = case["input"].clone();
    extra["player_reports"] = json!(["ヤギがいる"]);
    assert!(serde_json::from_value::<Input>(extra).is_err());
    let output = state.resolve(input.clone(), true).unwrap();
    let g = output.grounding;
    let mut details = json!({"player_chat_plan_action":input.plan.action,"entity_query":g.query,"entity_grounding_status":g.status,
      "entity_candidate_labels":g.candidate_labels,"entity_observed_labels":g.observed_labels});
    state.validate_leaf(&details).unwrap();
    details["entity_grounding_status"] = "observed".into();
    assert!(state.validate_leaf(&details).is_err());
}

#[test]
fn chat_grounding_filters_raw_topics_before_matching_and_keeps_original_indices() {
    let f = fixture();
    let case = &f["cases"][0];
    let mut input: Input = serde_json::from_value(case["input"].clone()).unwrap();
    input.plan.action = planner::Action::IdentifyEntity;
    input.plan.entity_query = "白い角のあるやつ".into();
    input.topic_hits = serde_json::from_value(json!([
        {"entry_id":"sheep","label":"ヒツジ","score":30},
        {"entry_id":"goat","label":"ヤギ","score":8}
    ]))
    .unwrap();
    input.topic_policy = Some(serde_json::from_value(json!({
        "has_visual_threats":false,"threat_summary":"","user_text":"白い角のあるやつ","observed_ids":[],
        "topic_hits":[
            {"entry_id":"sheep","label_ja":"ヒツジ","matched_terms":["白い"],"score":30},
            {"entry_id":"goat","label_ja":"ヤギ","matched_terms":["角"],"score":8}
        ]
    })).unwrap());
    // A one-character generic term cannot become an identification, either.
    input.topic_policy.as_mut().unwrap().topic_hits[1].matched_terms = vec!["ヤギ".into()];
    let mut state = Handoff::default();
    state.record_plan(&prepared(case), &input.plan).unwrap();
    let mut wrong = input.clone();
    wrong.topic_policy.as_mut().unwrap().topic_hits[1].entry_id = "zombie".into();
    assert!(state.resolve(wrong, true).is_err());
    let actual = state.resolve(input, true).unwrap();
    assert_eq!(actual.grounding.candidate_ids, vec!["goat"]);
    let topics = actual.topics.unwrap();
    assert_eq!(topics.policy.usable_indices, vec![1]);
    assert_eq!(topics.topic_for_identify_indices, vec![1]);
    assert!(topics.identify_skeleton.unwrap().contains("ヤギ"));
}

#[test]
fn chat_grounding_native_catalog_uses_accepted_plan_and_binds_later_materials() {
    let f = fixture();
    let case = &f["cases"][0];
    for action in [
        planner::Action::IdentifyEntity,
        planner::Action::ContinueConversation,
    ] {
        let mut input: Input = serde_json::from_value(case["input"].clone()).unwrap();
        input.plan.action = action;
        input.plan.entity_query = "ヤギ".into();
        input.native_catalog = true;
        input.topic_hits.clear();
        input.topic_policy = Some(
            serde_json::from_value(json!({"has_visual_threats":false,
            "topic_hits":[],"threat_summary":"","user_text":"ヤギ","observed_ids":[]}))
            .unwrap(),
        );
        let mut state = Handoff::default();
        state.record_plan(&prepared(case), &input.plan).unwrap();
        let actual = state.resolve(input, true).unwrap();
        let rows = actual.catalog.as_ref().unwrap();
        let topics = actual.topics.as_ref().unwrap();
        assert_eq!(
            rows.is_empty(),
            action == planner::Action::ContinueConversation
        );
        if action == planner::Action::IdentifyEntity {
            assert_eq!(rows[0].entry_id, "goat");
            assert!(
                actual
                    .catalog_topic_hints
                    .as_ref()
                    .unwrap()
                    .contains("ヤギ")
            );
        }
        for workshop in [false, true] {
            let stance = if workshop {
                "none"
            } else {
                topics.policy.reply_stance.as_str()
            };
            let mut details = json!({"reply_stance":stance,
                "reply_policy":dogido_rust::chat_prompt::reply_policy_line(stance),
                "identify_skeleton":if workshop {json!("")} else {json!(topics.identify_skeleton)},
                "catalog_topic_hints":if workshop {""} else {actual.catalog_topic_hints.as_deref().unwrap()},
                "catalog_topic_ids":if workshop {vec![]} else {topics.topic_for_identify_indices.iter().map(|i| rows[*i].entry_id.as_str()).collect::<Vec<_>>()}});
            state
                .validate_materials(&details, &json!({}), workshop)
                .unwrap();
            details["catalog_topic_hints"] = "勝手な候補".into();
            assert!(
                state
                    .validate_materials(&details, &json!({}), workshop)
                    .is_err()
            );
        }
    }
}
