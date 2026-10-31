//! 次の環境判断へ渡るbody_optionsを、実Session・実attach経路で検査する。
//! current_bodyのmode/activity/reasonは今回の最小表示契約で、選択履歴から推定して作らない。
use super::*;
use crate::dialogue::{Dialogue, DialogueConfig};
use crate::events::GameEvent;
use std::sync::Arc;

fn server() -> (Arc<Dialogue>, GameEvent) {
    let mut config = DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let dialogue = Dialogue::new(config).unwrap();
    dialogue.register("s", "test", false);
    let mut body = crate::body::tests::fixture();
    body["age_ms"] = json!(0);
    body["sample"]["player_pose"]["position"] = json!({"x":0,"y":64,"z":0});
    let sample = &body["sample"];
    let owner = sample["context"]["owner_id"].as_str().unwrap();
    dialogue.bind_body_owner("s", Some(owner));
    dialogue.set_execution_capabilities(
        "s",
        &[
            "body.inspect_target.v3".into(),
            crate::body::requests::CAPABILITY.into(),
        ],
    );
    let event = GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"acceptance","observed_at":chrono::Utc::now(),"sequence":1,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"biome":"plains","time_phase":"day","sky_visible":true,"local_light":15,"danger_darkness_score":0},
        "body_observation":body,
        "body_autonomy":{"version":2,"status":"observed","context":sample["context"],"server_tick":sample["server_tick"],
            "snapshot_revision":sample["snapshot_revision"],"age_ms":0,"enabled":true,"mode":"follow","follow_distance":16.0,"warp_distance":24.0,
            "activity":"holding","reason":"follow_wait","attention":{"scope":"ambient","expansion":1.0},
            "reassurance":{"value":50.0,"owner_distance":2.0,"owner_visible":true,"face_visible":true,"owner_working":false},"candidates":[]}})).unwrap();
    dialogue.observe("s", event.clone(), None);
    (dialogue, event)
}

// 顔へ近づく命令の実到着後に身体観測を更新する。次の環境入力へ選択・成功結果・最新の身体状態を一緒に渡す。
#[test]
fn acceptance_body_results_current_body_state_accompanies_next_model_options() {
    let (dialogue, event) = server();
    let mut data = dialogue.data.lock().unwrap();
    let session = data.sessions.get_mut("s").unwrap();
    let now = Instant::now();
    let at = now - std::time::Duration::from_secs(4);
    let utc = chrono::Utc::now() - chrono::Duration::seconds(4);
    let mut sample = session.body_observation.accepted(now).unwrap().clone();
    let mut observed = event.body_autonomy.clone().unwrap();
    session.body_autonomy = Default::default();
    session
        .body_autonomy
        .observe(Some(&observed), Some(&sample), at, Some(0));
    let prepared = session.body_autonomy.prepare(at).unwrap();
    let command = session
        .body_autonomy
        .choose(
            &prepared,
            r#"{"action":"owner_face","target_id":null}"#,
            at,
            utc,
        )
        .unwrap()
        .unwrap();
    let result = serde_json::from_value(
        json!({"command_id":command["command_id"],"command_type":"inspect_body_target",
        "status":"succeeded","detail_code":"arrived","executed_at":utc+chrono::Duration::seconds(1),
        "body_id":sample.context.body_id,"epoch":sample.context.epoch}),
    )
    .unwrap();
    assert_eq!(
        session
            .body_autonomy
            .observe_results(
                &[result],
                at + std::time::Duration::from_secs(1),
                utc + chrono::Duration::seconds(1)
            )
            .len(),
        1
    );
    sample.server_tick += 1;
    sample.snapshot_revision += 1;
    let crate::body::autonomy::Observation::Observed(current) = &mut observed else {
        unreachable!()
    };
    current.server_tick = sample.server_tick;
    current.snapshot_revision = sample.snapshot_revision;
    current.reason = "within_follow_distance".into();
    session
        .body_autonomy
        .observe(Some(&observed), Some(&sample), now, Some(0));
    let mut action = crate::environment::notice::action();
    action.delivery = Delivery::Ambient;
    crate::environment::reaction::attach(&mut action, &event, &dialogue.config.combat);
    attach(session, "acceptance-turn", &mut action, 0);
    let prepared = crate::environment::reaction::Prepared::new(
        &json!({"kind":"environment_reaction","model":"acceptance-no-call",
        "max_tokens":256,"fallback_text":"","details":action.leaf.as_ref().unwrap().details}),
        "acceptance-no-call",
    )
    .unwrap();
    let model_context: Value = serde_json::from_str(&prepared.request.messages[1].content).unwrap();
    let options = &model_context["body_options"];
    assert!(
        options["available_actions"]["stay"].is_string(),
        "既存の実際の候補準備経路まで到達する"
    );
    assert_eq!(
        options["current_body"]["mode"], "follow",
        "現在の制御modeを選択と別に渡す"
    );
    assert_eq!(
        options["current_body"]["activity"], "holding",
        "到着したという推測ではなく観測済みの活動を渡す"
    );
    assert_eq!(
        options["current_body"]["reason"], "within_follow_distance",
        "選択開始時のfollow_waitへ戻さない"
    );
    assert_eq!(options["recent_choices"][0]["action"], "owner_face");
    assert_eq!(
        options["recent_choices"][0]["execution"]["status"],
        "succeeded"
    );
    assert_eq!(
        options["recent_choices"][0]["execution"]["detail_code"],
        "arrived"
    );
}

