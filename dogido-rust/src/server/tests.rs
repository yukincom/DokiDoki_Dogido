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
    assert!(html.contains("runtime.runtime_environment || runtime.python_environment"));
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
