use super::*;

static FIXTURES: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("fixtures.json")).unwrap());

fn request(action: &str) -> (Value, Value) {
    let context = &FIXTURES["contexts"][0];
    let mut frame = context["frame"].clone();
    let mut details = context["details"].clone();
    frame["text"] = "それでええよ".into();
    details["player_text"] = frame["text"].clone();
    details["original_player_text"] = frame["text"].clone();
    details["allowed_actions"] = ASSETS["actions"].clone();
    details["allowed_actions"]
        .as_array_mut()
        .unwrap()
        .push(json!("acknowledge_meaning"));
    details["pending_verse"] = "新しい案\n中の行\n最後の行".into();
    details["workshop_context"]["discussion_target"] = json!({"line_index":0});
    frame["workshop"]["conversation_candidate"] = json!({"proposal":{
        "line_index":0,"target_fragment":"","replacement_text":"松明"}});
    frame["payload"] = json!({"action":action,"speech":"その言葉でいこか。"});
    (frame, details)
}

#[test]
fn interpreted_actions_do_not_require_verbatim_evidence_or_confidence() {
    for action in [
        "accept_pending",
        "reject_pending",
        "stage_conversation_candidate",
        "propose_revision",
        "close_workshop",
        "clear_lessons",
    ] {
        for metadata in [
            json!({}),
            json!({"confidence":0.0,"evidence":"意味を解釈した記録"}),
            json!({"confidence":1.5,"evidence":""}),
            json!({"confidence":"unknown","evidence":{},"checks":"unused","unused":true}),
        ] {
            let (mut frame, details) = request(action);
            frame["payload"]
                .as_object_mut()
                .unwrap()
                .extend(metadata.as_object().unwrap().clone());
            let before = frame.clone();
            let output = decode(&frame, &details).unwrap();
            assert_eq!(output["step"]["action"], action, "{action}: {output}");
            assert_eq!(frame, before);
        }
    }
}

#[test]
fn wording_does_not_reclassify_the_selected_action() {
    for input in [
        "もしそれにしたら？",
        "『その案で』と言った",
        "変更しない",
        "ん",
    ] {
        let (mut frame, mut details) = request("stage_conversation_candidate");
        frame["text"] = input.into();
        details["player_text"] = input.into();
        details["original_player_text"] = input.into();
        assert_eq!(
            decode(&frame, &details).unwrap()["step"]["action"],
            "stage_conversation_candidate"
        );
    }
}

#[test]
fn interpreted_replacement_and_target_survive_without_raw_word_matching() {
    let (mut frame, mut details) = request("stage_player_edit");
    frame["text"] = "紙の句を松松がにしましょう".into();
    details["player_text"] = frame["text"].clone();
    details["original_player_text"] = frame["text"].clone();
    frame["payload"]["line_reference"] =
        json!({"found":true,"concept_id":"line_1","evidence":"上五","confidence":0.01});
    frame["payload"]["line_proposal"] = json!({"found":true,"replacement_text":"松明","target_fragment":"","evidence":"上五を松明にする"});
    let result = decode(&frame, &details).unwrap();
    assert_eq!(result["step"]["action"], "stage_player_edit");
    assert_eq!(
        result["step"]["analysis"]["line_proposal"]["replacement_text"],
        "松明"
    );
    assert_eq!(result["step"]["analysis"]["line_proposal"]["line_index"], 0);
    // 同じ内容の相談候補があっても、コードが別のactionへ付け替えない。
    assert!(frame["workshop"]["conversation_candidate"].is_object());
}

#[test]
fn execution_still_needs_an_available_action_and_its_current_target() {
    for action in ["accept_pending", "reject_pending"] {
        let (frame, mut details) = request(action);
        details["pending_verse"] = Value::Null;
        assert_eq!(
            decode(&frame, &details).unwrap()["reason"],
            "pending_required"
        );
    }
    let (mut frame, details) = request("stage_conversation_candidate");
    frame["workshop"]["conversation_candidate"] = Value::Null;
    assert_eq!(
        decode(&frame, &details).unwrap()["reason"],
        "conversation_candidate_missing"
    );
    let (frame, mut details) = request("propose_revision");
    details["workshop_context"]["discussion_target"] = Value::Null;
    assert_eq!(
        decode(&frame, &details).unwrap()["reason"],
        "repair_target_required"
    );
    let (mut frame, details) = request("stage_player_edit");
    frame["payload"]["line_proposal"] = json!({"found":true,"replacement_text":" "});
    assert_eq!(
        decode(&frame, &details).unwrap()["reason"],
        "line_proposal_required"
    );
    for action in ["resume_workshop", "decline_resume", "run_shell"] {
        let (frame, mut details) = request(action);
        details["allowed_actions"]
            .as_array_mut()
            .unwrap()
            .push(json!(action));
        assert!(decode(&frame, &details).unwrap()["step"].is_null());
    }
    let (frame, mut details) = request("clear_lessons");
    details["allowed_actions"] = json!(["respond"]);
    assert_eq!(
        decode(&frame, &details).unwrap()["reason"],
        "action_not_allowed"
    );
}

