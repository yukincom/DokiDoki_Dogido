use dogido_rust::{
    chat_topics::{self, Input, Projection},
    planner::{Grounding, Plan},
};
use serde_json::Value;
fn fixture() -> Value {
    serde_json::from_str(include_str!("../src/chat_topics/fixtures.json")).unwrap()
}
#[test]
fn chat_topics_canonical_python_projection_including_none_and_observation_boundaries() {
    let f = fixture();
    let pool = f["pool"].as_array().unwrap();
    for (i, row) in f["cases"].as_array().unwrap().iter().enumerate() {
        let get = |n: usize| pool[row[n].as_u64().unwrap() as usize].clone();
        let input: Input = serde_json::from_value(
            serde_json::json!({"topic_hits":get(0),"user_text":get(1),"observed_ids":get(2),
            "has_visual_threats":row[3],"threat_summary":get(4)}),
        )
        .unwrap();
        let plan: Plan = serde_json::from_value(get(5)).unwrap();
        let ground: Grounding = serde_json::from_value(get(6)).unwrap();
        let expected: Projection = serde_json::from_value(get(7)).unwrap();
        assert_eq!(
            chat_topics::finish(&input, &plan, &ground),
            expected,
            "case {i}"
        );
    }
}
#[test]
fn chat_topics_weak_terms_intent_and_policy_lines_match_python() {
    let f = fixture();
    for row in f["terms"].as_array().unwrap() {
        assert_eq!(
            chat_topics::is_generic_topic_term(row[0].as_str().unwrap()),
            row[1].as_bool().unwrap(),
            "{row}"
        );
    }
    for row in f["intents"].as_array().unwrap() {
        assert_eq!(
            chat_topics::has_identify_intent(row[0].as_str().unwrap()),
            row[1].as_bool().unwrap(),
            "{row}"
        );
        assert_eq!(
            chat_topics::has_threat_presence_query(row[0].as_str().unwrap()),
            row[2].as_bool().unwrap(),
            "{row}"
        );
    }
    for row in f["policies"].as_array().unwrap() {
        assert_eq!(
            dogido_rust::chat_prompt::reply_policy_line(row[0].as_str().unwrap()),
            row[1].as_str().unwrap(),
            "{row}"
        );
    }
}
#[test]
fn chat_topics_closed_projection_does_not_accept_claimed_observations_inside_hits() {
    let value = serde_json::json!({"has_visual_threats":false,"topic_hits":[{"entry_id":"goat","label_ja":"ヤギ","matched_terms":["ヤギ"],"score":8,"observed":true}],
        "threat_summary":"","user_text":"ヤギいる？","observed_ids":[]});
    assert!(serde_json::from_value::<Input>(value).is_err());
}
