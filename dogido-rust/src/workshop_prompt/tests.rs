use super::*;
use crate::haiku_bridge::Helper;
use std::path::Path;

static FIXTURES: LazyLock<Value> = LazyLock::new(|| {
    let mut fixture: Value =
        serde_json::from_str(include_str!("fixtures.json")).expect("canonical workshop fixtures");
    let contents = fixture["message_contents"].clone();
    for group in ["projection_cases", "pure_cases"] {
        for case in fixture[group].as_array_mut().unwrap() {
            for message in case["expected"]["messages"].as_array_mut().unwrap() {
                let message = message.as_object_mut().unwrap();
                let reference = message.remove("content_ref").unwrap().as_u64().unwrap() as usize;
                message.insert("content".into(), contents[reference].clone());
            }
        }
    }
    fixture
});

#[test]
fn all_python_consultation_prompts_match_exactly() {
    let mut count = 0;
    for group in ["projection_cases", "pure_cases"] {
        for case in FIXTURES[group].as_array().unwrap() {
            let input = case["prepared"].clone();
            let retry = case["retry"].clone();
            let actual =
                prepare(&input, Some(&retry)).unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
            assert_eq!(actual, case["expected"], "{}", case["name"]);
            assert_eq!(input, case["prepared"]);
            assert_eq!(retry, case["retry"]);
            let messages = actual["messages"].as_array().unwrap();
            assert_eq!(
                messages.len(),
                if input.get("fixed_payload").is_none() && truth(&retry) {
                    3
                } else {
                    2
                }
            );
            count += 1;
        }
    }
    assert_eq!(count, 670);
    let keys = [
        "raw_semantic",
        "current_idea",
        "meaning_ack",
        "confirm_close",
        "resume",
        "followup",
        "editing",
        "conversation_candidate",
        "propose_revision",
        "pending",
        "after_validation",
    ];
    assert_eq!(ASSETS["extra_order"], json!(keys));
}

#[test]
fn editable_templates_preserve_every_default_and_apply_only_requested_instructions() {
    let mut settings = editable_defaults();
    let assets = editable_assets(&settings).unwrap();
    for group in ["projection_cases", "pure_cases"] {
        for case in FIXTURES[group].as_array().unwrap() {
            assert_eq!(
                prepare_with_assets(&case["prepared"], Some(&case["retry"]), &assets).unwrap(),
                case["expected"]
            );
        }
    }
    settings["system"] = "変更した指示".into();
    settings["main"] = format!(
        "{}\n分からないことは分からないと答えて。",
        settings["main"].as_str().unwrap()
    )
    .into();
    let changed = editable_assets(&settings).unwrap();
    let case = &FIXTURES["projection_cases"][0];
    let result = prepare_with_assets(&case["prepared"], None, &changed).unwrap();
    assert_eq!(result["messages"][0]["content"], "変更した指示");
    assert!(
        result["messages"][1]["content"]
            .as_str()
            .unwrap()
            .ends_with("分からないことは分からないと答えて。")
    );
    settings["main"] = settings["main"]
        .as_str()
        .unwrap()
        .replace("{{player}}", "")
        .into();
    assert!(editable_assets(&settings).is_err());
}

#[test]
fn malformed_projection_is_not_a_legacy_prompt_or_model_fallback() {
    let good = FIXTURES["projection_cases"][0]["prepared"].clone();
    for invalid in [
        Value::Null,
        json!({"messages":[]}),
        json!({"details":null}),
        json!({"details":good["details"],"messages":[]}),
        json!({"details":good["details"],"fixed_payload":null}),
        json!({"details":good["details"],"fixed_payload":"model prose"}),
    ] {
        assert!(prepare(&invalid, None).is_err());
    }
    for (field, value) in [
        ("allowed_actions", json!(["ask", 1])),
        ("player_text", json!(1)),
        ("original_player_text", Value::Null),
        ("workshop_context", json!([])),
        ("tool_observation", json!("unvalidated")),
        ("turn_steps", json!(["not-step"])),
    ] {
        let mut invalid = good.clone();
        invalid["details"][field] = value;
        assert!(prepare(&invalid, None).is_err(), "{field}");
    }
}

#[test]
fn assembled_response_preserves_former_frame_limit_for_retry_and_fixed_edit() {
    for fixed in [false, true] {
        let mut input = FIXTURES["projection_cases"][0]["prepared"].clone();
        input["details"]["player_text"] = json!("猫".repeat(340_000));
        if fixed {
            input["fixed_payload"] = json!({"action":"stage_player_edit"});
        }
        assert!(
            prepare(&input, None)
                .unwrap_err()
                .to_string()
                .contains("too large")
        );
    }
    let input = FIXTURES["projection_cases"][0]["prepared"].clone();
    assert!(prepare(&input, Some(&json!({"payload":"a".repeat(1_000_000)}))).is_err());
}

