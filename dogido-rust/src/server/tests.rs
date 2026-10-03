use super::*;
use axum::{
    body::{Body, to_bytes},
    http::Request,
};
use std::time::Duration;
use tower::ServiceExt;

fn event() -> Value {
    json!({"schema_version":"2026-05-24","adapter":"fabric","sequence":1,
        "observed_at":"2026-09-21T00:00:00Z","event":{"name":"status_snapshot",
        "source_kind":"system","priority_hint":"background","certainty":"high"}})
}

fn registration() -> Value {
    json!({"adapter_name":"dogido-fabric-client", "adapter_version":"test", "game":"minecraft-java",
        "schema_version":"2026-05-24", "player_name":"試験用", "adapter_build":"future-build",
        "capabilities":["workshop_display.v1"], "execution_capabilities":["client.hotbar.select.v1"]})
}

async fn call(
    router: &Router,
    method: &str,
    path: &str,
    body: Option<Value>,
    headers: &[(&str, &str)],
) -> (StatusCode, Value) {
    let mut request = Request::builder().method(method).uri(path);
    for (key, value) in headers {
        request = request.header(*key, *value);
    }
    let bytes = body.map(|body| body.to_string()).unwrap_or_default();
    let request = request
        .header("content-type", "application/json")
        .body(Body::from(bytes))
        .unwrap();
    let response = router.clone().oneshot(request).await.unwrap();
    let status = response.status();
    assert_eq!(response.headers()["cache-control"], "no-store");
    let body = to_bytes(response.into_body(), 1_000_000).await.unwrap();
    (status, serde_json::from_slice(&body).unwrap())
}

async fn create(router: &Router) -> String {
    let (status, body) = call(
        router,
        "POST",
        "/api/v1/adapter-sessions",
        Some(registration()),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body["heartbeat_interval_ms"], 5000);
    assert_eq!(body["max_batch_size"], 25);
    body["session_id"].as_str().unwrap().to_owned()
}

