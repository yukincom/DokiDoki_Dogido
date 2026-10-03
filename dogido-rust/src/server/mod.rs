//! Rust本体のHTTP APIと表示画面。接続試験モードは会話・音声・保存を開始しない。
mod catalog;
pub mod contracts;
mod runtime;
mod settings;
pub use settings::Settings;

use std::sync::Arc;

use axum::{
    Json, Router,
    extract::{
        DefaultBodyLimit, Path, Query, Request, State,
        rejection::{JsonRejection, QueryRejection},
    },
    http::{HeaderMap, HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
    routing::{delete, get, post},
};
use chrono::Utc;
use serde_json::{Value, json};
use tokio::sync::{mpsc, oneshot, watch};

use crate::events::{BatchEvents, GameEvent};
use contracts::{
    HeartbeatRequest, PlayerInputRequest, SessionRequest, SnapshotQuery, WorkshopQuery,
};
use runtime::{Command, Operation, Published};

#[derive(Clone, Default)]
pub struct ServerConfig {
    pub auth_token: Option<String>,
    pub settings: Settings,
    pub dialogue: Option<Arc<crate::dialogue::Dialogue>>,
}

pub(super) struct ApiReply {
    status: StatusCode,
    body: Value,
}

impl ApiReply {
    fn new(status: StatusCode, body: Value) -> Self {
        Self { status, body }
    }
    fn ok(body: Value) -> Self {
        Self::new(StatusCode::OK, body)
    }
    fn error(status: StatusCode, detail: Value) -> Self {
        Self::new(status, json!({"detail": detail}))
    }
    fn unavailable() -> Self {
        Self::error(StatusCode::SERVICE_UNAVAILABLE, json!("server_stopping"))
    }
    // dialogueを構築していない接続試験の応答。各呼出元がNoneを確認してから使う。
    // dialogue内の生成失敗・音声無効・機能別失敗には使わない。phaseもこの前提で固定する。
    fn unsupported(feature: &str) -> Self {
        Self::new(
            StatusCode::NOT_IMPLEMENTED,
            json!({
                "accepted": false, "reason": "not_implemented",
                "detail": {"code": "not_implemented", "feature": feature, "phase": "connection_only"},
            }),
        )
    }
}

impl IntoResponse for ApiReply {
    fn into_response(self) -> Response {
        (self.status, Json(self.body)).into_response()
    }
}

#[derive(Clone)]
struct AppState {
    commands: mpsc::UnboundedSender<Command>,
    published: watch::Receiver<Arc<Published>>,
    auth_token: Option<Arc<str>>,
    dialogue: Option<Arc<crate::dialogue::Dialogue>>,
}

impl AppState {
    async fn submit(&self, operation: Operation) -> ApiReply {
        let (reply, receive) = oneshot::channel();
        if self.commands.send(Command { operation, reply }).is_err() {
            return ApiReply::unavailable();
        }
        receive.await.unwrap_or_else(|_| ApiReply::unavailable())
    }

    fn view(&self) -> Arc<Published> {
        self.published.borrow().clone()
    }
}

pub struct Application {
    state: AppState,
    router: Router,
    worker: Option<tokio::task::JoinHandle<()>>,
}

impl Application {
    pub fn new(config: ServerConfig) -> Self {
        let max_body_bytes = config.settings.max_body_kb * 1024;
        let auth_token = config
            .auth_token
            .as_deref()
            .filter(|key| !key.is_empty())
            .map(Arc::from);
        let dialogue = config.dialogue.clone();
        let (commands, published, worker) = runtime::spawn(config);
        let state = AppState {
            commands,
            published,
            auth_token,
            dialogue,
        };
        let router = Router::new()
            .route("/healthz", get(health))
            .route("/dogido", get(display_page))
            .route("/rust-chat", get(chat_page))
            .route("/catalog", get(catalog::page))
            .route("/api/v1/catalog", get(catalog::snapshot))
            .route(
                "/api/v1/catalog/readings",
                axum::routing::put(catalog::save).delete(catalog::remove),
            )
            .route("/api/v1/rust-dialogue/snapshot", get(chat_snapshot))
            .route("/api/v1/rust-dialogue/interrupt", post(chat_interrupt))
            .route("/api/v1/adapter-sessions", post(create_session))
            .route("/api/v1/adapter-sessions/{id}/heartbeat", post(heartbeat))
            .route("/api/v1/adapter-sessions/{id}", delete(close_session))
            .route("/api/v1/display/snapshot", get(display_snapshot))
            .route("/api/v1/haiku-workshop/snapshot", get(workshop_snapshot))
            .route("/api/v1/game-events", post(game_event))
            .route("/api/v1/game-events/batch", post(game_event_batch))
            .route("/api/v1/player-input", post(player_input))
            .route("/api/v1/voice-input/context", get(voice_context))
            .route("/api/v1/voice-input/diagnostics", post(voice_diagnostic))
            .route("/api/v1/memory/haiku", get(memory_haiku))
            .route("/api/v1/memory/profile", get(memory_profile))
            .route("/api/v1/memory/summary", get(memory_summary))
            .fallback(|| async { ApiReply::error(StatusCode::NOT_FOUND, json!("Not Found")) })
            .layer(DefaultBodyLimit::max(max_body_bytes))
            .layer(middleware::from_fn_with_state(state.clone(), access))
            .with_state(state.clone());
        Self {
            state,
            router,
            worker: Some(worker),
        }
    }

    pub fn router(&self) -> Router {
        self.router.clone()
    }

    pub async fn shutdown(mut self) -> anyhow::Result<()> {
        let _ = self.state.submit(Operation::Shutdown).await;
        if let Some(worker) = self.worker.take() {
            worker.await?;
        }
        if let Some(dialogue) = &self.state.dialogue {
            dialogue.shutdown().await;
        }
        Ok(())
    }
}

impl Drop for Application {
    fn drop(&mut self) {
        if let Some(dialogue) = &self.state.dialogue {
            dialogue.cancel_all();
        }
        if let Some(worker) = &self.worker {
            worker.abort();
        }
    }
}

async fn access(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let path = request.uri().path().to_owned();
    let authorized = !path.starts_with("/api/")
        || state.auth_token.as_ref().is_none_or(|key| {
            request
                .headers()
                .get(header::AUTHORIZATION)
                .and_then(|value| value.to_str().ok())
                .is_some_and(|value| value.strip_prefix("Bearer ") == Some(key.as_ref()))
        });
    let mut response = if authorized {
        next.run(request).await
    } else {
        ApiReply::error(StatusCode::UNAUTHORIZED, json!("unauthorized")).into_response()
    };
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    if response.status().is_client_error() || response.status().is_server_error() {
        // URLクエリ・認証ヘッダー・本文は端末ログへ出さない。
        tracing::warn!(
            event = "http_rejected",
            path,
            status = response.status().as_u16()
        );
    }
    response
}

async fn health(State(state): State<AppState>) -> Json<Value> {
    let enabled = state.dialogue.is_some();
    Json(
        json!({"ok": true, "service": "dogido-server", "version": env!("CARGO_PKG_VERSION"),
        "runtime": "rust", "phase": if enabled {"dialogue"} else {"connection_only"}, "dialogue_ready": enabled, "llm_enabled": state.dialogue.as_ref().is_some_and(|d| d.llm_enabled())}),
    )
}

async fn display_page(State(state): State<AppState>) -> Response {
    let banner = if state.dialogue.is_some() {
        "ドギド本体。会話・戦闘・川柳の共同編集・剣の持ち替えに対応。<a href=\"/rust-chat\">会話入力を開く</a>"
    } else {
        "接続テスト用です。会話・警告・音声はまだ使えません。"
    };
    let (before, after) = include_str!("dogido.html")
        .split_once("<!--runtime-status-->")
        .expect("display page requires a runtime status slot");
    let html = format!("{before}{banner}{after}");
    let mut response = Html(html).into_response();
    response.headers_mut().insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static(
        "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"));
    response
}

