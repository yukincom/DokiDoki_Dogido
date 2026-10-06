use super::*;

#[test]
fn praise_feedback_is_optional_and_cannot_authorize_closing() {
    let input = "余韻が心に残る。今日はここまでにしよう";
    let valid = json!({"kind":"praise","evidence":"余韻が心に残る"});
    let (base, details) = conversation(input, "また詠もな。", "close_workshop");
    assert_eq!(
        validate(&base, &details).unwrap()["step"]["action"],
        "close_workshop"
    );
    for (feedback, accepted) in [
        (valid.clone(), true),
        (Value::Null, false),
        (json!("praise"), false),
        (json!({"kind":"other","evidence":"余韻が心に残る"}), false),
        (json!({"kind":"praise"}), false),
        (json!({"kind":"praise","evidence":4}), false),
        (json!({"kind":"praise","evidence":""}), false),
        (json!({"kind":"praise","evidence":" "}), false),
        (json!({"kind":"praise","evidence":"気に入った"}), false),
        (json!({"kind":"praise","evidence":"余韻が 心に残る"}), false),
        (
            json!({"kind":"praise","evidence":"余韻が心に残る","confidence":0.95}),
            false,
        ),
    ] {
        let mut frame = base.clone();
        frame["payload"]["feedback"] = feedback.clone();
        let result = validate(&frame, &details).unwrap();
        assert_eq!(
            result["step"]["action"], "close_workshop",
            "{feedback}: {result}"
        );
        assert_eq!(result["step"]["speech"], "また詠もな。");
        assert!(result["step"].get("feedback").is_none());
        assert_eq!(
            super::feedback(&feedback, input),
            accepted.then_some(valid.clone())
        );
    }
    for input in ["ここで終わろうか？", "終了しないよ", "『終了』と言っただけ"]
    {
        let (mut frame, details) = conversation(input, "", "close_workshop");
        frame["payload"]["feedback"] = json!({"kind":"praise","evidence":input});
        assert!(
            validate(&frame, &details).unwrap()["step"].is_null(),
            "{input}"
        );
    }
    let (mut frame, details) = conversation(input, "ありがとう。", "respond");
    frame["payload"]["feedback"] = valid;
    let result = validate(&frame, &details).unwrap();
    assert_eq!(result["step"]["action"], "respond");
    assert!(result["step"].get("feedback").is_none());
}

#[test]
fn praise_evidence_uses_raw_input_without_semantic_or_lexical_reconstruction() {
    let (mut frame, mut details) = conversation("余韻が残る。今日は終了", "", "close_workshop");
    details["player_text"] = "余韻が心に残る。今日は終了".into();
    frame["payload"]["evidence"] = "今日は終了".into();
    frame["payload"]["feedback"] = json!({"kind":"praise","evidence":"余韻が心に残る"});
    let result = validate(&frame, &details).unwrap();
    assert_eq!(result["step"]["action"], "close_workshop");
    assert!(super::feedback(&frame["payload"]["feedback"], text(&frame["text"])).is_none());
    let long = "句".repeat(121);
    assert!(contract::feedback(&json!({"kind":"praise","evidence":long}), &long).is_none());
}
static FIXTURES: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("fixtures.json")).unwrap());
// This corpus predates model-owned conversational decisions. Preserve its
// structural/authority cases; exact prose/lexical-policy parity is no longer the contract.
#[test]
fn canonical_corpus_preserves_structural_and_execution_boundaries() {
    let mut accepted = 0;
    let mut structural_rejections = 0;
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
        let legacy_errors = array(&case["expected"]["contract_errors"]);
        let read_only = conversation::handles(text(&frame["payload"]["action"]));
        if !read_only && legacy_errors.iter().any(|e| e != "speech:not_allowed") {
            assert!(
                actual["step"].is_null(),
                "structural case {}: {}",
                case["name"],
                actual
            );
            structural_rejections += 1;
        }
        if actual["step"].is_object() {
            accepted += 1;
            let step = &actual["step"];
            let action = text(&step["action"]);
            if step["action"] != frame["payload"]["action"] {
                assert_eq!(frame["payload"]["action"], "stage_player_edit");
                assert_eq!(step["action"], "stage_conversation_candidate");
                let p = &frame["workshop"]["conversation_candidate"]["proposal"];
                assert_eq!(
                    frame["payload"]["line_proposal"]["replacement_text"],
                    p["replacement_text"]
                );
                assert_eq!(
                    frame["payload"]["line_proposal"]["target_fragment"],
                    p["target_fragment"]
                );
            }
            assert!(strings(&context["details"]["allowed_actions"]).contains(action));
            assert_eq!(
                step["speech"],
                sanitize::strip(text(&frame["payload"]["speech"]))
            );
            if ASSETS["mutation_purposes"].get(action).is_some() {
                assert!(step["confidence"].as_f64().unwrap() >= 0.85);
                assert!(text(&frame["text"]).contains(text(&step["evidence"])));
                assert!(workshop_input_guard::state_change_safe(
                    action,
                    text(&frame["text"]),
                    text(&step["evidence"])
                ));
            }
        }
        assert_eq!(frame["payload"], case["payload"]);
    }
    assert!(accepted > 100 && structural_rejections > 100);
    assert_eq!(array(&FIXTURES["cases"]).len(), 3330);
}

