use dogido_rust::language::{State, Turn};
use serde_json::{Value, json};

fn turn() -> Turn {
    Turn::new(
        &json!({"operation_id":"t1", "text":"枕詞ってどういう意味？", "source":"voice",
        "history":[], "language_active":false, "language_state":State::default()}),
    )
    .unwrap()
}
fn lookup_turn() -> Turn {
    let mut t = turn();
    t.advance(&json!({"stage":"start"})).unwrap();
    t.generated(json!({})).unwrap();
    t.advance(&json!({"stage":"interpretation", "payload":{
        "dialogue_act":"information_request", "relation":"new", "topic":"language",
        "question":"枕詞ってどういう意味？", "target":"枕詞", "target_status":"explicit",
        "facet":"meaning"}}))
        .unwrap();
    t
}
fn reply_turn() -> Turn {
    let mut t = lookup_turn();
    t.advance(
        &json!({"stage":"lookup", "lookup":{"status":"found", "facts":[
            {"id":"local:1", "sources":[{"source_id":"local-card"}]}
        ]}}),
    )
    .unwrap();
    t.generated(json!({})).unwrap();
    t
}

#[test]
fn generation_is_bounded_and_out_of_order_frames_cannot_skip_lookup() {
    let mut t = turn();
    assert!(t.generated(json!({})).is_err());
    assert!(t.advance(&json!({"stage":"reply", "payload":{}})).is_err());
    t.advance(&json!({"stage":"start"})).unwrap();
    assert_eq!(
        t.prompt_kind().unwrap(),
        ("language_dialogue_interpretation", 850)
    );
    t.generated(json!({})).unwrap();
    assert!(t.prompt_kind().is_err());
    assert!(t.generated(json!({})).is_err());
    let mut t = reply_turn();
    t.advance(&json!({"stage":"reply", "payload":null}))
        .unwrap();
    assert!(t.finished());
    assert!(t.prompt_kind().is_err());
    assert!(t.advance(&json!({"stage":"start"})).is_err());
}

#[test]
fn answer_requires_actual_fact_ids_and_cannot_invent_reference_urls() {
    for ids in [json!([]), json!(["made-up"]), json!(["local:1", "made-up"])] {
        let mut t = reply_turn();
        let outcome = t
            .advance(&json!({"stage":"reply", "payload":{
            "status":"answer", "text":"出典のない断言", "fact_ids":ids}}))
            .unwrap();
        assert_eq!(outcome["status"], "unsupported");
        assert_eq!(outcome["references"], json!([]));
    }
    let mut t = reply_turn();
    let outcome = t
        .advance(&json!({"stage":"reply", "payload":{
        "status":"answer", "text":"資料に基づく説明", "fact_ids":["local:1"],
        "references":[{"url":"https://made-up.invalid"}]}}))
        .unwrap();
    assert_eq!(outcome["status"], "answer");
    assert_eq!(outcome["references"], json!([{"source_id":"local-card"}]));
}

#[test]
fn missing_context_becomes_a_question_and_keeps_the_same_focus() {
    let mut t = reply_turn();
    let outcome = t
        .advance(&json!({"stage":"reply", "payload":{
        "status":"partial", "text":"未完成の説明", "fact_ids":["local:1"],
        "missing_kind":"context", "clarification":"どの文章に出てきたん？"}}))
        .unwrap();
    assert_eq!(outcome["status"], "clarify");
    assert_eq!(outcome["text"], "どの文章に出てきたん？");
    assert_eq!(t.state.focus.target, "枕詞");
    assert_eq!(t.state.focus.clarification, "どの文章に出てきたん？");
    assert!(!t.state.kanji_scope_confirmed);
}

#[test]
fn missing_evidence_ends_without_reply_generation_and_handoff_clears_focus() {
    let mut t = lookup_turn();
    let outcome = t
        .advance(&json!({"stage":"lookup", "lookup":{"facts":[]}}))
        .unwrap();
    assert_eq!(outcome["status"], "unsupported");
    assert!(t.prompt_kind().is_err());
    let mut t = turn();
    t.state.focus.target = "古い話題".into();
    t.state.kanji_scope_confirmed = true;
    t.advance(&json!({"stage":"start"})).unwrap();
    t.generated(Value::Null).unwrap();
    let outcome = t
        .advance(&json!({"stage":"interpretation", "payload":{
        "dialogue_act":"information_request", "relation":"switch", "topic":"minecraft"}}))
        .unwrap();
    assert_eq!(outcome["status"], "host_chat");
    assert_eq!(outcome["text"], "");
    assert!(t.state.focus.target.is_empty());
    assert!(!t.state.kanji_scope_confirmed);
    assert!(t.finished());
}