fn stopped(pid: u32) {
    assert_eq!(unsafe { libc::kill(pid as i32, 0) }, -1, "child remains");
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ESRCH)
    );
}
fn directory() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("dogido-workshop-prompt-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}
fn child(dir: &Path, response: &Value, block: bool) -> Helper {
    std::fs::write(dir.join("response"), format!("{}\n", python_json(response))).unwrap();
    let reply = if block {
        "read -r never"
    } else {
        "cat response"
    };
    // Paths are only a generated temporary directory, never user text.
    let script = format!(
        "cd '{}'\nprintf '%s' \"$$\" > pid\nread -r line || exit 0\nprintf '%s\\n' \"$line\" > request\n{reply}\nwhile read -r line; do\nprintf '%s\\n' \"$line\" >> request\nprintf '{{\"step\":null,\"reason\":\"mock\"}}\\n'\ndone\n",
        dir.display()
    );
    let path = dir.join("helper.sh");
    std::fs::write(&path, script).unwrap();
    Helper::start(Path::new("/bin/sh"), &path).unwrap()
}
fn pid(dir: &Path) -> u32 {
    std::fs::read_to_string(dir.join("pid"))
        .unwrap()
        .parse()
        .unwrap()
}

#[tokio::test]
async fn native_preparation_uses_no_helper_for_nonedit_or_kana_fragments() {
    for index in [0, 2, 579] {
        let case = &FIXTURES["projection_cases"][index];
        let dir = directory();
        let mut helper = child(&dir, &Value::Null, true);
        let mut frame = case["frame"].clone();
        let (prepared, details) = super::super::prepare_consultation(
            &mut helper,
            &mut crate::workshop_editing::Engine::default(),
            &mut frame,
        )
        .await
        .unwrap();
        let expected = prepare(
            &json!({"details":case["prepared"]["details"]}),
            frame.get("retry"),
        )
        .unwrap();
        assert_eq!(prepared, expected);
        assert!(prepared.get("fixed_payload").is_none());
        assert_eq!(details, case["prepared"]["details"]);
        assert_eq!(frame, case["frame"]);
        frame["payload"] = json!({"action":"ask","purpose":"continue_discussion","confidence":0.9,"evidence":frame["text"],"speech":"気になるところを教えてな。","checks":[]});
        assert_eq!(
            super::super::validation::validate(&frame, &details).unwrap()["reason"],
            "accepted"
        );
        helper.finish(false).await.unwrap();
        stopped(pid(&dir));
        assert!(!dir.join("request").exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}

#[tokio::test]
async fn preparing_even_kanji_edits_never_asks_helper_to_select_an_action() {
    let dir = directory();
    let mut helper = child(&dir, &json!({"fixed_payload":"must not be read"}), true);
    let mut frame = FIXTURES["projection_cases"][0]["frame"].clone();
    frame["text"] = "「桜」を「夏」にして".into();
    let (prepared, _) = super::super::prepare_consultation(
        &mut helper,
        &mut crate::workshop_editing::Engine::default(),
        &mut frame,
    )
    .await
    .unwrap();
    assert!(prepared.get("fixed_payload").is_none());
    helper.finish(false).await.unwrap();
    stopped(pid(&dir));
    assert!(!dir.join("request").exists());
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn native_runtime_materials_match_every_canonical_preparation() {
    for case in FIXTURES["projection_cases"].as_array().unwrap() {
        let details = crate::workshop_projection::details_for(&case["frame"]).unwrap();
        assert_eq!(details, case["prepared"]["details"], "{}", case["name"]);
        assert_eq!(
            python_json(&details),
            python_json(&case["prepared"]["details"])
        );
    }
    let fixtures: Value =
        serde_json::from_str(include_str!("../workshop_projection/fixtures.json")).unwrap();
    for case in fixtures["projection"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["op"] == "revision")
    {
        let prepared = super::super::prepare_revision(&case["input"]).unwrap();
        let expected: crate::haiku::revision::Input =
            serde_json::from_value(case["expected"].clone()).unwrap();
        assert_eq!(prepared.lines, expected.lines);
        assert_eq!(prepared.line_sources, expected.line_sources);
        assert_eq!(prepared.findings, expected.findings);
        assert_eq!(prepared.basis, expected.basis);
        assert_eq!(prepared.max_tokens, expected.max_tokens);
        assert_eq!(prepared.grounding_max_tokens, expected.grounding_max_tokens);
    }
}

#[tokio::test]
async fn non_editing_phases_and_restricted_actions_do_not_use_helper() {
    for index in [192, 384, 0] {
        let case = &FIXTURES["projection_cases"][index];
        let mut frame = case["frame"].clone();
        if index == 0 {
            frame["allowed_actions"] = json!(["respond", "ask"]);
        }
        let details = crate::workshop_projection::details_for(&frame).unwrap();
        let expected = prepare(&json!({"details":details}), frame.get("retry")).unwrap();
        let dir = directory();
        let mut helper = child(&dir, &Value::Null, true);
        let (actual, projected) = super::super::prepare_consultation(
            &mut helper,
            &mut crate::workshop_editing::Engine::default(),
            &mut frame,
        )
        .await
        .unwrap();
        assert_eq!(actual, expected);
        assert_eq!(projected, details);
        helper.finish(false).await.unwrap();
        stopped(pid(&dir));
        assert!(!dir.join("request").exists(), "unexpected dictionary IPC");
        std::fs::remove_dir_all(dir).unwrap();
    }
}

#[tokio::test]
async fn native_projection_limits_reject_before_fragment_ipc() {
    for (field, size) in [("text", 1_000_000), ("text", 600_000)] {
        let mut frame = FIXTURES["projection_cases"][0]["frame"].clone();
        frame[field] = json!("a".repeat(size));
        frame["interpreted_text"] = Value::Null;
        let dir = directory();
        let mut helper = child(&dir, &Value::Null, true);
        let result = super::super::prepare_consultation(
            &mut helper,
            &mut crate::workshop_editing::Engine::default(),
            &mut frame,
        )
        .await;
        assert!(result.unwrap_err().to_string().contains("too large"));
        helper.finish(false).await.unwrap();
        stopped(pid(&dir));
        assert!(!dir.join("request").exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
