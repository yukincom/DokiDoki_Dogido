//! 模擬の共有起動・FabricからHTTP受付、画面配送と表示確認を通す。実ゲーム・実音声は別確認。
use super::*;
const PATH: &str = "/api/v1/family-message";
const KEY: &str = "0123456789abcdef0123456789abcdef";
const FAMILY: (&str, &str) = ("x-dogido-family-key", KEY);
const BEARER: (&str, &str) = ("authorization", "Bearer fixture");

fn message() -> Value {
    json!({"id": "00000000000000000000000000000001", "text": "お母さんから、そろそろごはんやで。"})
}

// 共有起動だけが受付を開く。模擬HTTPで未設定・誤鍵・Bearer不足・Origin付きは拒否、
// 両方の鍵を持つクライアントだけが本文の型・一行・長さ・IDの形式検査へ進むことが合格。
#[tokio::test]
async fn family_message_requires_launch_key_no_origin_and_existing_auth() {
    for key in [None, Some("broken"), Some(KEY)] {
        let app = Application::new(ServerConfig {
            auth_token: Some("fixture".into()),
            family_session_key: key.map(str::to_owned),
            ..Default::default()
        });
        let router = app.router();
        let wrong = ("x-dogido-family-key", "incorrect");
        for headers in [
            vec![],
            vec![BEARER],
            vec![FAMILY],
            vec![BEARER, wrong],
            vec![BEARER, FAMILY, ("origin", "http://localhost")],
        ] {
            assert_eq!(
                call(&router, "POST", PATH, Some(message()), &headers)
                    .await
                    .0,
                StatusCode::UNAUTHORIZED
            );
        }
        for (field, value) in [
            ("id", json!(KEY.to_uppercase())),
            ("id", json!("x")),
            ("text", json!("")),
            ("text", json!("一行\n次の行")),
            ("text", json!("あ".repeat(5201))),
        ] {
            let mut invalid = message();
            invalid[field] = value;
            assert_eq!(
                call(&router, "POST", PATH, Some(invalid), &[BEARER, FAMILY])
                    .await
                    .0,
                if key == Some(KEY) {
                    StatusCode::UNPROCESSABLE_ENTITY
                } else {
                    StatusCode::UNAUTHORIZED
                }
            );
        }
        app.shutdown().await.unwrap();
    }
}

