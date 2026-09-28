//! 接続専用と、明示選択した平時会話preview。記憶store・世界操作は未接続。
pub mod contracts;
mod runtime;

use std::sync::Arc;

use axum::{
    Json, Router,
    extract::{
        Path, Query, Request, State,
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

#[derive(Clone)]
pub struct ServerConfig {
    pub auth_token: Option<String>,
    pub accepted_schema_version: String,
    pub heartbeat_interval_ms: u64,
    pub max_batch_size: usize,
    pub dialogue: Option<Arc<crate::dialogue::Dialogue>>,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            auth_token: None,
            accepted_schema_version: "2026-05-24".into(),
            heartbeat_interval_ms: 5000,
            max_batch_size: 25,
            dialogue: None,
        }
    }
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
            .route("/api/v1/memory/haiku", get(memory))
            .route("/api/v1/memory/profile", get(memory))
            .route("/api/v1/memory/summary", get(memory))
            .fallback(|| async { ApiReply::error(StatusCode::NOT_FOUND, json!("Not Found")) })
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
        "runtime": "rust", "phase": if enabled {"dialogue_preview"} else {"connection_only"}, "dialogue_ready": enabled, "llm_enabled": enabled}),
    )
}

async fn display_page(State(state): State<AppState>) -> Response {
    let banner = if state.dialogue.is_some() {
        "Rust版・平時の会話試験。戦闘・川柳・世界操作は未接続。<a href=\"/rust-chat\">会話入力を開く</a>"
    } else {
        "接続テスト用です。会話・警告・音声はまだ使えません。"
    };
    // Python版の画面は変更せず、同じHTMLを埋め込んで実行環境の表示だけ合わせる。
    let html = include_str!("../../../dogido_server/static/dogido.html")
        .replace("Python環境:", "実行環境:")
        .replace(
            "runtime.python_environment || '不明'",
            "runtime.runtime_environment || runtime.python_environment || '不明'",
        )
        .replace(
            "`${environment}（仮想環境ではありません）`",
            "`${environment}`",
        )
        .replace("tag.textContent = categoryLabels[item.category] || '発言';",
            "tag.textContent = item.playback_status ? ({routing:'内容を確認中',waiting_for_safety:'安全になるまで保留',not_selected:'発話なし',generating:'生成中',queued:'音声準備中',started:'再生中',completed:'再生完了',cancelled:'取消',failed:'失敗',quiet:'発話なし',unsupported:'未接続'}[item.playback_status] || item.playback_status) : (categoryLabels[item.category] || '発言');")
        // heartbeatが途切れるとrevisionは止まるため、接続状態だけは毎回描画する。
        .replace(
            "const data = await response.json();",
            "const data = await response.json();\n          renderRuntime(data);",
        )
        .replace(
            "<main class=\"page\">",
            &format!("<main class=\"page\"><p role=\"status\">{banner}</p>"),
        );
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
async fn memory(State(state): State<AppState>) -> ApiReply {
    state.submit(Operation::Unsupported("memory")).await
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