#[tokio::test]
async fn catalog_form_is_authenticated_explicit_and_reversible_without_a_dialogue_turn() {
    let root = std::env::temp_dir().join(format!("dogido-catalog-{}", uuid::Uuid::new_v4()));
    let mut config = crate::dialogue::DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        language_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_dir = root.clone();
    let d = crate::dialogue::Dialogue::new(config).unwrap();
    let app = Application::new(ServerConfig {
        auth_token: Some("catalog-test".into()),
        dialogue: Some(d.clone()),
        ..Default::default()
    });
    let router = app.router();
    let auth = [("authorization", "Bearer catalog-test")];
    let before = d.snapshot(None);
    let (status, view) = call(&router, "GET", "/api/v1/catalog", None, &auth).await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        view["entries"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["id"] == "biome:meadow" && r["surface"] == "草地")
    );
    assert!(!root.exists(), "read-only catalogue creates no storage");
    let edit = json!({"surface":"草地","reading":"くさち","entry_id":"biome:meadow","wrong_reading":"そうち","expected_id":null});
    assert_eq!(
        call(
            &router,
            "PUT",
            "/api/v1/catalog/readings",
            Some(edit.clone()),
            &[]
        )
        .await
        .0,
        StatusCode::UNAUTHORIZED
    );
    for bad in [
        json!({"reading":"草地"}),
        json!({"entry_id":"mob:zombie"}),
        json!({"source":"voice"}),
        json!({"wrong_reading":"くさち"}),
    ] {
        let mut body = edit.clone();
        body.as_object_mut()
            .unwrap()
            .extend(bad.as_object().unwrap().clone());
        assert_eq!(
            call(
                &router,
                "PUT",
                "/api/v1/catalog/readings",
                Some(body),
                &auth
            )
            .await
            .0,
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }
    assert!(!root.exists());
    let (status, saved) = call(
        &router,
        "PUT",
        "/api/v1/catalog/readings",
        Some(edit.clone()),
        &auth,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(saved["correction"]["source"], "biome:meadow");
    assert_eq!(saved["correction"]["origin"], "catalog_form");
    assert!(saved["correction"]["session_id"].is_null());
    assert_eq!(
        call(
            &router,
            "PUT",
            "/api/v1/catalog/readings",
            Some(edit),
            &auth
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let remove = json!({"surface":"草地","expected_id":saved["correction"]["id"]});
    assert_eq!(
        call(
            &router,
            "DELETE",
            "/api/v1/catalog/readings",
            Some(remove.clone()),
            &auth
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &router,
            "DELETE",
            "/api/v1/catalog/readings",
            Some(remove),
            &auth
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    let store = crate::haiku_record::MemoryStore::new(&root);
    assert!(store.reading_corrections().unwrap().is_empty());
    assert_eq!(
        std::fs::read_to_string(store.corrections_path())
            .unwrap()
            .lines()
            .count(),
        2
    );
    assert_eq!(
        d.snapshot(None),
        before,
        "dictionary form cannot create turns or mutate workshop"
    );
    app.shutdown().await.unwrap();
    std::fs::remove_dir_all(root).unwrap();
}

#[tokio::test]
async fn sessions_heartbeat_close_and_reconnect_keep_existing_contracts() {
    let app = Application::new(ServerConfig::default());
    let router = app.router();
    let old = "ses_before_restart";
    let (status, body) = call(
        &router,
        "POST",
        "/api/v1/game-events",
        Some(event()),
        &[("x-dogido-session-id", old)],
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(
        body,
        json!({"detail":{"code":"unknown_session_id","session_id":old}})
    );
    assert_eq!(
        call(
            &router,
            "GET",
            &format!("/api/v1/haiku-workshop/snapshot?session_id={old}"),
            None,
            &[]
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    let id = create(&router).await;
    let another = create(&router).await;
    assert_ne!(id, another);
    let heartbeat = format!("/api/v1/adapter-sessions/{id}/heartbeat");
    let (status, _) = call(
        &router,
        "POST",
        &heartbeat,
        Some(json!({"last_sequence":42,"sent_at":"2026-09-21T00:00:00Z"})),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let snapshot = format!("/api/v1/haiku-workshop/snapshot?session_id={id}");
    let (_, first) = call(&router, "GET", &snapshot, None, &[]).await;
    assert_eq!(first["observed_sequence"], 42);
    assert_eq!(first["state"], "closed");
    assert_eq!(first["canonical_lines"], json!([]));
    assert_eq!(call(&router, "GET", &snapshot, None, &[]).await.1, first);
    assert_eq!(
        call(
            &router,
            "GET",
            &format!("/api/v1/haiku-workshop/snapshot?session_id={another}"),
            None,
            &[]
        )
        .await
        .1["observed_sequence"],
        0
    );
    let (_, display) = call(&router, "GET", "/api/v1/display/snapshot", None, &[]).await;
    assert_eq!(display["minecraft"]["active_sessions"], 2);
    assert_eq!(display["minecraft"]["state"], "connected");
    assert_eq!(display["migration"]["dialogue_ready"], false);
    for _ in 0..2 {
        assert_eq!(
            call(
                &router,
                "DELETE",
                &format!("/api/v1/adapter-sessions/{id}"),
                None,
                &[]
            )
            .await
            .1,
            json!({"ok":true,"session_id":id})
        );
    }
    assert_eq!(
        call(&router, "GET", &snapshot, None, &[]).await.0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(
            &router,
            "POST",
            &heartbeat,
            Some(json!({"sent_at":"2026-09-21T00:00:00Z"})),
            &[]
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    assert_ne!(create(&router).await, id);
    app.shutdown().await.unwrap();
}

#[tokio::test]
async fn auth_and_validation_do_not_create_sessions() {
    let app = Application::new(ServerConfig {
        auth_token: Some("comparison-token".into()),
        ..ServerConfig::default()
    });
    let router = app.router();
    assert_eq!(
        call(&router, "GET", "/healthz", None, &[]).await.0,
        StatusCode::OK
    );
    for headers in [vec![], vec![("authorization", "Bearer wrong")]] {
        for path in [
            "/api/v1/display/snapshot",
            "/api/v1/haiku-workshop/snapshot?session_id=x",
            "/api/v1/memory/haiku",
        ] {
            assert_eq!(
                call(&router, "GET", path, None, &headers).await.0,
                StatusCode::UNAUTHORIZED
            );
        }
        assert_eq!(
            call(
                &router,
                "POST",
                "/api/v1/adapter-sessions",
                Some(registration()),
                &headers
            )
            .await
            .0,
            StatusCode::UNAUTHORIZED
        );
    }
    let auth = [("authorization", "Bearer comparison-token")];
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/adapter-sessions",
            Some(json!({})),
            &auth
        )
        .await
        .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    assert_eq!(
        call(
            &router,
            "GET",
            "/api/v1/haiku-workshop/snapshot",
            None,
            &auth
        )
        .await
        .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let (_, before) = call(&router, "GET", "/api/v1/display/snapshot", None, &auth).await;
    assert_eq!(before["minecraft"]["registered_sessions"], 0);
    let (status, session) = call(
        &router,
        "POST",
        "/api/v1/adapter-sessions",
        Some(registration()),
        &auth,
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    let heartbeat = format!(
        "/api/v1/adapter-sessions/{}/heartbeat",
        session["session_id"].as_str().unwrap()
    );
    for body in [
        json!({"last_sequence":-1,"sent_at":"2026-09-21T00:00:00Z"}),
        json!({"sent_at":"invalid"}),
    ] {
        assert_eq!(
            call(&router, "POST", &heartbeat, Some(body), &auth).await.0,
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }
    app.shutdown().await.unwrap();
}

#[tokio::test]
async fn unimplemented_inputs_are_never_acknowledged_as_processed() {
    let app = Application::new(ServerConfig::default());
    let router = app.router();
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/player-input",
            Some(json!({"text":"こんにちは"})),
            &[]
        )
        .await
        .1,
        json!({"accepted":false,"reason":"no_active_session"})
    );
    let id = create(&router).await;
    for (path, body) in [
        ("/api/v1/game-events", event()),
        ("/api/v1/game-events/batch", json!({"events":[event()]})),
        (
            "/api/v1/player-input",
            json!({"text":"剣に持ち替えて","source":"voice"}),
        ),
    ] {
        let (status, body) = call(
            &router,
            "POST",
            path,
            Some(body),
            &[("x-dogido-session-id", &id)],
        )
        .await;
        assert_eq!(status, StatusCode::NOT_IMPLEMENTED);
        assert_eq!(body["accepted"], false);
        assert_eq!(body["reason"], "not_implemented");
        assert!(body.get("commands").is_none());
        assert!(body.get("state").is_none());
    }
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/game-events/batch",
            Some(json!({"events":vec![event();26]})),
            &[]
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/player-input",
            Some(json!({"text":" "})),
            &[]
        )
        .await
        .1["reason"],
        "empty_text"
    );
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/player-input",
            Some(json!({"text":"hi","source":"other"})),
            &[]
        )
        .await
        .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let (_, display) = call(&router, "GET", "/api/v1/display/snapshot", None, &[]).await;
    assert_eq!(display["utterances"], json!([]));
    let (_, hud) = call(
        &router,
        "GET",
        &format!("/api/v1/haiku-workshop/snapshot?session_id={id}"),
        None,
        &[],
    )
    .await;
    assert_eq!(hud["observed_sequence"], 0);
    app.shutdown().await.unwrap();
}

#[tokio::test]
async fn blocked_worker_keeps_snapshots_readable_and_cancelled_waiter_keeps_operation_order() {
    let app = Application::new(ServerConfig::default());
    let router = app.router();
    let id = create(&router).await;
    let (entered, ready) = oneshot::channel();
    let (release, blocked) = oneshot::channel();
    let (reply, pause_done) = oneshot::channel();
    app.state
        .commands
        .send(Command {
            operation: Operation::Pause {
                entered,
                release: blocked,
            },
            reply,
        })
        .unwrap_or_else(|_| panic!("worker closed"));
    ready.await.unwrap();
    let (reply, cancelled_waiter) = oneshot::channel();
    app.state
        .commands
        .send(Command {
            operation: Operation::Close(id.clone()),
            reply,
        })
        .unwrap_or_else(|_| panic!("worker closed"));
    drop(cancelled_waiter);
    let (reply, recreated) = oneshot::channel();
    app.state
        .commands
        .send(Command {
            operation: Operation::Create(serde_json::from_value(registration()).unwrap()),
            reply,
        })
        .unwrap_or_else(|_| panic!("worker closed"));
    let (status, before) = tokio::time::timeout(
        Duration::from_millis(250),
        call(
            &router,
            "GET",
            &format!("/api/v1/haiku-workshop/snapshot?session_id={id}"),
            None,
            &[],
        ),
    )
    .await
    .expect("snapshot queued behind work");
    assert_eq!(status, StatusCode::OK);
    assert_eq!(before["session_id"], id);
    let (_, display) = tokio::time::timeout(
        Duration::from_millis(250),
        call(&router, "GET", "/api/v1/display/snapshot", None, &[]),
    )
    .await
    .expect("display queued behind work");
    assert_eq!(display["minecraft"]["registered_sessions"], 1);
    release.send(()).unwrap();
    pause_done.await.unwrap();
    let new = recreated.await.unwrap();
    assert_ne!(new.body["session_id"], id);
    assert_eq!(
        call(
            &router,
            "GET",
            &format!("/api/v1/haiku-workshop/snapshot?session_id={id}"),
            None,
            &[]
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&router, "GET", "/api/v1/display/snapshot", None, &[])
            .await
            .1["minecraft"]["registered_sessions"],
        1
    );
    app.shutdown().await.unwrap();
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/adapter-sessions",
            Some(registration()),
            &[]
        )
        .await
        .0,
        StatusCode::SERVICE_UNAVAILABLE
    );
}

#[tokio::test]
async fn existing_display_is_embedded_with_rust_status_and_no_external_assets() {
    let app = Application::new(ServerConfig::default());
    let response = app
        .router()
        .oneshot(
            Request::builder()
                .uri("/dogido")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()["content-security-policy"]
            .to_str()
            .unwrap()
            .contains("connect-src 'self'")
    );
    let html = String::from_utf8(
        to_bytes(response.into_body(), 1_000_000)
            .await
            .unwrap()
            .to_vec(),
    )
    .unwrap();
    assert!(html.contains("会話・警告・音声はまだ使えません"));
    assert!(html.contains("実行環境:"));
    assert!(!html.contains("Python環境:"));
    assert!(html.contains("runtime.runtime_environment"));
    assert_eq!(
        include_str!("dogido.html")
            .matches("<!--runtime-status-->")
            .count(),
        1
    );
    assert!(!html.contains("<!--runtime-status-->"));
    assert!(html.contains("item.playback_status"));
    assert!(html.contains("const data = await response.json();\n          renderRuntime(data);"));
    assert!(html.contains("/api/v1/display/snapshot"));
    app.shutdown().await.unwrap();
}

#[tokio::test]
async fn event_validation_precedes_mutation_and_validates_the_whole_batch() {
    let app = Application::new(ServerConfig::default());
    let router = app.router();
    let id = create(&router).await;
    let before = call(&router, "GET", "/api/v1/display/snapshot", None, &[])
        .await
        .1;
    let mut bad = event();
    bad["player"] = json!({"hotbar":{"selected_slot":0,"slots":[{"slot":0},{"slot":0}]}});
    let mut bad_smell = event();
    bad_smell["smell_observation"] = json!({"status":"none","entity_id":"hidden"});
    for body in [json!({}), bad.clone(), bad_smell] {
        let (status, _) = call(
            &router,
            "POST",
            "/api/v1/game-events",
            Some(body),
            &[("x-dogido-session-id", &id)],
        )
        .await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    }
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/game-events/batch",
            Some(json!({"events":[event(), bad]})),
            &[("x-dogido-session-id", &id)]
        )
        .await
        .0,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let after = call(&router, "GET", "/api/v1/display/snapshot", None, &[])
        .await
        .1;
    assert_eq!(before["runtime_revision"], after["runtime_revision"]);
    assert_eq!(
        before["minecraft"]["last_seen_at"],
        after["minecraft"]["last_seen_at"]
    );
    assert_eq!(before["diagnostic_revision"], after["diagnostic_revision"]);
    app.shutdown().await.unwrap();
}

#[tokio::test]
async fn memory_reads_are_authorized_read_only_and_use_the_configured_store() {
    let root = std::env::temp_dir().join(format!("dogido-memory-router-{}", uuid::Uuid::new_v4()));
    let mut config = crate::dialogue::DialogueConfig {
        audio_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    config.haiku.memory_dir = root.clone();
    let dialogue = crate::dialogue::Dialogue::new(config).unwrap();
    let app = Application::new(ServerConfig {
        auth_token: Some("read-test".into()),
        dialogue: Some(dialogue),
        ..Default::default()
    });
    let router = app.router();
    let auth = [("authorization", "Bearer read-test")];
    let before = call(&router, "GET", "/api/v1/display/snapshot", None, &auth)
        .await
        .1;
    for (name, expected) in [
        ("haiku", json!([])),
        (
            "summary",
            json!({"updated_at":null,"startup_summary":"","open_topics":[],"recent_tone":""}),
        ),
    ] {
        let path = format!("/api/v1/memory/{name}");
        assert_eq!(
            call(&router, "GET", &path, None, &[]).await.0,
            StatusCode::UNAUTHORIZED
        );
        let (status, value) = call(&router, "GET", &path, None, &auth).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(value, expected);
    }
    assert_eq!(
        call(&router, "GET", "/api/v1/memory/profile", None, &auth)
            .await
            .1["player_name"],
        "main_player"
    );
    assert!(!root.exists());
    std::fs::create_dir_all(root.join("sessions/one/long_term")).unwrap();
    std::fs::create_dir_all(root.join("long_term")).unwrap();
    std::fs::write(
        root.join("sessions/one/long_term/haiku_entries.jsonl"),
        "{\"id\":\"one\",\"text\":\"保存した句\"}\n",
    )
    .unwrap();
    std::fs::write(
        root.join("long_term/player_profile.json"),
        "{\"player_name\":\"本人\"}",
    )
    .unwrap();
    assert_eq!(
        call(&router, "GET", "/api/v1/memory/haiku", None, &auth)
            .await
            .1,
        json!([{"id":"one","text":"保存した句"}])
    );
    assert_eq!(
        call(&router, "GET", "/api/v1/memory/profile", None, &auth)
            .await
            .1["player_name"],
        "本人"
    );
    let after = call(&router, "GET", "/api/v1/display/snapshot", None, &auth)
        .await
        .1;
    assert_eq!(before["runtime_revision"], after["runtime_revision"]);
    assert_eq!(before["utterances"], after["utterances"]);
    assert!(!root.join("eval").exists());
    app.shutdown().await.unwrap();
    std::fs::remove_dir_all(root).unwrap();
}

#[tokio::test]
async fn disabled_memory_returns_empty_shapes_without_reading_files() {
    let root =
        std::env::temp_dir().join(format!("dogido-memory-disabled-{}", uuid::Uuid::new_v4()));
    let mut config = crate::dialogue::DialogueConfig {
        audio_enabled: false,
        ..Default::default()
    };
    config.haiku.memory_enabled = false;
    config.haiku.enabled = false;
    config.haiku.memory_dir = root.clone();
    let app = Application::new(ServerConfig {
        dialogue: Some(crate::dialogue::Dialogue::new(config).unwrap()),
        ..Default::default()
    });
    let router = app.router();
    for (name, expected) in [
        ("haiku", json!([])),
        ("profile", json!({})),
        ("summary", json!({})),
    ] {
        assert_eq!(
            call(&router, "GET", &format!("/api/v1/memory/{name}"), None, &[]).await,
            (StatusCode::OK, expected)
        );
    }
    assert!(!root.exists());
    app.shutdown().await.unwrap();
}

#[tokio::test]
async fn configured_http_limits_and_session_contract_are_applied() {
    let settings = Settings::merged(&json!({
        "accepted_schema_version": "fixture-version", "heartbeat_interval_ms": 900,
        "max_batch_size": 1, "max_body_kb": 1
    }))
    .unwrap();
    let app = Application::new(ServerConfig {
        settings,
        ..Default::default()
    });
    let router = app.router();
    let (status, registered) = call(
        &router,
        "POST",
        "/api/v1/adapter-sessions",
        Some(registration()),
        &[],
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(registered["accepted_schema_version"], "fixture-version");
    assert_eq!(registered["heartbeat_interval_ms"], 900);
    assert_eq!(registered["max_batch_size"], 1);
    let id = registered["session_id"].as_str().unwrap();
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/game-events/batch",
            Some(json!({"events":[event(), event()]})),
            &[("x-dogido-session-id", id)]
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let mut oversized = registration();
    oversized["player_name"] = "a".repeat(1500).into();
    assert_eq!(
        call(
            &router,
            "POST",
            "/api/v1/adapter-sessions",
            Some(oversized),
            &[]
        )
        .await
        .0,
        StatusCode::PAYLOAD_TOO_LARGE
    );
    app.shutdown().await.unwrap();
}

#[test]
fn http_settings_reject_invalid_limits_and_unknown_keys() {
    for invalid in [
        json!({"max_batch_size":0}),
        json!({"max_body_kb":0}),
        json!({"max_body_kb":u64::MAX}),
        json!({"heartbeat_interval_ms":0}),
        json!({"accepted_schema_version":" "}),
        json!({"typo":3}),
        json!([]),
    ] {
        assert!(Settings::merged(&invalid).is_err(), "{invalid}");
    }
}

#[tokio::test]
async fn dialogue_display_describes_connected_features() {
    let mut config = crate::dialogue::DialogueConfig {
        audio_enabled: false,
        llm_enabled: false,
        language_enabled: false,
        ..Default::default()
    };
    config.haiku.enabled = false;
    let dialogue = crate::dialogue::Dialogue::new(config).unwrap();
    let app = Application::new(ServerConfig {
        dialogue: Some(dialogue),
        ..Default::default()
    });
    let router = app.router();
    let health = call(&router, "GET", "/healthz", None, &[]).await.1;
    assert_eq!(health["phase"], "dialogue");
    assert_eq!(health["dialogue_ready"], true);
    let response = router
        .oneshot(
            Request::builder()
                .uri("/dogido")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let html = String::from_utf8(
        to_bytes(response.into_body(), 1_000_000)
            .await
            .unwrap()
            .to_vec(),
    )
    .unwrap();
    assert!(html.contains("川柳の共同編集・剣の持ち替えに対応"));
    assert!(!html.contains("戦闘・川柳・世界操作は未接続"));
    assert!(!html.contains("<!--runtime-status-->"));
    app.shutdown().await.unwrap();
}