async fn connected(router: &Router) -> String {
    let mut request = registration();
    request["capabilities"] = json!(["dialogue_text.v1"]);
    request["voice_enabled"] = false.into();
    let (status, body) = call(
        router,
        "POST",
        "/api/v1/adapter-sessions",
        Some(request),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    body["session_id"].as_str().unwrap().to_owned()
}

async fn observe(router: &Router, sid: &str, sequence: i64, engaged: bool) {
    let event = json!({"schema_version":"2026-05-24","adapter":"dogido-fabric-client","sequence":sequence,
        "observed_at":chrono::Utc::now(),"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
        "combat":{"player_engaged":engaged}, "world":{"time_phase":"day","weather":"clear","local_light":15,"sky_visible":true}});
    assert_eq!(
        call(
            router,
            "POST",
            "/api/v1/game-events",
            Some(event),
            &[("x-dogido-session-id", sid)]
        )
        .await
        .0,
        StatusCode::ACCEPTED
    );
}

// 最新観測を持つ一人が安全な時だけ表示する。交戦中・複数・観測なしの再試行を保ち、
// voice offは表示ACKのみ、voice onは模擬合成・再生が一回だけ成功し、私信が全体表示・履歴に漏れなければ合格。
#[tokio::test]
async fn family_message_waits_for_one_safe_game_and_deduplicates_through_display_ack() {
    let directory =
        std::env::temp_dir().join(format!("dogido-family-test-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&directory).unwrap();
    let (audios, mut queried) = mpsc::unbounded_channel();
    let fake_tts = Router::new()
        .route(
            "/audio_query",
            post(
                move |Query(values): Query<std::collections::HashMap<String, String>>| {
                    audios.send(values["text"].clone()).unwrap();
                    async { Json(json!({})) }
                },
            ),
        )
        .route("/synthesis", post(|| async { b"RIFF0000WAVE".to_vec() }));
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = crate::dialogue::DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        audio_dir: directory.join("audio"),
        voicevox_url: format!("http://{}", socket.local_addr().unwrap()),
        player: "/usr/bin/true".into(),
        ..Default::default()
    };
    let server = tokio::spawn(async move {
        axum::serve(socket, fake_tts).await.unwrap();
    });
    config.haiku.enabled = false;
    config.haiku.memory_enabled = false;
    config.haiku.memory_dir = directory.join("memory");
    config.haiku.screenshot_dir = Some(directory.join("screenshots"));
    config.haiku.capture_dir = Some(directory.join("captures"));
    let dialogue = crate::dialogue::Dialogue::new(config).unwrap();
    let app = Application::new(ServerConfig {
        dialogue: Some(dialogue.clone()),
        family_session_key: Some(KEY.into()),
        ..Default::default()
    });
    let router = app.router();
    let send = || call(&router, "POST", PATH, Some(message()), &[FAMILY]);
    let readiness = || call(&router, "GET", PATH, None, &[FAMILY]);
    let sid = connected(&router).await;
    assert_eq!(readiness().await.1["reason"], "no_active_game");
    assert_eq!(send().await.1["reason"], "no_active_game");
    observe(&router, &sid, 1, true).await;
    assert_eq!(send().await.1["reason"], "game_busy");
    let other = connected(&router).await;
    observe(&router, &other, 1, false).await;
    assert_eq!(send().await.1["reason"], "ambiguous_game");
    call(
        &router,
        "DELETE",
        &format!("/api/v1/adapter-sessions/{sid}"),
        None,
        &[],
    )
    .await;

    assert_eq!(readiness().await.1["ready"], true);
    assert_eq!(
        send().await.1,
        json!({"accepted":true,"duplicate":false,"status":"queued"})
    );
    assert_eq!(
        send().await.1,
        json!({"accepted":true,"duplicate":true,"status":"queued"})
    );
    let rows = &dialogue.snapshot(Some(&other))["utterances"];
    let family = rows
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["source"] == "family_message")
        .collect::<Vec<_>>();
    assert_eq!(family.len(), 1);
    let row = family[0];
    assert_eq!(row["text"], message()["text"]);
    assert_eq!(row["output_mode"], "text");
    assert_eq!(row["combat_actions"], json!([]));
    assert_ne!(row["delivery_confirmed"], true);
    assert!(
        !dialogue
            .snapshot(None)
            .to_string()
            .contains(message()["text"].as_str().unwrap())
    );
    let ack = json!({"session_id":other,"display_ids":[row["display_id"].clone()]});
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/rust-dialogue/displayed",
            Some(ack),
            &[]
        )
        .await
        .1["displayed"],
        json!([row["display_id"]])
    );
    let snapshot = dialogue.snapshot(Some(&other));
    let delivered = snapshot["utterances"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["turn_id"] == row["turn_id"])
        .unwrap();
    assert_eq!(delivered["text_displayed"], true);
    assert_eq!(delivered["delivery_confirmed"], true);
    assert!(
        !snapshot["sessions"]
            .to_string()
            .contains(message()["text"].as_str().unwrap())
    );
    assert_eq!(send().await.1["status"], "delivered");
    assert!(queried.try_recv().is_err());
    assert_eq!(
        dialogue.set_voice_enabled(&other, true)["voice_enabled"],
        true
    );
    let mut voice = message();
    voice["id"] = json!("00000000000000000000000000000002");
    voice["text"] = json!("お母さんが待っとるで。");
    assert_eq!(
        call(&router, "POST", PATH, Some(voice.clone()), &[FAMILY])
            .await
            .1["accepted"],
        true
    );
    assert_eq!(
        call(&router, "POST", PATH, Some(voice.clone()), &[FAMILY])
            .await
            .1["duplicate"],
        true
    );
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(3), queried.recv())
            .await
            .unwrap()
            .unwrap(),
        voice["text"]
    );
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let view = dialogue.snapshot(Some(&other));
            let current = view["utterances"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r["text"] == voice["text"])
                .unwrap();
            if current["playback_status"] == "completed" {
                assert_eq!(current["delivery_confirmed"], true);
                assert_eq!(current["text_displayed"], false);
                assert!(
                    !view["sessions"]
                        .to_string()
                        .contains(voice["text"].as_str().unwrap())
                );
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    assert!(queried.try_recv().is_err());
    call(
        &router,
        "DELETE",
        &format!("/api/v1/adapter-sessions/{other}"),
        None,
        &[],
    )
    .await;
    assert_eq!(
        send().await.1,
        json!({"accepted":true,"duplicate":true,"status":"delivered"})
    );
    app.shutdown().await.unwrap();
    server.abort();
    std::fs::remove_dir_all(directory).unwrap();
}