fn conversation(input: &str, speech: &str, action: &str) -> (Value, Value) {
    let context = &FIXTURES["contexts"][0];
    let mut frame = context["frame"].clone();
    let mut details = context["details"].clone();
    frame["text"] = input.into();
    details["player_text"] = input.into();
    details["original_player_text"] = input.into();
    frame["payload"] = json!({"action":action,"purpose":if action=="close_workshop" {"finish_workshop"} else {"understand_meaning"},
        "confidence":0.95,"evidence":input,"speech":speech,"checks":[]});
    (frame, details)
}

#[test]
fn live_conversation_metadata_cannot_discard_speech_or_execute_an_action() {
    for (input, speech, extra) in [
        (
            "白い塩?",
            "骨粉の白さを塩に見立てた読みやな。",
            json!({"evidence":"しろいしお"}),
        ),
        (
            "コップンゆらいならシンプルに白い骨の方がいいんじゃないでしょうか",
            "白い骨の方が、直接伝わる感じやな。",
            json!({}),
        ),
        (
            "ん",
            "この句の話、続けよか。",
            json!({"evidence":"この子はもう完成でいいんじゃないかな","confidence":0.1}),
        ),
        (
            "そうなん？",
            "うん、その響きから考えたんや。",
            json!({"purpose":false,"confidence":[],"evidence":{},"checks":"meter","close_after_action":true,"close_evidence":"終了","line_reference":[],"findings":[{"problem":"meter","fragment":"さくらのは","confidence":0.95}],"line_proposal":false,"unused":true}),
        ),
    ] {
        let (mut frame, details) = conversation(input, speech, "respond");
        frame["payload"] = json!({"action":"respond","speech":speech});
        frame["payload"]
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        let result = validate(&frame, &details).unwrap();
        assert_eq!(result["reason"], "accepted", "{result}");
        assert_eq!(result["step"]["speech"], speech);
        assert_eq!(result["step"]["checks"], json!([]));
        assert_eq!(result["step"]["close_after_action"], false);
        assert_eq!(result["step"]["analysis"]["findings"], json!([]));
    }
    for speech in [Value::Null, json!("　\n"), json!(42)] {
        let (mut frame, details) = conversation("相談しよ", "", "respond");
        frame["payload"] = json!({"action":"respond","speech":speech});
        assert_eq!(
            validate(&frame, &details).unwrap()["reason"],
            "invalid_speech"
        );
    }
    // 同じ省略を保存・終了の許可には使わない。
    for action in ["close_workshop", "stage_player_edit", "propose_revision"] {
        let (mut frame, details) = conversation("ん", "はい。", action);
        frame["payload"] = json!({"action":action,"speech":"できたで。"});
        assert!(validate(&frame, &details).unwrap()["step"].is_null());
    }
}

