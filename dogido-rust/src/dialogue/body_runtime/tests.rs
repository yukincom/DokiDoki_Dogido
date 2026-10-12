//! 生eventの受理・接続分離・モデル材料への反映を実際のDialogue経由で検査する。
use super::*;
use crate::{
    chat_materials::CompletedHistory,
    dialogue::{DialogueConfig, chat_context},
};
use serde_json::{Value, json};
use std::sync::Arc;
const OWNER: &str = "11111111-1111-4111-8111-111111111111";
fn server() -> Arc<Dialogue> {
    let mut c = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    c.haiku.enabled = false;
    c.haiku.memory_enabled = false;
    let d = Dialogue::new(c).unwrap();
    d.register("s", "呼称", true);
    d.bind_body_owner("s", Some(OWNER));
    d.register("other", "他", true);
    d
}
fn event(sequence: i64, tick: u64, z: f64) -> GameEvent {
    let mut body: Value =
        serde_json::from_str(include_str!("../../../fixtures/body-observation-v2.json")).unwrap();
    body["sample"]["server_tick"] = json!(tick);
    body["sample"]["snapshot_revision"] = json!(tick * 2);
    body["sample"]["player_pose"]["position"]["z"] = json!(z);
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"body-test","observed_at":chrono::Utc::now(),"sequence":sequence,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20,"yaw":120},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0},
        "body_observation":body})).unwrap()
}
fn edit(e: GameEvent, change: impl FnOnce(&mut Value)) -> GameEvent {
    let mut v = serde_json::to_value(e).unwrap();
    change(&mut v);
    GameEvent::parse(v).unwrap()
}
fn capture(d: &Dialogue, e: &GameEvent) -> chat_context::Native {
    let data = d.data.lock().unwrap();
    chat_context::capture(
        &data.sessions["s"],
        e,
        &d.config.combat,
        CompletedHistory::default(),
        None,
    )
    .unwrap()
}
fn body(c: &chat_context::Native) -> &Value {
    &c.context.world_context.as_ref().unwrap().observations["body_observation"]
}
#[tokio::test]
async fn body_original_event_updates_once_and_capture_is_immutable() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), Some("a"));
    let first = capture(&d, &a);
    let first_diagnostic = d.snapshot(Some("s"))["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap()["body_observation"]
        .clone();
    assert_eq!(first_diagnostic["current"]["player"]["position"]["z"], 23.0);
    d.observe("other", a.clone(), Some("a"));
    let b = edit(event(2, 110, 28.0), |v| {
        let sample = &mut v["body_observation"]["sample"];
        sample["self_pose"]["yaw"] = json!(90.0);
        sample["self_pose"]["view_yaw"] = json!(90.0);
        sample["player_pose"]["yaw"] = json!(-45.0);
        sample["player_pose"]["pitch"] = json!(-20.0);
        sample["vision"]["player_visibility"] = json!("not_visible");
        sample["vision"]["reason"] = json!("occluded");
    });
    d.observe("s", b.clone(), Some("b"));
    let second = capture(&d, &b);
    let second_diagnostic = d.snapshot(Some("s"))["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap()["body_observation"]
        .clone();
    assert_eq!(
        second_diagnostic["current"]["player"]["position"]["z"],
        28.0
    );
    assert_eq!(first_diagnostic["current"]["player"]["position"]["z"], 23.0);
    assert_eq!(
        first_diagnostic["current"]["vision"]["player_visibility"],
        "visible"
    );
    assert_eq!(second_diagnostic["current"], body(&second)["current"]);
    assert_eq!(second_diagnostic["previous"], body(&second)["previous"]);
    assert_eq!(
        body(&second)["current"]["vision"]["player_visibility"],
        "not_visible"
    );
    assert_eq!(
        body(&second)["previous"]["vision"]["player_visibility"],
        "visible"
    );
    assert_eq!(body(&second)["current"]["player"]["position"]["z"], 28.0);
    assert_eq!(body(&second)["previous"]["player"]["position"]["z"], 23.0);
    assert_eq!(body(&first)["current"]["player"]["position"]["z"], 23.0);
    assert_eq!(body(&second)["current"]["self"]["yaw"], 90.0); // player yawは混ぜない。
    assert_eq!(body(&second)["current"]["self"]["view_yaw"], 90.0);
    assert_eq!(body(&second)["current"]["self"]["view_pitch"], 0.0);
    assert_eq!(body(&second)["current"]["player"]["yaw"], -45.0);
    assert_eq!(body(&second)["current"]["player"]["pitch"], -20.0);
    let dup = event(2, 120, 99.0);
    d.observe("s", dup.clone(), Some("dup"));
    assert_eq!(
        body(&capture(&d, &dup))["current"]["player"]["position"]["z"],
        28.0
    );
    let partial = edit(event(3, 110, 88.0), |v| {
        v.as_object_mut().unwrap().remove("body_observation");
    });
    d.observe("s", partial.clone(), Some("partial"));
    assert_eq!(
        body(&capture(&d, &partial))["current"]["player"]["position"]["z"],
        28.0
    );
    assert_eq!(
        context(&d.data.lock().unwrap().sessions["other"])
            .unwrap()
            .observations["body_observation"]["reason"],
        "owner_mismatch"
    );
    assert_eq!(
        second.context.world_context.as_ref().unwrap().observations["observer"],
        "player"
    );
    let input = crate::chat_materials::PlayerInput {
        raw_text: "オレはさっきより離れた？".into(),
        semantic_text: "オレはさっきより離れた？".into(),
        ..Default::default()
    };
    let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
        &b,
        &second.settings,
        &input,
        &second.context,
        &second.snapshot,
        &chat_context::CatalogLabels,
    )
    .unwrap() else {
        panic!("normal reply route");
    };
    let request = reply.request("fixture-model", 256).unwrap();
    let wire = serde_json::to_value(&request).unwrap().to_string();
    assert_eq!(
        request.messages[1]
            .content
            .matches("\"body_observation\"")
            .count(),
        1
    );
    assert!(
        request.messages[0]
            .content
            .contains("body_observation の self はオレ")
    );
    assert!(wire.contains("server_pose"));
    assert!(wire.contains("pose_basis"));
    assert!(wire.contains("body_eye_raycast_fov"));
    assert!(wire.contains("occluded"));
    assert!(wire.contains("view_pitch"));
    assert!(wire.contains("previous"));
    assert!(!wire.contains(OWNER));
    let mut result = json!({"text":"fixture"});
    attach_input(&mut result, &reply.details);
    assert_eq!(result["body_observation_input"], body(&second).clone());
    let later = event(4, 130, 42.0);
    d.observe("s", later.clone(), Some("later"));
    assert_eq!(
        body(&capture(&d, &later))["current"]["player"]["position"]["z"],
        42.0
    );
    assert_eq!(
        result["body_observation_input"]["current"]["player"]["position"]["z"],
        28.0
    );
    assert_eq!(
        result["body_observation_input"]["previous"]["player"]["position"]["z"],
        23.0
    );
    assert_eq!(
        result["body_observation_input"]["current"]["vision"]["reason"],
        "occluded"
    );
    assert_eq!(
        body(&capture(&d, &later))["current"]["vision"]["reason"],
        "in_view"
    );
    assert!(!result.to_string().contains(OWNER));
    drop(permit);
    d.shutdown().await;
}
#[tokio::test]
async fn body_unavailable_vision_is_kept_in_normal_chat_and_diagnostic_without_losing_pose() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let e = edit(event(1, 100, 23.0), |v| {
        v["body_observation"]["sample"]["vision"]["player_visibility"] = json!("unavailable");
        v["body_observation"]["sample"]["vision"]["reason"] = json!("area_unloaded");
    });
    d.observe("s", e.clone(), Some("unavailable-vision"));
    let captured = capture(&d, &e);
    let projected = body(&captured);
    assert_eq!(projected["status"], "observed");
    assert_eq!(projected["current"]["self"]["position"]["z"], 20.0);
    assert_eq!(projected["current"]["player"]["position"]["z"], 23.0);
    assert_eq!(
        projected["current"]["vision"]["player_visibility"],
        "unavailable"
    );
    let snapshot = d.snapshot(Some("s"));
    let diagnostic = snapshot["sessions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["session_id"] == "s")
        .unwrap();
    assert_eq!(
        diagnostic["body_observation"]["current"],
        projected["current"]
    );
    let input = crate::chat_materials::PlayerInput {
        raw_text: "今オレが見える？".into(),
        semantic_text: "今オレが見える？".into(),
        ..Default::default()
    };
    let crate::chat_materials::Prepared::Reply(reply) = crate::chat_materials::prepare(
        &e,
        &captured.settings,
        &input,
        &captured.context,
        &captured.snapshot,
        &chat_context::CatalogLabels,
    )
    .unwrap() else {
        panic!("normal reply route");
    };
    let request = reply.request("fixture-model", 256).unwrap();
    assert!(request.messages[1].content.contains("area_unloaded"));
    assert!(request.messages[1].content.contains("pose_basis"));
    let mut result = json!({"text":"fixture"});
    attach_input(&mut result, &reply.details);
    assert_eq!(result["body_observation_input"], projected.clone());
    assert!(result["body_observation_input"]["current"]["self"]["position"].is_object());
    drop(permit);
    d.shutdown().await;
}
#[test]
fn body_game_event_boundary_rejects_old_version_or_invalid_vision() {
    for (path, value) in [
        ("/body_observation/version", json!(1)),
        ("/body_observation/sample/self_pose/view_yaw", json!(180)),
        ("/body_observation/sample/player_pose/pitch", json!(91)),
        (
            "/body_observation/sample/vision/player_visibility",
            json!("unavailable"),
        ),
        ("/body_observation/sample/vision/reason", json!("occluded")),
        ("/body_observation/sample/vision/range_blocks", json!(0)),
    ] {
        let mut v = serde_json::to_value(event(1, 100, 23.0)).unwrap();
        *v.pointer_mut(path).unwrap() = value;
        assert!(GameEvent::parse(v).is_err(), "{path}");
    }
}
#[tokio::test]
async fn body_expired_transport_and_explicit_absence_cannot_be_revived_by_partial_events() {
    let d = server();
    let permit = d.serial.acquire().await.unwrap();
    let a = event(1, 100, 23.0);
    d.observe("s", a.clone(), Some("a"));
    let stale = edit(event(2, 110, 28.0), |v| {
        v["observed_at"] = json!(chrono::Utc::now() - chrono::Duration::seconds(4))
    });
    d.observe("s", stale.clone(), Some("stale"));
    assert_eq!(body(&capture(&d, &stale))["reason"], "stale");
    let partial = edit(event(3, 120, 90.0), |v| {
        v.as_object_mut().unwrap().remove("body_observation");
    });
    d.observe("s", partial.clone(), Some("partial"));
    assert_eq!(body(&capture(&d, &partial))["status"], "unavailable");
    let b = event(4, 130, 29.0);
    d.observe("s", b.clone(), Some("b"));
    assert!(body(&capture(&d, &b))["previous"].is_null());
    let absent =
        edit(
            event(5, 140, 99.0),
            |v| {
                v["body_observation"] =
                    json!({"version":2,"status":"unavailable","reason":"unloaded"})
            },
        );
    d.observe("s", absent.clone(), Some("absent"));
    assert!(body(&capture(&d, &absent)).get("current").is_none());
    drop(permit);
    d.shutdown().await;
}
