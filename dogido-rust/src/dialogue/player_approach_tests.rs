//! 見込み: look_playerの一回生成から呼び寄せを選べる。合格: recallだけ既存命令を一件発行し、stay・旧入力では発行しない。確認範囲: BERT結果注入と模擬HTTP/身体ACK。実モデルの選択品質・実移動・音声は別確認。
use super::*;
use crate::request_classifier::{Classification, Mode as ClassifierMode, Prediction};
use std::{sync::Mutex, time::Duration};

fn wire(sequence: i64) -> GameEvent {
    let mut body = crate::body::tests::fixture();
    body["age_ms"] = json!(0);
    body["sample"]["server_tick"] = json!(100 + sequence);
    body["sample"]["snapshot_revision"] = json!(200 + sequence);
    body["sample"]["senses"]["source_server_tick"] = json!(100 + sequence);
    GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"approach-test",
        "observed_at":chrono::Utc::now(),"sequence":sequence,
        "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "player":{"dimension":"minecraft:overworld","health":20},
        "world":{"time_phase":"day","local_light":15,"danger_darkness_score":0},
        "body_observation":body})).unwrap()
}

// 見込み: 画像がなくても移動を選べ、受付だけを到着と扱わない。合格: 一回の要求でrecall一件かstay零件、欠損/不正選択も零件、到着ACK後だけsucceeded。確認範囲: 固定応答と模擬ACK。
#[tokio::test]
async fn look_player_choice_uses_one_reply_and_existing_recall_with_or_without_image_support() {
    for (choice, image_enabled, expected) in [
        (json!({"action":"recall"}), false, "queued"),
        (json!({"action":"recall"}), true, "queued"),
        (json!({"action":"stay"}), false, "stay"),
        (Value::Null, false, "not_selected"),
        (json!({"action":"inspect"}), false, "not_selected"),
    ] {
        run_choice(choice, image_enabled, "none", expected).await;
    }
}

// 見込み: 生成中に入力や実行可能性が変われば古い移動を実行しない。合格: 新入力は返答取消、身体変更/能力消失は移動不可の返答、全て命令零件。確認範囲: 生成応答を待たせた模擬競合。
#[tokio::test]
async fn changed_input_or_execution_context_cannot_move_on_an_old_choice() {
    for (change, expected) in [("input", "cancelled"), ("body", "rejected"), ("capability", "rejected")] {
        run_choice(json!({"action":"recall"}), false, change, expected).await;
    }
}