#[test]
fn optional_metadata_does_not_discard_speech_findings_or_praise() {
    let (mut frame, details) = request("explain");
    frame["payload"]["confidence"] = 0.01.into();
    frame["payload"]["evidence"] = "理解した指摘".into();
    frame["payload"]["findings"] = json!([{"line_index":0,"fragment":"モデルの表記","problem":"preference","note":"本人の希望","confidence":0.01}]);
    frame["payload"]["close_after_action"] = true.into();
    let result = decode(&frame, &details).unwrap();
    assert_eq!(result["step"]["speech"], frame["payload"]["speech"]);
    assert_eq!(result["step"]["analysis"]["findings"][0]["line_index"], 0);
    assert_eq!(
        result["step"]["analysis"]["findings"][0]["note"],
        "本人の希望"
    );
    assert_eq!(result["step"]["close_after_action"], false);
    for value in [
        json!({"kind":"praise"}),
        json!({"kind":"praise","evidence":"しっくりきた様子"}),
        json!({"kind":"praise","evidence":[],"confidence":0.01}),
    ] {
        assert_eq!(feedback(&value).unwrap()["kind"], "praise");
    }
    assert!(feedback(&json!({"kind":"clear_lessons"})).is_none());
    assert!(feedback(&Value::Null).is_none());
}

#[test]
fn inspection_requires_only_its_read_arguments() {
    let (mut frame, details) = request("inspect");
    assert_eq!(
        decode(&frame, &details).unwrap()["reason"],
        "inspection_check_required"
    );
    frame["payload"] = json!({"action":"inspect","checks":["reading","source","reading"],"inspect_text":"白い骨","inspect_line_index":2});
    let result = decode(&frame, &details).unwrap();
    assert_eq!(result["step"]["checks"], json!(["reading", "source"]));
    assert_eq!(result["step"]["inspect_text"], "白い骨");
    frame["payload"]["inspect_line_index"] = 3.into();
    assert_eq!(
        decode(&frame, &details).unwrap()["reason"],
        "invalid_inspection_line_index"
    );
    frame["payload"]["inspect_line_index"] = 2.into();
    for check in ["meter", "shell"] {
        frame["payload"]["checks"] = json!([check]);
        assert_eq!(
            decode(&frame, &details).unwrap()["reason"],
            "invalid_checks"
        );
    }
}

#[test]
fn conversation_uses_body_and_ignores_irrelevant_operation_fields() {
    for speech in [
        "うん。",
        "『ふゆむ』の響きから、草が風に揺れる感じを受け取ったわ。",
        "長い話でも\nそのまま返せるで。",
    ] {
        let (mut frame, details) = request("respond");
        frame["payload"] = json!({"action":"respond","speech":speech,"evidence":[],"confidence":{},"checks":"meter"});
        assert_eq!(decode(&frame, &details).unwrap()["step"]["speech"], speech);
    }
    for speech in [Value::Null, json!("　\n"), json!(42)] {
        let (mut frame, details) = request("respond");
        frame["payload"]["speech"] = speech;
        assert_eq!(
            decode(&frame, &details).unwrap()["reason"],
            "invalid_speech"
        );
    }
}

#[test]
fn old_input_corpus_does_not_reclassify_actions_or_mutate_inputs() {
    // 過去の拒否期待値は削除済み。多様な外形を読む回帰入力としてだけ保持する。
    for case in array(&FIXTURES["cases"]) {
        let context = &FIXTURES["contexts"][case["context"].as_u64().unwrap() as usize];
        let mut frame = context["frame"].clone();
        frame["payload"] = case["payload"].clone();
        let before = frame.clone();
        if let Ok(result) = decode(&frame, &context["details"])
            && let Some(step) = result["step"].as_object()
        {
            assert_eq!(
                step["action"], frame["payload"]["action"],
                "{}",
                case["name"]
            );
            assert!(
                strings(&context["details"]["allowed_actions"]).contains(text(&step["action"]))
            );
        }
        assert_eq!(frame, before);
    }
}

#[test]
fn oversized_input_and_broken_internal_projection_fail_before_decoding() {
    let (mut frame, details) = request("respond");
    frame["payload"]["speech"] = json!("a".repeat(1_000_000));
    assert!(
        decode(&frame, &details)
            .unwrap_err()
            .to_string()
            .contains("too large")
    );
    frame["payload"] = json!({"action":"respond","speech":"うん。"});
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
        let mut broken = details.clone();
        broken[key] = Value::Null;
        assert!(decode(&frame, &broken).is_err(), "{key}");
    }
}