#[test]
fn conversational_acceptance_does_not_authorize_unverified_feedback() {
    let (mut frame, details) =
        conversation("白い塩?", "骨粉の白さを塩に見立てた読みやな。", "explain");
    let verified = validate(&frame, &details).unwrap();
    assert_eq!(verified["step"]["analysis"]["feedback_allowed"], true);
    frame["payload"]
        .as_object_mut()
        .unwrap()
        .remove("confidence");
    frame["payload"]["evidence"] = json!("しろいしお");
    let rescued = validate(&frame, &details).unwrap();
    assert_eq!(rescued["reason"], "accepted");
    assert_eq!(rescued["step"]["purpose"], "understand_meaning");
    assert_eq!(rescued["step"]["analysis"]["feedback_allowed"], false);
}

#[test]
fn inspection_needs_only_the_requested_tool_arguments() {
    let (mut frame, details) = conversation("読みを確認してみよう", "", "inspect");
    frame["payload"] = json!({"action":"inspect","checks":["reading","meter","reading"],"inspect_text":"白い骨","inspect_line_index":2});
    let result = validate(&frame, &details).unwrap();
    assert_eq!(result["reason"], "accepted");
    assert_eq!(result["step"]["checks"], json!(["reading", "meter"]));
    assert_eq!(result["step"]["inspect_text"], "白い骨");
    assert_eq!(result["step"]["inspect_line_index"], 2);
    frame["payload"]["inspect_line_index"] = json!(3);
    assert!(validate(&frame, &details).unwrap()["step"].is_null());
    frame["payload"]["inspect_line_index"] = json!(2);
    assert_eq!(result["step"]["close_after_action"], false);
    frame["payload"]["checks"] = json!(["shell"]);
    assert!(validate(&frame, &details).unwrap()["step"].is_null());
    frame["payload"]["checks"] = json!(["reading"]);
    frame["payload"]["inspect_text"] = json!("骨".repeat(161));
    assert!(validate(&frame, &details).unwrap()["step"].is_null());
}

#[test]
fn one_character_acknowledgement_does_not_require_operation_evidence() {
    let (mut frame, mut details) = conversation("ん", "うん。", "respond");
    details["allowed_actions"]
        .as_array_mut()
        .unwrap()
        .push(json!("acknowledge_meaning"));
    frame["payload"] = json!({"action":"acknowledge_meaning","speech":"うん。"});
    let result = validate(&frame, &details).unwrap();
    assert_eq!(result["reason"], "accepted");
    crate::workshop_followup::validate(
        &result["step"],
        "ん",
        crate::workshop_followup::Stage::MeaningExplained,
        false,
    )
    .unwrap();
    assert_eq!(
        crate::workshop_followup::Stage::after_completed(
            "acknowledge_meaning",
            "understand_meaning",
            false
        ),
        crate::workshop_followup::Stage::Discussion
    );
}

#[test]
fn actual_player_approved_explanations_are_not_screened_by_prose_or_origin_words() {
    for (input, speech) in [
        (
            "今はふゆむ由来の話をしてる",
            "ふゆむは温帯の夜に草が冷えて硬く、静かに揺れる様子やな。冬じゃなく、夜の冷たさや重さを感じたんやろ？",
        ),
        (
            "ふゆむ由来どこから",
            "『ふゆ』は冬やなく草が風に揺れる揺らぎで、『む』は草むらの響きや。",
        ),
        (
            "踏むの言い間違え？",
            "『ふゆむ』は『冬む』やなくて、『草を踏む』の『ふむ』やと思うてな。雪もないのに、なぜ『冬』って響きが出たんやろうか？",
        ),
        (
            "もう少し説明して",
            "うん。\n『ふゆむ』の響きから、夜の冷たさを感じるわ。",
        ),
    ] {
        let (frame, details) = conversation(input, speech, "explain");
        let result = validate(&frame, &details).unwrap();
        assert_eq!(result["reason"], "accepted", "{result}");
        assert_eq!(result["step"]["speech"], speech);
    }
}

