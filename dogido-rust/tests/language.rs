use dogido_rust::language::{State, Turn};
use serde_json::{Value, json};

fn turn() -> Turn {
    Turn::new(
        &json!({"operation_id":"t1", "text":"枕詞ってどういう意味？", "source":"voice",
        "history":[], "language_active":false, "language_state":State::default()}),
    )
    .unwrap()
}
fn interpretation() -> Value {
    json!({"dialogue_act":"information_request", "relation":"new", "topic":"language",
        "question":"枕詞ってどういう意味？", "target":"枕詞", "target_status":"explicit",
        "facet":"meaning", "alternatives":[],"evidence":[{"turn_id":"t1","quote":"枕詞ってどういう意味？"}],
        "search_terms":["枕詞"], "clarification":""})
}
fn generated(payload: Value) -> Value {
    json!({"text":payload.to_string(),"finish_reason":"stop"})
}
fn reply(ids: Value) -> Value {
    json!({"status":"answer", "text":"資料に基づく説明", "fact_ids":ids,
        "application":"資料を使う。","missing":""})
}
fn lookup_turn() -> Turn {
    let mut t = turn();
    t.advance(&json!({"stage":"start"})).unwrap();
    assert_eq!(
        t.generated(generated(interpretation())).unwrap()["command"],
        "lookup"
    );
    t
}
fn reply_turn() -> Turn {
    let mut t = lookup_turn();
    t.advance(
        &json!({"stage":"lookup", "lookup":{"status":"found", "facts":[
        {"id":"local:1", "sources":[{"source_id":"local-card"}]}]}}),
    )
    .unwrap();
    t
}

#[test]
fn generation_is_bounded_and_helper_cannot_submit_checked_model_frames() {
    let mut t = turn();
    assert!(t.generated(json!({})).is_err());
    assert!(
        t.advance(&json!({"stage":"reply", "payload":reply(json!([]))}))
            .is_err()
    );
    t.advance(&json!({"stage":"start"})).unwrap();
    assert_eq!(
        t.prompt_kind().unwrap(),
        ("language_dialogue_interpretation", 850)
    );
    assert!(
        t.advance(&json!({"stage":"interpretation", "payload":interpretation()}))
            .is_err()
    );
    assert!(
        t.advance(&json!({"stage":"lookup", "lookup":{"facts":[]}}))
            .is_err()
    );
    t.generated(generated(interpretation())).unwrap();
    assert!(t.prompt_kind().is_err());
    assert!(t.generated(json!({})).is_err());
    let mut t = reply_turn();
    assert_eq!(t.prompt_kind().unwrap(), ("language_dialogue_reply", 700));
    assert_eq!(
        t.generated(json!({"text":"bad"})).unwrap()["status"],
        "unsupported"
    );
    assert!(t.finished());
    assert!(t.prompt_kind().is_err());
    assert!(t.advance(&json!({"stage":"start"})).is_err());
    assert!(t.generated(generated(reply(json!(["local:1"])))).is_err());
}

#[test]
fn answer_requires_actual_fact_ids_and_cannot_invent_reference_urls() {
    for ids in [json!([]), json!(["made-up"]), json!(["local:1", "made-up"])] {
        let outcome = reply_turn().generated(generated(reply(ids))).unwrap();
        assert_eq!(outcome["status"], "unsupported");
        assert_eq!(outcome["references"], json!([]));
    }
    let outcome = reply_turn()
        .generated(generated(reply(json!(["local:1"]))))
        .unwrap();
    assert_eq!(outcome["status"], "answer");
    assert_eq!(outcome["references"], json!([{"source_id":"local-card"}]));
    let mut injected = reply(json!(["local:1"]));
    injected["references"] = json!([{"url":"https://made-up.invalid"}]);
    assert_eq!(
        reply_turn().generated(generated(injected)).unwrap()["status"],
        "unsupported"
    );
}

#[test]
fn missing_context_becomes_a_question_and_keeps_the_same_focus() {
    let mut t = reply_turn();
    let mut r = reply(json!(["local:1"]));
    r["status"] = "partial".into();
    r["missing_kind"] = "context".into();
    r["clarification"] = "どの文章に出てきたん？".into();
    let outcome = t.generated(generated(r)).unwrap();
    assert_eq!(outcome["status"], "clarify");
    assert_eq!(outcome["text"], "どの文章に出てきたん？");
    assert_eq!(t.state.focus.target, "枕詞");
    assert_eq!(t.state.focus.clarification, "どの文章に出てきたん？");
    assert!(!t.state.kanji_scope_confirmed);
}

