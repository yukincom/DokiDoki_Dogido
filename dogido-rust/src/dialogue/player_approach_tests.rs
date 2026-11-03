//! 見込み: 雑談・look_playerの一回生成から呼び寄せを選べる。合格: recallだけ既存命令を一件発行し、stay・旧入力では発行しない。確認範囲: BERT結果注入と模擬HTTP/身体ACK。実モデルの選択品質・実移動・音声は別確認。
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
        run_choice(choice, image_enabled, "none", expected, false).await;
    }
}

// 見込み: 生成中に入力や実行可能性が変われば古い移動を実行しない。合格: 新入力は返答取消、身体変更/能力消失は移動不可の返答、全て命令零件。確認範囲: 生成応答を待たせた模擬競合。
#[tokio::test]
async fn changed_input_or_execution_context_cannot_move_on_an_old_choice() {
    for (change, expected) in [("input", "cancelled"), ("body", "rejected"), ("capability", "rejected")] {
        run_choice(json!({"action":"recall"}), false, change, expected, false).await;
    }
}

// 見込み: 雑談の一回生成でも、台詞から行動を推測せず選択だけを実行できる。
// 合格: 同じ台詞でrecall一件・stay零件・能力なしは契約も命令も零件。表示ACKと実行結果も別に確定する。範囲: 実submitと模擬HTTP/身体ACK。
#[tokio::test]
async fn casual_input_uses_only_the_offered_choice_in_the_same_generation() {
    for (choice, change, expected) in [("recall", "none", "queued"), ("stay", "none", "stay"),
        ("recall", "no_capability", "not_offered"), ("recall", "capability", "rejected"),
        ("recall", "input", "cancelled"), ("recall", "combat_after_issue", "queued"),
        ("recall", "display", "queued"), ("recall", "failed_ack", "queued")] {
        run_choice(json!({"action":choice}), false, change, expected, true).await;
    }
}

// 見込み: 読み上げ完了より先に動ける。合格: 文ストリームのTTS応答を保留したままrecallが取れ、到着は後続ACKで確定。
// 確認範囲: 一回の模擬SSE生成、模擬音声合成と無音player、command取得。実音声とMinecraft内の時刻は別確認。
#[tokio::test]
async fn casual_recall_is_available_before_audio_delivery_completes() {
    run_choice(json!({"action":"recall"}), false, "streaming", "queued", true).await;
}

// 見込み: 完結した行動だけを受け取った時点で接近を開始し、残りの本文は生成を続ける。
// 合格: 台詞の差分を保留中、雑談と明示注視の両方・音声あり/なしで、ゲーム観測の応答に同じrecallを一件取得。確認範囲: 模擬SSE/ゲーム通信。実移動は別確認。
#[tokio::test]
async fn recall_reaches_the_game_while_the_sentence_is_still_generating() {
    for ordinary in [true, false] {
        for change in ["early_text", "early_audio"] {
            run_choice(json!({"action":"recall"}), false, change, "queued", ordinary).await;
        }
    }
}