#[test]
fn model_selects_natural_close_and_words_but_cannot_close_a_negation() {
    let (frame, details) = conversation(
        "きょうはここらで区切っとこか",
        "うん、また気になったら続きを話そな。",
        "close_workshop",
    );
    let result = validate(&frame, &details).unwrap();
    assert_eq!(result["reason"], "accepted", "{result}");
    assert_eq!(result["step"]["speech"], frame["payload"]["speech"]);
    for input in [
        "おわらんわ。",
        "終わりにしない",
        "『終了』ってどういう意味？",
        "終了したらどうなる？",
    ] {
        let (frame, details) = conversation(input, "まだ話そな。", "close_workshop");
        assert!(
            validate(&frame, &details).unwrap()["step"].is_null(),
            "{input}"
        );
    }
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

#[test]
fn generated_repair_must_keep_the_retained_line_until_player_selects_another() {
    let (mut frame, mut details) = conversation("もっと自然に直して", "", "propose_revision");
    details["workshop_context"]["discussion_target"] = json!({"line_index":2});
    frame["payload"]["purpose"] = "improve_wording".into();
    let lines: Vec<_> = text(&details["working_reading"])
        .lines()
        .map(str::to_owned)
        .collect();
    assert_eq!(lines.len(), 3);
    frame["payload"]["findings"] = json!([{"line_index":0,"fragment":lines[0],"problem":"preference","note":"自然にしたい","confidence":0.95}]);
    assert_eq!(
        validate(&frame, &details).unwrap()["reason"],
        "repair_target_conflict"
    );
    frame["payload"]["findings"][0]["line_index"] = 2.into();
    frame["payload"]["findings"][0]["fragment"] = lines[2].clone().into();
    assert_eq!(validate(&frame, &details).unwrap()["reason"], "accepted");
}

#[test]
fn short_edit_selects_only_the_exact_current_candidate_with_current_evidence() {
    let (mut frame, mut details) =
        conversation("うん！変えて！", "その案に変えたで。", "stage_player_edit");
    details["allowed_actions"]
        .as_array_mut()
        .unwrap()
        .push(json!("stage_conversation_candidate"));
    frame["payload"]["purpose"] = "improve_wording".into();
    frame["payload"]["line_proposal"] = json!({"found":true,"replacement_text":"くささむし",
        "target_fragment":"のくさふゆむ","evidence":"のくさふゆむをくささむしに変更しよう","confidence":0.95});
    frame["workshop"]["conversation_candidate"] = json!({"proposal":{"line_index":2,
        "target_fragment":"のくさふゆむ","replacement_text":"くささむし"},"validation_codes":[]});
    let result = validate(&frame, &details).unwrap();
    assert_eq!(
        result["step"]["action"], "stage_conversation_candidate",
        "{result}"
    );
    for (key, value) in [
        ("replacement_text", "くさむしり"),
        ("target_fragment", "てつじんみまもる"),
    ] {
        let mut wrong = frame.clone();
        wrong["payload"]["line_proposal"][key] = value.into();
        assert!(validate(&wrong, &details).unwrap()["step"].is_null());
    }
    let mut wrong = frame.clone();
    wrong["payload"]["evidence"] = "のくさふゆむをくささむしに変更しよう".into();
    assert!(validate(&wrong, &details).unwrap()["step"].is_null());
    wrong = frame.clone();
    wrong["workshop"]["conversation_candidate"] = Value::Null;
    assert!(validate(&wrong, &details).unwrap()["step"].is_null());
    for input in [
        "今度は上五をそれに変えて",
        "変えないで！",
        "『変えて』って言っただけ",
        "変えていい？",
        "変えたらどうなる？",
    ] {
        let mut negative = frame.clone();
        let mut d = details.clone();
        negative["text"] = input.into();
        negative["payload"]["evidence"] = input.into();
        d["player_text"] = input.into();
        d["original_player_text"] = input.into();
        assert!(
            validate(&negative, &d).unwrap()["step"].is_null(),
            "{input}"
        );
    }
}
