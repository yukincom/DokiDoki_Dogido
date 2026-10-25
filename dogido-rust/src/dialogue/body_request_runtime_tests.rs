//! 呼び寄せ命令の受付と返事の一回生成を接続し、到着ACKを待たないことを模擬HTTPで確かめる。
//! 分類精度・実Minecraftの移動・実モデルの返答品質はこの試験の対象ではない。
use super::*;
use crate::request_classifier::{Classification, Mode as ClassifierMode, Prediction};
use std::{sync::Mutex, time::Duration};

#[tokio::test]
async fn recall_reply_starts_before_arrival_and_uses_the_existing_reply_prompt() {
    const OWNER: &str = "10000000-0000-4000-8000-000000000001";
    const TEXT: &str = "どぎどーこっちおいで〜";
    let requests = Arc::new(Mutex::new(Vec::<Value>::new()));
    let captured = requests.clone();
    let requested = Arc::new(tokio::sync::Notify::new());
    let started = requested.clone();
    let release = Arc::new(tokio::sync::Notify::new());
    let gate = release.clone();
    let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(
        move |axum::Json(payload): axum::Json<Value>| {
            captured.lock().unwrap().push(payload);
            started.notify_one();
            let gate = gate.clone();
            async move {
                gate.notified().await;
                axum::Json(json!({"id":"recall-fixture","object":"chat.completion","created":0,"model":"fixture",
                    "choices":[{"index":0,"message":{"role":"assistant",
                    "content":"{\"action\":\"speak\",\"speech\":\"はーい、今そっち行くで。\"}"},"finish_reason":"stop"}]}))
            }
        }));
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base_url = format!("http://{}/v1", socket.local_addr().unwrap());
    let http = tokio::spawn(async move {
        axum::serve(socket, app).await.unwrap();
    });
    let mut config = super::super::DialogueConfig {
        base_url,
        model: "fixture".into(),
        reading_engine: "off".into(),
        audio_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let dialogue = Dialogue::new(config).unwrap();
    dialogue.register("s", "試験", false);
    dialogue.bind_body_owner("s", Some(OWNER));
    dialogue.set_execution_capabilities("s", &[crate::body::requests::CAPABILITY.into()]);
    let mut wire = super::super::empty_event("試験");
    wire["observed_at"] = json!(chrono::Utc::now());
    wire["player"]["dimension"] = json!("minecraft:overworld");
    wire["player"]["health"] = json!(20);
    let event = GameEvent::parse(wire.clone()).unwrap();
    let mut jobs = vec![];
    let accepted;
    let command;
    {
        let mut data = dialogue.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.latest = Some(event);
        s.received = Some(Instant::now());
        s.input_generation = 7;
        let binding = super::super::request_runtime::Binding::capture(s);
        s.request_classification.restore(
            7,
            binding,
            Some(Classification {
                mode: ClassifierMode::Normal,
                text: TEXT.into(),
                source: "text".into(),
                manifest_sha256: "fixture".into(),
                prediction: Prediction {
                    label: Label::DogidoRecall,
                    probabilities: vec![0.0; 16],
                    token_count: 12,
                    inference_ms: 1.0,
                },
            }),
        );
        accepted = dialogue
            .body_request_input(&mut data, &mut jobs, "s", TEXT, "text")
            .unwrap();
        assert_eq!(accepted["body_request_status"], "queued");
        let s = data.sessions.get_mut("s").unwrap();
        command = commands(s).pop().unwrap();
        assert_eq!(command["mode"], "recall");
        assert_eq!(s.warning.as_ref().unwrap().actions[0].kind, "body_recall");
        assert!(
            data.rows
                .iter()
                .find(|r| r["turn_id"] == accepted["turn_id"])
                .unwrap()["body_request_result"]
                .is_null()
        );
    }
    tokio::time::timeout(Duration::from_secs(3), requested.notified())
        .await
        .unwrap();
    {
        let sent = requests.lock().unwrap();
        assert_eq!(sent.len(), 1);
        let messages = &sent[0]["messages"];
        assert_eq!(
            messages[0]["content"],
            crate::companion_prompt::dialogue(
                "normal",
                crate::companion_prompt::DialoguePurpose::Reply,
                false
            )
        );
        let context: Value =
            serde_json::from_str(messages[1]["content"].as_str().unwrap()).unwrap();
        assert_eq!(context["event"], "body_recall");
        assert_eq!(context["player_input"], TEXT);
        assert_eq!(context["situation"], "プレイヤーがドギドを呼んでいる。");
        assert!(context.get("body_options").is_none());
        assert!(context.get("execution_result").is_none());
    }
    release.notify_one();
    for job in jobs {
        tokio::time::timeout(Duration::from_secs(3), job)
            .await
            .unwrap()
            .unwrap();
    }
    {
        let mut data = dialogue.data.lock().unwrap();
        assert!(
            data.rows
                .iter()
                .any(|r| r["text"] == "はーい、今そっち行くで。")
        );
        // 返答が出た後でも、到着の正本はまだ未確定。
        assert!(
            data.rows
                .iter()
                .find(|r| r["turn_id"] == accepted["turn_id"])
                .unwrap()["body_request_result"]
                .is_null()
        );
        wire["command_results"] = json!([{"command_id":command["command_id"],"command_type":"body_request",
            "status":"succeeded","detail_code":"arrived","executed_at":chrono::Utc::now(),
            "owner_id":OWNER,"dimension":"minecraft:overworld","input_generation":7,
            "body_id":"20000000-0000-4000-8000-000000000001","epoch":"30000000-0000-4000-8000-000000000001"}]);
        let event = GameEvent::parse(wire).unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        let reports = observe(s, &event);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].value["status"], "succeeded");
        assert_eq!(reports[0].feedback, None);
        assert!(observe(s, &event).is_empty());
    }
    assert_eq!(requests.lock().unwrap().len(), 1);
    dialogue.shutdown().await;
    http.abort();
}