#[test]
fn missing_evidence_ends_without_reply_generation_and_handoff_clears_focus() {
    let mut past_only = Turn::new(
        &json!({"operation_id":"t1", "text":"枕詞ってどういう意味？", "source":"voice",
        "history":[{"turn_id":"past","text":"枕詞の話","role":"user"}],
        "language_active":true,"language_state":State::default()}),
    )
    .unwrap();
    past_only.advance(&json!({"stage":"start"})).unwrap();
    let mut i = interpretation();
    i["evidence"] = json!([{"turn_id":"past","quote":"枕詞"}]);
    assert_eq!(
        past_only.generated(generated(i)).unwrap()["status"],
        "clarify"
    );
    assert!(past_only.state.focus.target.is_empty());
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
    let mut i = interpretation();
    i["relation"] = "switch".into();
    i["facet"] = "other".into();
    i["topic"] = "minecraft".into();
    let outcome = t.generated(generated(i)).unwrap();
    assert_eq!(outcome["status"], "host_chat");
    assert_eq!(outcome["text"], "");
    assert!(t.state.focus.target.is_empty());
    assert!(!t.state.kanji_scope_confirmed);
    assert!(t.finished());
}

#[test]
fn malformed_and_truncated_replies_cannot_bypass_checks_through_lookup() {
    for mut r in [reply(json!(["local:1"])), reply(json!(["made-up"]))] {
        r["status"] = "invented".into();
        let result = lookup_turn().advance(
            &json!({"stage":"lookup", "lookup":{"facts":[{"id":"local:1"}]},
            "fixed_reply":r}),
        );
        assert!(result.is_err());
    }
    let mut g = generated(reply(json!(["local:1"])));
    g["finish_reason"] = "length".into();
    assert_eq!(reply_turn().generated(g).unwrap()["status"], "unsupported");
    let mut t = turn();
    t.advance(&json!({"stage":"start"})).unwrap();
    let mut g = generated(interpretation());
    g["finish_reason"] = "max_tokens".into();
    assert_eq!(t.generated(g).unwrap()["status"], "clarify");
}

#[test]
fn native_prompts_cannot_be_replaced_by_helper_frames() {
    let mut t = turn();
    assert!(t.request("fixture").is_err());
    assert_eq!(
        t.advance(&json!({"stage":"start"})).unwrap()["command"],
        "generate"
    );
    let request = t.request("fixture").unwrap();
    assert_eq!(request.max_tokens, 850);
    assert_eq!(request.temperature, 0.0);
    assert!(!request.enable_thinking);
    assert!(t.advance(&json!({"stage":"prompt","messages":[]})).is_err());
    t.generated(generated(interpretation())).unwrap();
    assert!(t.request("fixture").is_err());
}

#[test]
fn explicit_kana_answer_needs_no_helper_lookup_or_reply_generation() {
    let mut t = Turn::new(
        &json!({"operation_id":"t1", "text":"音数を教えて。きょう", "source":"voice",
        "history":[], "language_active":false,"language_state":State::default()}),
    )
    .unwrap();
    t.advance(&json!({"stage":"start"})).unwrap();
    let mut i = interpretation();
    i["target"] = "きょう".into();
    i["facet"] = "mora_count".into();
    i["question"] = "音数を教えて。きょう".into();
    i["evidence"] = json!([{"turn_id":"t1","quote":"きょう"}]);
    let outcome = t.generated(generated(i)).unwrap();
    assert_eq!(outcome["command"], "done");
    assert_eq!(outcome["text"], "「きょう」は2音やで。");
    assert_eq!(outcome["references"], json!([]));
    assert!(t.request("fixture").is_err());
    assert!(
        t.advance(&json!({"stage":"lookup","lookup":{"facts":[]}}))
            .is_err()
    );
}

#[test]
fn grade_answer_uses_only_matching_character_allocation_and_preserves_sources() {
    let input = json!({"operation_id":"t1","text":"漢字の3は何年生で習う？","source":"typed",
        "history":[],"language_active":false,"language_state":State::default()});
    for grade in [json!(1), json!(true), json!(1.0), json!("1"), json!(7)] {
        let mut t = Turn::new(&input).unwrap();
        t.advance(&json!({"stage":"start"})).unwrap();
        let mut i = interpretation();
        i["target"] = "3".into();
        i["facet"] = "grade".into();
        i["evidence"] = json!([{"turn_id":"t1","quote":input["text"]}]);
        assert_eq!(t.generated(generated(i)).unwrap()["command"], "lookup");
        let outcome=t.advance(&json!({"stage":"lookup","lookup":{"facts":[
            {"id":"table:three","sources":[{"source_id":"school-table"}],
             "allocation":{"character":"三","school_grade":grade,"scope":"character_only"}}],"status":"searched"}})).unwrap();
        if grade.as_u64() == Some(1) {
            assert_eq!(outcome["text"], "「三」は小学1年生で習う漢字やで。");
            assert_eq!(outcome["references"], json!([{"source_id":"school-table"}]));
            assert!(t.state.kanji_scope_confirmed);
            assert!(t.request("fixture").is_err());
        } else {
            assert_eq!(outcome["command"], "generate");
            assert_eq!(t.request("fixture").unwrap().max_tokens, 700);
        }
    }
}