async fn ordinary_operation(expected: &str) {
    use crate::chat_materials::{self, PlayerInput};
    use crate::request_classifier::{Classification, Label, Mode, Prediction};
    let (dialogue, event) = server();
    let permit = dialogue.serial.acquire().await.unwrap();
    let text = "ドギド、こっち来て";
    let mut jobs = Vec::new();
    let command = {
        let mut data = dialogue.data.lock().unwrap();
        let session = data.sessions.get_mut("s").unwrap();
        session.input_generation = 7;
        // 分類精度ではなく、分類結果を受けた実操作受付からの情報伝達を検査する。
        let binding = super::super::request_runtime::Binding::capture(session);
        session.request_classification.restore(
            7,
            binding,
            Some(Classification {
                mode: Mode::Normal,
                text: text.into(),
                source: "text".into(),
                manifest_sha256: "acceptance-fixture".into(),
                prediction: Prediction {
                    label: Label::DogidoRecall,
                    probabilities: vec![0.0; 16],
                    token_count: 10,
                    inference_ms: 0.0,
                },
            }),
        );
        let accepted = dialogue
            .body_request_input(&mut data, &mut jobs, "s", text, "text")
            .unwrap();
        assert_eq!(accepted["body_request_status"], "queued");
        super::super::body_request_runtime::commands(data.sessions.get_mut("s").unwrap())
            .pop()
            .unwrap()
    };
    if expected != "queued" {
        let mut wire = serde_json::to_value(&event).unwrap();
        wire["sequence"] = json!(2);
        wire["observed_at"] = json!(chrono::Utc::now());
        if expected == "cancelled" {
            dialogue
                .data
                .lock()
                .unwrap()
                .sessions
                .get_mut("s")
                .unwrap()
                .input_generation += 1;
        } else {
            let context = &command["context"];
            wire["command_results"] = json!([{"command_id":command["command_id"],"command_type":"body_request",
                "status":expected,"detail_code":if expected == "succeeded" {"arrived"} else {"path_unavailable"},
                "executed_at":chrono::Utc::now(),"input_generation":7,"owner_id":command["owner_id"],
                "dimension":command["dimension"],"body_id":context["body_id"],"epoch":context["epoch"]}]);
        }
        dialogue.observe("s", GameEvent::parse(wire).unwrap(), None);
    }
    let (operation, request) = {
        let data = dialogue.data.lock().unwrap();
        let session = &data.sessions["s"];
        // 実際の受付結果も確認し、試験用にモデル向け状態を挿入しない。
        let row = data
            .rows
            .iter()
            .find(|row| row["body_request_command_id"] == command["command_id"])
            .unwrap();
        assert_eq!(
            row["body_request_status"], expected,
            "本体の受付・結果反映まで到達する"
        );
        let event = session.latest.as_ref().unwrap();
        let native = super::super::chat_context::capture(
            session,
            event,
            &dialogue.config.combat,
            Default::default(),
            None,
        )
        .unwrap();
        let input = PlayerInput {
            raw_text: "今どうなってる？".into(),
            semantic_text: "今どうなってる？".into(),
            ..Default::default()
        };
        let chat_materials::Prepared::Reply(reply) = chat_materials::prepare(
            native.sensory_event.as_ref().unwrap(),
            &native.settings,
            &input,
            &native.context,
            &native.snapshot,
            &super::super::chat_context::CatalogLabels,
        )
        .unwrap() else {
            panic!("通常モデル要求へ到達する")
        };
        (
            native.context.world_context.unwrap().observations["body_operation"].clone(),
            reply.request("acceptance-no-call", 256).unwrap(),
        )
    };
    for job in jobs {
        job.abort();
    }
    drop(permit);
    dialogue.shutdown().await;
    assert_eq!(
        operation["status"], expected,
        "通常返答へ今回の操作の受付・実行状態を渡す"
    );
    assert_eq!(operation["mode"], "recall");
    let material = request
        .messages
        .iter()
        .filter(|m| m.role == crate::types::Role::User)
        .map(|m| m.content.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        material.contains("body_operation") && material.contains(expected),
        "最終user材料からも操作状態が欠落しない"
    );
}

// 呼び寄せを受け付け、まだ到着していない。通常返答の材料はqueuedを含み、実行成功と区別できる。
#[tokio::test]
async fn acceptance_body_results_ordinary_reply_receives_queued_operation() {
    ordinary_operation("queued").await;
}

// 呼び寄せの正しい到着結果を受け取る。実行成功が通常返答の最終材料へ引き継がれる。
#[tokio::test]
async fn acceptance_body_results_ordinary_reply_receives_successful_operation() {
    ordinary_operation("succeeded").await;
}

// 呼び寄せの実行が失敗する。失敗状態が通常返答の最終材料へ引き継がれる。
#[tokio::test]
async fn acceptance_body_results_ordinary_reply_receives_failed_operation() {
    ordinary_operation("failed").await;
}

// 新入力で未完了の呼び寄せが取り消される。次の通常返答に取消状態が届き、移動中と取り違えない。
#[tokio::test]
async fn acceptance_body_results_ordinary_reply_receives_cancelled_operation() {
    ordinary_operation("cancelled").await;
}
