use super::*;

#[test]
fn canonical_complete_request_and_fallback_goldens() {
    let rows: Vec<Value> = serde_json::from_str(include_str!("fixtures.json")).unwrap();
    assert!(rows.len() > 6500);
    for row in rows {
        let input: Input = serde_json::from_value(row["input"].clone()).unwrap();
        let output = prepare(row["model"].as_str(), &input);
        assert_eq!(
            serde_json::to_value(&output).unwrap(),
            row["expected"],
            "{}",
            row["name"]
        );
        if let Some(request) = output.request {
            request
                .validate()
                .unwrap_or_else(|error| panic!("{}: {error}", row["name"]));
        }
    }
}

#[test]
fn empty_or_unavailable_model_never_produces_a_request() {
    for text in ["", " \n\t\u{1c}", "🐈"].into_iter() {
        let input: Input = serde_json::from_value(json!({"user_text":text})).unwrap();
        assert!(prepare(None, &input).request.is_none());
        if text != "🐈" {
            assert!(prepare(Some("m"), &input).request.is_none());
        }
    }
}

#[test]
fn preparation_reuses_handoff_current_rows_and_does_not_infer_presence_from_history() {
    let input: Input=serde_json::from_value(json!({"user_text":"ヤギいる？",
        "conversation_turns":[{"role":"assistant","text":"ヤギがおるで"}],
        "observed_entities":[{"entity_id":" minecraft:CAT\n", "label":" 猫 "},{"entity_id":"cat","label":"duplicate"}],
    })).unwrap();
    let output = prepare(Some("m"), &input);
    let request = output.request.unwrap();
    assert_eq!(
        request.details.observations["observed_entities"],
        json!([{"entity_id":"cat","label":"猫"}])
    );
    assert!(!request.details.observations.to_string().contains("goat"));
    let mut handoff = handoff::Handoff::default();
    handoff.record_plan(&request, &output.fallback).unwrap();
    let result = handoff
        .resolve(
            handoff::Input {
                individual_names: vec![],
                schema_version: 1,
                source: "current_observation".into(),
                plan: output.fallback,
                topic_hits: vec![],
                observed_entities: vec![
                    handoff::Observation {
                        entity_id: " minecraft:CAT\n".into(),
                        label: " 猫 ".into(),
                    },
                    handoff::Observation {
                        entity_id: "cat".into(),
                        label: "duplicate".into(),
                    },
                ],
                topic_policy: None,
                look_target: None,
                native_catalog: false,
                name_context: None,
            },
            true,
        )
        .unwrap();
    assert_eq!(result.grounding.status, "unknown");
}

#[test]
fn malformed_observation_projection_fails_before_planning() {
    for raw in [Value::Null, json!(true), json!("goat"), json!([])] {
        assert!(
            serde_json::from_value::<Input>(json!({"user_text":"話", "observed_entities":[raw]}))
                .is_err()
        );
    }
    assert!(
        serde_json::from_value::<Input>(json!({"user_text":"話","injected_details":{}})).is_err()
    );
}

#[test]
fn raw_evidence_stays_distinct_and_disabled_repair_cannot_leak_pending() {
    let mut input: Input=serde_json::from_value(json!({"user_text":"違う、仲間になるのは無理ってこと", "raw_user_text":"原文には訂正なし",
        "conversation_turns":[{"turn_id":"q","role":"user","text":"違う","repair_action":"clarify_repair","repair_target_turn_id":"t","repair_target_quote":"前の返答"},{"turn_id":"q:reply","role":"assistant","text":"どんな意味？"}],
    })).unwrap();
    assert!(input.repair_enabled);
    let enabled = prepare(Some("m"), &input).request.unwrap();
    assert!(
        enabled
            .details
            .allowed_actions
            .contains(&Action::RepairConversation)
    );
    assert_eq!(enabled.details.current["raw_text"], "原文には訂正なし");
    input.repair_enabled = false;
    let disabled = prepare(Some("m"), &input).request.unwrap();
    assert_eq!(disabled.details.pending_repair, json!({}));
    assert!(
        disabled
            .details
            .allowed_actions
            .iter()
            .all(|action| !action.is_repair())
    );
}

#[test]
fn story_events_survive_preparation_but_unknown_reactions_do_not() {
    let full = "戦闘が終わり、川柳の相談へ戻った。".repeat(20);
    let input: Input = serde_json::from_value(json!({"user_text":"さっきの続きにしよう",
    "conversation_turns":[
        {"turn_id":"story:combat","role":"event","reaction":"story","text":full},
        {"turn_id":"silent:reply","role":"event","reaction":"silent","text":"発話なし。"},
        {"turn_id":"unknown","role":"event","reaction":"invented","text":"許可されない出来事。"}
    ]}))
    .unwrap();
    let request = prepare(Some("m"), &input).request.unwrap();
    request.validate().unwrap();
    assert_eq!(request.details.history.len(), 2);
    assert_eq!(request.details.history[0]["turn_id"], "story:combat");
    assert_eq!(request.details.history[0]["role"], "event");
    assert_eq!(request.details.history[0]["reaction"], "story");
    assert_eq!(request.details.history[0]["text"], clean(&full, 160));
    assert_eq!(request.details.history[1]["reaction"], "silent");
    for reaction in [json!("invented"), Value::Null, json!(true)] {
        let mut invalid = request.clone();
        invalid.details.history[0]["reaction"] = reaction;
        assert!(invalid.validate().is_err());
    }
}

#[test]
fn story_does_not_break_pending_clarification_but_silence_still_does() {
    let player = json!({"turn_id":"q","role":"user","text":"違う",
        "repair_action":"clarify_repair","repair_target_turn_id":"old:reply",
        "repair_target_quote":"前の返答"});
    let reply = json!({"turn_id":"q:reply","role":"assistant","text":"どんな意味？"});
    let expected = repair::pending(&[player.clone(), reply.clone()]);
    assert!(!expected.as_object().unwrap().is_empty());
    let mut input: Input = serde_json::from_value(json!({"user_text":"材料を集めたい",
        "conversation_turns":[
            player,
            {"turn_id":"story:before-reply","role":"event","reaction":"story","text":"敵の離脱を確認した。"},
            reply,
            {"turn_id":"story:after-reply","role":"event","reaction":"story","text":"川柳相談へ戻った。"}
        ]})).unwrap();
    assert!(!repair::has_signal(&input.user_text));
    let request = prepare(Some("m"), &input).request.unwrap();
    request.validate().unwrap();
    assert_eq!(request.details.pending_repair, expected);
    assert!(
        request
            .details
            .allowed_actions
            .contains(&Action::RepairConversation)
    );

    input
        .conversation_turns
        .as_array_mut()
        .unwrap()
        .push(json!({
            "turn_id":"silent:reply","role":"event","reaction":"silent","text":"沈黙を選んだ。"
        }));
    let request = prepare(Some("m"), &input).request.unwrap();
    request.validate().unwrap();
    assert_eq!(request.details.pending_repair, json!({}));
    assert!(
        request
            .details
            .allowed_actions
            .iter()
            .all(|a| !a.is_repair())
    );
}
