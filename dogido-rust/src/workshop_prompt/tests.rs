use super::*;
use crate::haiku_bridge::Helper;
use std::{path::Path, time::Duration};

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
async fn runtime_requests_only_fragment_once_and_reuses_helper_after_native_validation() {
    for index in [0, 2, 579] {
        let case = &FIXTURES["projection_cases"][index];
        let dir = directory();
        let mut helper = child(
            &dir,
            &json!({"fixed_payload":case["prepared"]["fixed_payload"]}),
            false,
        );
        let mut frame = case["frame"].clone();
        let (prepared, details) = super::super::prepare_consultation(&mut helper, &mut frame)
            .await
            .unwrap();
        assert_eq!(prepared, case["expected"]);
        assert_eq!(details, case["prepared"]["details"]);
        let request: Value =
            serde_json::from_str(&std::fs::read_to_string(dir.join("request")).unwrap()).unwrap();
        assert_eq!(request["op"], "fragment_candidate");
        assert_eq!(frame, case["frame"], "native prepare mutated input");
        let mut expected_request = frame.clone();
        expected_request["op"] = json!("fragment_candidate");
        expected_request["allowed_actions"] = details["allowed_actions"].clone();
        assert_eq!(expected_request, request);
        assert!(!request.as_object().unwrap().contains_key("messages"));
        frame["op"] = json!("validate");
        frame["payload"] = json!({"action":"ask","purpose":"continue_discussion","confidence":0.9,
            "evidence":frame["text"],"speech":"気になるところを教えてな。","checks":[]});
        let validated = super::super::validation::validate(&frame, &details).unwrap();
        assert_eq!(validated["reason"], "accepted");
        assert_eq!(
            std::fs::read_to_string(dir.join("request"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        assert_eq!(
            helper
                .exchange(json!({"op":"reading_overlay","rows":[]}))
                .await
                .unwrap()["reason"],
            "mock"
        );
        let child_pid = pid(&dir);
        helper.finish(false).await.unwrap();
        stopped(child_pid);
        assert_eq!(
            std::fs::read_to_string(dir.join("request"))
                .unwrap()
                .lines()
                .count(),
            2
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}

#[tokio::test]
async fn incompatible_fragment_helper_errors_and_cancelled_extraction_are_reaped() {
    for mode in [
        "legacy_messages",
        "legacy_details",
        "bad_candidate",
        "timeout",
        "cancel",
    ] {
        let case = &FIXTURES["projection_cases"][0];
        let dir = directory();
        let response = if mode == "legacy_messages" {
            case["expected"].clone()
        } else if mode == "legacy_details" {
            case["prepared"].clone()
        } else {
            json!({"fixed_payload":"invalid"})
        };
        let blocked = matches!(mode, "timeout" | "cancel");
        let mut helper = child(&dir, &response, blocked);
        let mut frame = case["frame"].clone();
        let result = if mode == "cancel" {
            let (send, mut cancel) = tokio::sync::watch::channel(false);
            let trigger = async {
                tokio::time::sleep(Duration::from_millis(80)).await;
                send.send(true).unwrap();
            };
            let render = async {
                tokio::select! {
                    _ = crate::dialogue::bridge::cancelled(&mut cancel) => Err(anyhow::anyhow!("cancelled")),
                    result = super::super::prepare_consultation(&mut helper, &mut frame) => result,
                }
            };
            tokio::join!(render, trigger).0
        } else {
            tokio::time::timeout(
                Duration::from_millis(120),
                super::super::prepare_consultation(&mut helper, &mut frame),
            )
            .await
            .unwrap_or_else(|_| Err(anyhow::anyhow!("test turn deadline")))
        };
        assert!(result.is_err(), "{mode}");
        let child_pid = pid(&dir);
        helper.finish(true).await.unwrap();
        stopped(child_pid);
        assert_eq!(
            std::fs::read_to_string(dir.join("request"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
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
        let (actual, projected) = super::super::prepare_consultation(&mut helper, &mut frame)
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
        let result = super::super::prepare_consultation(&mut helper, &mut frame).await;
        assert!(result.unwrap_err().to_string().contains("too large"));
        helper.finish(false).await.unwrap();
        stopped(pid(&dir));
        assert!(!dir.join("request").exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