fn parse<T>(payload: Result<Json<T>, JsonRejection>) -> Result<T, ApiReply> {
    payload.map(|Json(value)| value).map_err(|error| {
        ApiReply::error(
            if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
                error.status()
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            },
            json!({"code": "invalid_request", "message": error.body_text()}),
        )
    })
}

fn query<T>(payload: Result<Query<T>, QueryRejection>) -> Result<T, ApiReply> {
    payload
        .map(|Query(value)| value)
        .map_err(|_| ApiReply::error(StatusCode::UNPROCESSABLE_ENTITY, json!("invalid query")))
}

async fn create_session(
    State(state): State<AppState>,
    payload: Result<Json<SessionRequest>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    Ok(state
        .submit(Operation::Create(Box::new(parse(payload)?)))
        .await)
}
async fn heartbeat(
    State(state): State<AppState>,
    Path(id): Path<String>,
    payload: Result<Json<HeartbeatRequest>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    Ok(state
        .submit(Operation::Heartbeat(id, parse(payload)?))
        .await)
}
async fn close_session(State(state): State<AppState>, Path(id): Path<String>) -> ApiReply {
    state.submit(Operation::Close(id)).await
}
async fn display_snapshot(
    State(state): State<AppState>,
    payload: Result<Query<SnapshotQuery>, QueryRejection>,
) -> Result<Json<Value>, ApiReply> {
    Ok(Json(
        state.view().display(query(payload)?.session_id, Utc::now()),
    ))
}
async fn workshop_snapshot(
    State(state): State<AppState>,
    payload: Result<Query<WorkshopQuery>, QueryRejection>,
) -> Result<Json<Value>, ApiReply> {
    state
        .view()
        .workshop(&query(payload)?.session_id)
        .map(Json)
        .ok_or_else(|| ApiReply::error(StatusCode::NOT_FOUND, json!("unknown_session_id")))
}

