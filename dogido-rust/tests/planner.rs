use dogido_rust::planner::{self, Action, PreparedPlan};
use serde_json::json;

fn repair_case() -> PreparedPlan {
    serde_json::from_str(include_str!("../fixtures/planner/explicit_repair.json")).unwrap()
}
fn candidate() -> serde_json::Value {
    json!({"action": "repair_conversation", "focus": "本人の言い直し", "entity_query": "",
        "evidence": [{"turn_id": "current", "quote": "違う"}, {"turn_id": "old:reply", "quote": "1位になれんでもええやん。"}],
        "confidence": 0.95, "repair": {"target_turn_id": "old:reply", "target_quote": "1位になれんでもええやん。", "signal_quote": "違う", "replacement_quote": "仲間になるのは無理ってこと"}})
}

#[test]
fn repair_needs_raw_evidence_and_never_changes_history() {
    let mut input = repair_case();
    let original = input.details.history.clone();
    let plan = planner::parse_model_plan(&candidate(), &input.details).unwrap();
    assert_eq!(plan.action, Action::RepairConversation);
    assert_eq!(input.details.history, original);
    input.details.current["raw_text"] = json!("仲間にはなれないかな");
    assert!(planner::parse_model_plan(&candidate(), &input.details).is_none());
}

#[test]
fn low_confidence_is_a_consumer_rejection_not_a_schema_retry() {
    let input = repair_case();
    let mut value = candidate();
    value["confidence"] = json!(0.81);
    assert!(planner::contract_errors(&value, &input.details).is_empty());
    assert!(planner::parse_model_plan(&value, &input.details).is_none());
}

#[test]
fn quoted_signal_cannot_enable_repair_and_unknown_fields_are_rejected() {
    assert!(!planner::repair::has_signal(
        "🐈『違う』っていうキャラが好き"
    ));
    assert!(planner::repair::has_signal(
        "🐈『違う』の話じゃなくて、仲間の話"
    ));
    let mut value = candidate();
    value["world_operation"] = json!("move");
    assert!(!planner::contract_errors(&value, &repair_case().details).is_empty());
}

#[test]
fn seeing_a_related_mob_does_not_prove_the_requested_structure_exists() {
    let mut plan = repair_case().fallback;
    plan.action = Action::CheckEntityPresence;
    plan.entity_query = "前哨基地".into();
    let result = planner::ground(
        &plan,
        &[
            json!({"entry_id": "outpost", "label_ja": "前哨基地", "score": 1.0}),
            json!({"entry_id": "pillager", "label_ja": "ピリジャー", "score": 0.8}),
        ],
        &[json!({"entity_id": "pillager", "label": "ピリジャー"})],
    );
    assert_eq!(result.status, "not_observed");
    assert_eq!(
        planner::fixed_reply(&plan, &result),
        "今の観測では、前哨基地は確認できてへんわ。"
    );
    let blank_label = planner::ground(
        &plan,
        &[json!({"entry_id": "outpost", "label_ja": " \u{3000}"})],
        &[],
    );
    assert_eq!(blank_label.candidate_labels, ["outpost"]);
}

#[tokio::test]
async fn empty_input_returns_without_connecting_to_a_model() {
    let mut input: PreparedPlan =
        serde_json::from_str(include_str!("../fixtures/planner/casual.json")).unwrap();
    input.details.current["text"] = json!(" \u{3000}\n");
    let client = dogido_rust::llm::RigLlm::new(
        "http://127.0.0.1:1/v1",
        None,
        std::time::Duration::from_millis(1),
    )
    .unwrap();
    let report = planner::run(&client, &input).await.unwrap();
    assert_eq!(report.calls, 0);
    assert_eq!(report.result, "empty_input");
    assert_eq!(report.plan, input.fallback);
}
