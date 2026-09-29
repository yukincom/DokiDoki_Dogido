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
        "cd '{}'\nprintf '%s' \"$$\" > pid\nread -r line\nprintf '%s\\n' \"$line\" > request\n{reply}\nwhile read -r line; do\nprintf '%s\\n' \"$line\" >> request\nprintf '{{\"step\":null,\"reason\":\"mock\"}}\\n'\ndone\n",
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
async fn runtime_requests_details_once_and_reuses_helper_after_native_validation() {
    for index in [0, 2, 579] {
        let case = &FIXTURES["projection_cases"][index];
        let dir = directory();
        let mut helper = child(&dir, &case["prepared"], false);
        let mut frame = case["frame"].clone();
        let (prepared, details) = super::super::prepare_consultation(&mut helper, &mut frame)
            .await
            .unwrap();
        assert_eq!(prepared, case["expected"]);
        assert_eq!(details, case["prepared"]["details"]);
        let request: Value =
            serde_json::from_str(&std::fs::read_to_string(dir.join("request")).unwrap()).unwrap();
        assert_eq!(request["op"], "prepare_details");
        frame["op"] = json!("prepare_details");
        assert_eq!(frame, request);
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
async fn incompatible_helper_errors_and_cancelled_projection_are_reaped() {
    for mode in ["legacy_messages", "bad_details", "timeout", "cancel"] {
        let case = &FIXTURES["projection_cases"][0];
        let dir = directory();
        let response = if mode == "legacy_messages" {
            case["expected"].clone()
        } else {
            json!({"details":null})
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