async fn run_choice(choice: Value, image_enabled: bool, change: &str, expected: &str, ordinary: bool) {
    let text = if ordinary { "このへんで少し話そうか" } else { "どぎどー！こっちおいでー！" };
    let requests = Arc::new(Mutex::new(Vec::<Value>::new()));
    let captured = requests.clone();
    let started = Arc::new(tokio::sync::Notify::new());
    let requested = started.clone();
    let release = Arc::new(tokio::sync::Notify::new());
    let gate = release.clone();
    let early = matches!(change, "early_text" | "early_audio");
    let allow_body = Arc::new(tokio::sync::Notify::new());
    let body_gate = allow_body.clone();
    let response = json!({"action":"speak","speech":"そっち行くで。","body_action":choice}).to_string();
    let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(
        move |axum::Json(payload): axum::Json<Value>| {
            let streaming = payload["stream"] == true;
            captured.lock().unwrap().push(payload);
            requested.notify_one();
            let gate = gate.clone();
            let body_gate = body_gate.clone();
            let response = response.clone();
            async move {
                gate.notified().await;
                use axum::response::IntoResponse;
                if early && streaming {
                    use futures_util::{stream, StreamExt};
                    use std::convert::Infallible;
                    let chunk = |content: &str| {
                        let value = json!({"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture",
                            "choices":[{"index":0,"delta":{"content":content},"finish_reason":null}]});
                        format!("data: {value}\n\n")
                    };
                    // 最上位objectは未完のまま、body_actionの閉じ括弧だけを送り終える。
                    let choice = chunk(r#"{"body_action":{"action":"recall"}"#);
                    let first = stream::once(async move { Ok::<_,Infallible>(choice) });
                    let rest = stream::once(async move {
                        body_gate.notified().await;
                        let speech = chunk(r#","action":"speak","speech":"そっち行くで。"}"#);
                        let end = json!({"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture",
                            "choices":[{"index":0,"delta":{},"finish_reason":"stop"}]});
                        Ok::<_,Infallible>(format!("{speech}data: {end}\n\ndata: [DONE]\n\n"))
                    });
                    return ([("content-type","text/event-stream")],
                        axum::body::Body::from_stream(first.chain(rest))).into_response();
                }
                if streaming {
                    let chunk = json!({"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture",
                        "choices":[{"index":0,"delta":{"role":"assistant","content":response},"finish_reason":null}]});
                    let end = json!({"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture",
                        "choices":[{"index":0,"delta":{},"finish_reason":"stop"}]});
                    return ([("content-type","text/event-stream")],
                        format!("data: {chunk}\n\ndata: {end}\n\ndata: [DONE]\n\n")).into_response();
                }
                axum::Json(json!({"id":"approach-fixture","object":"chat.completion","created":0,"model":"fixture",
                    "choices":[{"index":0,"message":{"role":"assistant","content":response},"finish_reason":"stop"}]})).into_response()
            }
        }));
    let synth = Arc::new(tokio::sync::Notify::new());
    let query_started = synth.clone();
    let allow_audio = Arc::new(tokio::sync::Notify::new());
    let audio_gate = allow_audio.clone();
    let app = app.route("/audio_query", axum::routing::post(move || {
        let query_started = query_started.clone();
        let gate = audio_gate.clone();
        async move {
            query_started.notify_one();
            gate.notified().await;
            axum::Json(json!({}))
        }
    })).route("/synthesis", axum::routing::post(|| async { b"RIFF0000WAVE".to_vec() }));
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = super::super::DialogueConfig {
        base_url: format!("http://{}/v1", socket.local_addr().unwrap()),
        model: "fixture".into(), reading_engine: "off".into(), audio_enabled: matches!(change, "streaming" | "early_audio"),
        voicevox_url: format!("http://{}", socket.local_addr().unwrap()),
        player: "/usr/bin/true".into(),
        ..Default::default()
    };
    config.chat_image.enabled = image_enabled;
    config.chat_image.wait_ms = 100;
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    let http = tokio::spawn(async move { axum::serve(socket, app).await.unwrap(); });
    let d = Dialogue::new(config).unwrap();
    d.register("s", "試験", false);
    if change == "display" { d.enable_text_delivery("s", Some(false)); }
    let fixture = crate::body::tests::fixture();
    let owner = fixture["sample"]["context"]["owner_id"].as_str().unwrap();
    d.bind_body_owner("s", Some(owner));
    d.set_execution_capabilities("s", &[crate::body::requests::CAPABILITY.into(),
        super::super::visual_runtime::CAPABILITY.into(), super::super::visual_runtime::AFTER_LOOK_CAPABILITY.into()]);
    d.observe("s", wire(1), None);
    if change == "no_capability" { d.set_execution_capabilities("s", &[]); }
    let context = d.data.lock().unwrap().sessions["s"].body_observation.accepted(Instant::now()).unwrap().context.clone();
    let (turn, command) = if ordinary {
        d.data.lock().unwrap().sessions.get_mut("s").unwrap().input_generation = 6;
        let accepted = d.submit(Some("s"), text, "text");
        assert_eq!(accepted["accepted"], true, "{accepted}");
        (accepted["turn_id"].as_str().unwrap().to_owned(), Value::Null)
    } else {
        let mut data = d.data.lock().unwrap();
        let s = data.sessions.get_mut("s").unwrap();
        s.input_generation = 7;
        let binding = super::super::request_runtime::Binding::capture(s);
        s.request_classification.restore(7, binding, Some(Classification {
            mode: ClassifierMode::Normal, text: text.into(), source: "text".into(), manifest_sha256: "fixture".into(),
            prediction: Prediction { label: Label::LookPlayer, probabilities: vec![0.0;16], token_count: 7, inference_ms: 1.0 }
        }));
        let accepted = d.body_request_input(&mut data, &mut vec![], "s", text, "text").unwrap();
        let command = commands(data.sessions.get_mut("s").unwrap()).pop().unwrap();
        assert_eq!(command["mode"], "look_player");
        (accepted["turn_id"].as_str().unwrap().to_owned(), command)
    };
    let look_ack = json!({"command_id":command["command_id"],"command_type":"body_request","status":"succeeded",
        "detail_code":"facing_target","executed_at":chrono::Utc::now(),"owner_id":owner,
        "dimension":context.dimension,"input_generation":7,"body_id":context.body_id,"epoch":context.epoch});
    if !ordinary {
        let mut frame = serde_json::to_value(wire(2)).unwrap();
        frame["command_results"] = json!([look_ack.clone(), look_ack.clone()]);
        d.observe("s", GameEvent::parse(frame).unwrap(), None);
    }
    tokio::time::timeout(Duration::from_secs(3), started.notified()).await.unwrap();
    {
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        if early { assert_eq!(requests[0]["stream"], true); }
        let system = requests[0]["messages"][0]["content"].as_str().unwrap();
        assert_eq!(system.matches(crate::companion_prompt::player_approach_contract()).count(), usize::from(expected != "not_offered"));
        assert_eq!(system.contains(crate::companion_prompt::choice_contract()), expected == "not_offered");
        assert!(requests[0]["messages"].to_string().contains(text));
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
    if early {
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let ready = d.data.lock().unwrap().rows.iter().any(|r|
                    r["turn_id"] == turn && r["player_approach_status"] == "queued");
                if ready { break; }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        }).await.expect("the model is still waiting to send the speech");
        let commands = d.observe("s", wire(3), None)["commands"].as_array().unwrap().clone();
        let recalls: Vec<_> = commands.iter().filter(|c| c["type"] == "body_request" && c["mode"] == "recall").collect();
        assert_eq!(recalls.len(), 1);
        let data = d.data.lock().unwrap();
        let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
        assert_eq!(recalls[0]["command_id"], row["player_approach_command_id"]);
        assert_eq!(recalls[0]["input_generation"], 7);
        assert_eq!(row["text"], "");
        assert!(row["player_approach_result"].is_null());
        drop(data);
        allow_body.notify_one();
        allow_audio.notify_one();
    }
    if change == "streaming" {
        tokio::time::timeout(Duration::from_secs(3), synth.notified()).await.unwrap();
        let mut data = d.data.lock().unwrap();
        let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
        assert_eq!(row["player_approach_status"], "queued");
        assert!(row["player_approach_result"].is_null());
        assert_ne!(row["playback_status"], "completed");
        assert_eq!(commands(data.sessions.get_mut("s").unwrap())[0]["mode"], "recall");
        allow_audio.notify_one();
    }
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
        } else if expected == "not_offered" {
            assert!(row["player_approach_status"].is_null());
            assert_eq!(row["text"], "そっち行くで。");
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
    if change == "display" {
        let display = d.data.lock().unwrap().rows.iter().find(|r| r["turn_id"] == turn).unwrap()["display_id"]
            .as_str().unwrap().to_owned();
        d.acknowledge_display("s", &[display]);
        let data = d.data.lock().unwrap();
        let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
        assert_eq!(row["text_displayed"], true);
        assert_eq!(row["player_approach_status"], "queued");
        assert!(row["player_approach_result"].is_null());
    }
    if expected == "queued" {
        let mut ack = json!({"command_id":movement[0]["command_id"],"command_type":"body_request","status":"succeeded",
            "detail_code":"arrived","executed_at":chrono::Utc::now(),"owner_id":owner,"dimension":context.dimension,
            "input_generation":7,"body_id":context.body_id,"epoch":context.epoch});
        let mut frame = serde_json::to_value(wire(4)).unwrap();
        if change == "failed_ack" {
            ack["status"] = json!("failed");
            ack["detail_code"] = json!("no_safe_destination");
        }
        let mut results = vec![ack.clone(), ack];
        if !ordinary { results.push(look_ack); }
        frame["command_results"] = json!(results);
        if change == "combat_after_issue" {
            // 警戒へ変わった同一観測では、既に届いていたarrivedも採用しない。
            let mut data = d.data.lock().unwrap();
            let s = data.sessions.get_mut("s").unwrap();
            s.foreground.start_combat();
            let reports = observe(s, &GameEvent::parse(frame).unwrap());
            assert_eq!(reports.len(), 1);
            assert_eq!(reports[0].value["status"], "cancelled");
            assert_eq!(s.input_generation, 8);
            assert!(commands(s).is_empty());
        } else {
            d.observe("s", GameEvent::parse(frame).unwrap(), None);
            let mut data = d.data.lock().unwrap();
            let row = data.rows.iter().find(|r| r["turn_id"] == turn).unwrap();
            let (status, detail) = if change == "failed_ack" { ("failed", "no_safe_destination") }
                else { ("succeeded", "arrived") };
            assert_eq!(row["player_approach_status"], status);
            assert_eq!(row["player_approach_result"]["detail_code"], detail);
            assert!(commands(data.sessions.get_mut("s").unwrap()).is_empty());
        }
    }
    assert_eq!(requests.lock().unwrap().len(), 1);
    d.shutdown().await;
    http.abort();
}