fn session_header(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-dogido-session-id")
        .and_then(|value| value.to_str().ok())
        .filter(|id| !id.is_empty())
        .map(str::to_owned)
}

async fn game_event(
    State(state): State<AppState>,
    headers: HeaderMap,
    payload: Result<Json<GameEvent>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    let event = parse(payload)?;
    if state.dialogue.is_some() {
        return Ok(state
            .submit(Operation::DialogueEvents {
                session_id: session_header(&headers),
                events: vec![event],
                batch: false,
                key: headers
                    .get("idempotency-key")
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_owned),
            })
            .await);
    }
    Ok(state
        .submit(Operation::GameEvent {
            session_id: session_header(&headers),
            batch_size: None,
        })
        .await)
}
async fn game_event_batch(
    State(state): State<AppState>,
    headers: HeaderMap,
    payload: Result<Json<BatchEvents>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    let body = parse(payload)?;
    let size = body.events.len();
    if state.dialogue.is_some() {
        return Ok(state
            .submit(Operation::DialogueEvents {
                session_id: session_header(&headers),
                events: body.events,
                batch: true,
                key: None,
            })
            .await);
    }
    Ok(state
        .submit(Operation::GameEvent {
            session_id: session_header(&headers),
            batch_size: Some(size),
        })
        .await)
}
async fn player_input(
    State(state): State<AppState>,
    payload: Result<Json<PlayerInputRequest>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    Ok(state.submit(Operation::PlayerInput(parse(payload)?)).await)
}
async fn voice_context(State(state): State<AppState>) -> ApiReply {
    if let Some(d) = &state.dialogue {
        return ApiReply::ok(d.voice_context());
    }
    state.submit(Operation::Unsupported("voice_context")).await
}
async fn voice_diagnostic(
    State(state): State<AppState>,
    payload: Result<Json<Value>, JsonRejection>,
) -> ApiReply {
    if state.dialogue.is_some() {
        let value = match parse(payload) {
            Ok(value) => value,
            Err(error) => return error,
        };
        tracing::info!(event="voice_input", diagnostic=%value);
        return ApiReply::ok(json!({"accepted":true}));
    }
    state
        .submit(Operation::Unsupported("voice_diagnostics"))
        .await
}
async fn memory_view(state: AppState, view: crate::memory_api::View) -> ApiReply {
    let Some(dialogue) = state.dialogue else {
        return ApiReply::ok(view.disabled());
    };
    match dialogue.memory_view(view).await {
        Ok(value) => ApiReply::ok(value),
        Err(error) => {
            tracing::warn!(event="memory_view_failed", %error);
            ApiReply::error(
                StatusCode::INTERNAL_SERVER_ERROR,
                json!("memory_read_failed"),
            )
        }
    }
}
async fn memory_haiku(State(state): State<AppState>) -> ApiReply {
    memory_view(state, crate::memory_api::View::Haiku).await
}
async fn memory_profile(State(state): State<AppState>) -> ApiReply {
    memory_view(state, crate::memory_api::View::Profile).await
}
async fn memory_summary(State(state): State<AppState>) -> ApiReply {
    memory_view(state, crate::memory_api::View::Summary).await
}

#[cfg(test)]
mod tests;

async fn chat_page() -> Html<&'static str> {
    Html(include_str!("chat.html"))
}
async fn chat_snapshot(State(state): State<AppState>, Query(q): Query<SnapshotQuery>) -> ApiReply {
    match &state.dialogue {
        Some(d) => ApiReply::ok(d.snapshot(q.session_id.as_deref())),
        None => ApiReply::unsupported("player_dialogue"),
    }
}
async fn chat_interrupt(
    State(state): State<AppState>,
    payload: Result<Json<SnapshotQuery>, JsonRejection>,
) -> Result<ApiReply, ApiReply> {
    let q = parse(payload)?;
    let Some(d) = &state.dialogue else {
        return Ok(ApiReply::unsupported("player_dialogue"));
    };
    let Some(id) = q.session_id.or_else(|| d.only_session()) else {
        return Ok(ApiReply::ok(
            json!({"accepted":false,"reason":"select_one_session"}),
        ));
    };
    d.interrupt(&id);
    Ok(ApiReply::ok(json!({"accepted":true})))
}