async fn run_choice(choice: Value, image_enabled: bool, change: &str, expected: &str) {
    const TEXT: &str = "どぎどー！こっちおいでー！";
    let requests = Arc::new(Mutex::new(Vec::<Value>::new()));
    let captured = requests.clone();
    let started = Arc::new(tokio::sync::Notify::new());
    let requested = started.clone();
    let release = Arc::new(tokio::sync::Notify::new());
    let gate = release.clone();
    let response = json!({"action":"speak","speech":"そっち行くで。","body_action":choice}).to_string();
    let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(
        move |axum::Json(payload): axum::Json<Value>| {
            captured.lock().unwrap().push(payload);
            requested.notify_one();
            let gate = gate.clone();
            let response = response.clone();
            async move {
                gate.notified().await;
                axum::Json(json!({"id":"approach-fixture","object":"chat.completion","created":0,"model":"fixture",
                    "choices":[{"index":0,"message":{"role":"assistant","content":response},"finish_reason":"stop"}]}))
            }
        }));
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = super::super::DialogueConfig {
        base_url: format!("http://{}/v1", socket.local_addr().unwrap()),
        model: "fixture".into(), reading_engine: "off".into(), audio_enabled: false,
        ..Default::default()
    };
    config.chat_image.enabled = image_enabled;
    config.chat_image.wait_ms = 100;
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let http = tokio::spawn(async move { axum::serve(socket, app).await.unwrap(); });
    let d = Dialogue::new(config).unwrap();
    d.register("s", "試験", false);
    let fixture = crate::body::tests::fixture();
    let owner = fixture["sample"]["context"]["owner_id"].as_str().unwrap();
    d.bind_body_owner("s", Some(owner));
    d.set_execution_capabilities("s", &[crate::body::requests::CAPABILITY.into(),
        super::super::visual_runtime::CAPABILITY.into(), super::super::visual_runtime::AFTER_LOOK_CAPABILITY.into()]);
    d.observe("s", wire(1), None);
    let turn;
    let command;
    let context;
    {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.input_generation = 7;
        context = s.body_observation.accepted(Instant::now()).unwrap().context.clone();
        let binding = super::super::request_runtime::Binding::capture(s);
        s.request_classification.restore(7, binding, Some(Classification {
            mode: ClassifierMode::Normal, text: TEXT.into(), source: "text".into(), manifest_sha256: "fixture".into(),
            prediction: Prediction { label: Label::LookPlayer, probabilities: vec![0.0;16], token_count: 7, inference_ms: 1.0 }
        }));
        let accepted = d.body_request_input(&mut data, &mut vec![], "s", TEXT, "text").unwrap();
        turn = accepted["turn_id"].as_str().unwrap().to_owned();
        command = commands(data.sessions.get_mut("s").unwrap()).pop().unwrap();
        assert_eq!(command["mode"], "look_player");
    }
    let look_ack = json!({"command_id":command["command_id"],"command_type":"body_request","status":"succeeded",
        "detail_code":"facing_target","executed_at":chrono::Utc::now(),"owner_id":owner,
        "dimension":context.dimension,"input_generation":7,"body_id":context.body_id,"epoch":context.epoch});
    let mut frame = serde_json::to_value(wire(2)).unwrap();
    frame["command_results"] = json!([look_ack.clone(), look_ack.clone()]);
    d.observe("s", GameEvent::parse(frame).unwrap(), None);
    tokio::time::timeout(Duration::from_secs(3), started.notified()).await.unwrap();
    {
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        let system = requests[0]["messages"][0]["content"].as_str().unwrap();
        assert_eq!(system.matches(crate::companion_prompt::look_player_contract()).count(), 1);
        assert!(!system.contains(crate::companion_prompt::choice_contract()));
        assert!(requests[0]["messages"].to_string().contains(TEXT));
    }
    match change {
        "input" => d.data.lock().unwrap().sessions.get_mut("s").unwrap().input_generation += 1,
        "capability" => d.set_execution_capabilities("s", &[]),
        "body" => {
            let mut frame = serde_json::to_value(wire(3)).unwrap();
            frame["body_observation"]["sample"]["context"]["epoch"] = json!(uuid::Uuid::new_v4().to_string());
            d.observe("s", GameEvent::parse(frame).unwrap(), None);
        }
        _ => (),
    }
    release.notify_one();
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let done = d.data.lock().unwrap().rows.iter().any(|r| r["turn_id"] == turn
                && (r["playback_status"] == "cancelled" || !r["text"].as_str().unwrap_or("").is_empty()));
            if done { break; }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }).await.unwrap();
    let movement;
    {
        let mut data = d.data.lock().unwrap();
        let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
        assert_eq!(row["body_request_command_id"], command["command_id"]);
        if expected == "cancelled" {
            assert_eq!(row["playback_status"], "cancelled");
            assert_eq!(row["text"], "");
        } else {
            assert_eq!(row["player_approach_status"], expected);
            assert!(row["player_approach_result"].is_null());
            if expected == "rejected" { assert_eq!(row["text"], "今はそっちへ行けへんわ。"); }
        }
        let s = data.sessions.get_mut("s").unwrap();
        movement = commands(s);
        assert_eq!(movement.len(), usize::from(expected == "queued"));
        if expected == "queued" {
            assert_eq!(movement[0]["mode"], "recall");
            assert_eq!(movement[0]["input_generation"], 7);
            assert_eq!(movement[0]["owner_id"], owner);
            assert_eq!(commands(s)[0]["command_id"], movement[0]["command_id"]);
        }
    }
    if expected == "queued" {
        let ack = json!({"command_id":movement[0]["command_id"],"command_type":"body_request","status":"succeeded",
            "detail_code":"arrived","executed_at":chrono::Utc::now(),"owner_id":owner,"dimension":context.dimension,
            "input_generation":7,"body_id":context.body_id,"epoch":context.epoch});
        let mut frame = serde_json::to_value(wire(4)).unwrap();
        frame["command_results"] = json!([look_ack, ack.clone(), ack]);
        d.observe("s", GameEvent::parse(frame).unwrap(), None);
        let mut data = d.data.lock().unwrap();
        let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
        assert_eq!(row["player_approach_status"], "succeeded");
        assert_eq!(row["player_approach_result"]["detail_code"], "arrived");
        assert!(commands(data.sessions.get_mut("s").unwrap()).is_empty());
    }
    assert_eq!(requests.lock().unwrap().len(), 1);
    d.shutdown().await;
    http.abort();
}

// 見込み: 通常会話の出力に行動が紛れても移動へ昇格しない。合格: look_player受付のない結果から命令も台帳行も増えない。確認範囲: 操作適用入口。分類・HTTP・実機は対象外。
#[tokio::test]
async fn ordinary_reply_cannot_enable_player_approach() {
    let d = Dialogue::new(super::super::DialogueConfig { audio_enabled: false, llm_enabled: false, ..Default::default() }).unwrap();
    d.register("s", "試験", false);
    let input = super::super::bridge::Input::from(json!({"text":"こんにちは"}));
    let mut result = json!({"text":"やあ。","body_action":{"action":"recall"}});
    d.apply_player_approach("s", "unrelated", 0, &input, &mut result, false).unwrap();
    assert!(d.data.lock().unwrap().rows.is_empty());
    assert!(result["player_approach_status"].is_null());
    d.shutdown().await;
}
