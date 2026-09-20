use std::{
    collections::{BTreeMap, VecDeque},
    sync::Arc,
};

use axum::http::StatusCode;
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use tokio::sync::{mpsc, oneshot, watch};
use uuid::Uuid;

use super::{
    ApiReply, ServerConfig,
    contracts::{HeartbeatRequest, PlayerInputRequest, SessionRequest},
};

pub(super) enum Operation {
    Create(Box<SessionRequest>),
    Heartbeat(String, HeartbeatRequest),
    Close(String),
    GameEvent {
        session_id: Option<String>,
        batch_size: Option<usize>,
    },
    PlayerInput(PlayerInputRequest),
    Unsupported(&'static str),
    Shutdown,
    #[cfg(test)]
    Pause {
        entered: oneshot::Sender<()>,
        release: oneshot::Receiver<()>,
    },
}

pub(super) struct Command {
    pub operation: Operation,
    pub reply: oneshot::Sender<ApiReply>,
}

#[derive(Clone)]
struct Session {
    registration: SessionRequest,
    last_seen: DateTime<Utc>,
    last_sequence: Option<u64>,
}

/// GET側には変更権限を渡さず、処理済みの投影だけを公開する。
pub(super) struct Published {
    runtime: Value,
    sessions: BTreeMap<String, Session>,
    runtime_revision: u64,
    hud_revision: u64,
    diagnostics: VecDeque<Value>,
    diagnostic_revision: u64,
    heartbeat_interval_ms: u64,
}

impl Published {
    pub fn display(&self, session_id: Option<String>, now: DateTime<Utc>) -> Value {
        let freshness = (self.heartbeat_interval_ms.saturating_mul(3) / 1000).max(15);
        let fresh: Vec<_> = self
            .sessions
            .values()
            .filter(|s| (now - s.last_seen).num_milliseconds() <= (freshness * 1000) as i64)
            .collect();
        let mut adapters: Vec<_> = fresh
            .iter()
            .map(|s| {
                format!(
                    "{} {}",
                    s.registration.adapter_name, s.registration.adapter_version
                )
                .trim()
                .to_owned()
            })
            .collect();
        adapters.sort();
        adapters.dedup();
        let connection = if !fresh.is_empty() {
            "connected"
        } else if self.sessions.is_empty() {
            "not_connected"
        } else {
            "stale"
        };
        json!({
            "schema_version": 1, "revision": 0, "generated_at": now,
            "session_id": session_id, "utterances": [], "references": [],
            "retention": {"storage": "process_memory", "max_utterances": 200, "cleared_on_restart": true},
            "diagnostic_schema_version": 1, "diagnostic_revision": self.diagnostic_revision,
            "diagnostics": self.diagnostics,
            "diagnostic_retention": {"storage": "process_memory", "max_entries": 1000, "cleared_on_restart": true},
            "runtime_revision": self.runtime_revision, "runtime": self.runtime,
            "minecraft": {
                "state": connection, "connected": !fresh.is_empty(), "active_sessions": fresh.len(),
                "registered_sessions": self.sessions.len(), "adapters": adapters,
                "last_seen_at": self.sessions.values().map(|s| s.last_seen).max(),
                "freshness_seconds": freshness,
            },
            "migration": {"phase": "connection_only", "dialogue_ready": false, "llm_enabled": false},
        })
    }

    pub fn workshop(&self, session_id: &str) -> Option<Value> {
        let session = self.sessions.get(session_id)?;
        Some(json!({
            "schema_version": 1, "session_id": session_id, "revision": self.hud_revision,
            "observed_sequence": session.last_sequence.unwrap_or(0), "workshop_id": null,
            "state": "closed", "character_state": "normal", "canonical_lines": [],
            "pending_lines": [], "editing": false, "selected_line": null, "provisional_resume": false,
        }))
    }
}

struct Runtime {
    config: ServerConfig,
    data: Published,
}

impl Runtime {
    fn new(config: ServerConfig) -> Self {
        let mut runtime = Self {
            data: Published {
                runtime: json!({
                    "instance_id": new_id("run"), "started_at": Utc::now(),
                    "source_kind": "rust_migration", "source_label_ja": "Rust版・接続テスト",
                    "runtime_environment": "Rust", "python_environment": null,
                    "virtual_environment": false, "process_id": std::process::id(),
                }),
                sessions: BTreeMap::new(),
                runtime_revision: 0,
                hud_revision: 0,
                diagnostics: VecDeque::new(),
                diagnostic_revision: 0,
                heartbeat_interval_ms: config.heartbeat_interval_ms,
            },
            config,
        };
        runtime.record(
            "server_ready",
            "Rust版の接続テスト。会話・警告・音声は未対応。外部AI接続なし。".into(),
        );
        runtime
    }

    fn record(&mut self, event: &'static str, message: String) {
        self.data.diagnostic_revision += 1;
        if self.data.diagnostics.len() == 1000 {
            self.data.diagnostics.pop_front();
        }
        self.data.diagnostics.push_back(json!({
            "entry_id": format!("log_{}", self.data.diagnostic_revision), "created_at": Utc::now(),
            "level": "INFO", "logger": "dogido.rust", "source": "server", "event": event,
            "message": message,
        }));
        tracing::info!(event, message);
    }

    fn snapshot(&self) -> Arc<Published> {
        Arc::new(Published {
            runtime: self.data.runtime.clone(),
            sessions: self.data.sessions.clone(),
            runtime_revision: self.data.runtime_revision,
            hud_revision: self.data.hud_revision,
            diagnostics: self.data.diagnostics.clone(),
            diagnostic_revision: self.data.diagnostic_revision,
            heartbeat_interval_ms: self.data.heartbeat_interval_ms,
        })
    }

    fn apply(&mut self, operation: Operation) -> ApiReply {
        match operation {
            Operation::Create(registration) => {
                let id = new_id("ses");
                let now = Utc::now();
                self.record(
                    "adapter_session_created",
                    format!("接続を登録しました session_id={id}"),
                );
                self.data.sessions.insert(
                    id.clone(),
                    Session {
                        registration: *registration,
                        last_seen: now,
                        last_sequence: None,
                    },
                );
                self.data.runtime_revision += 1;
                self.data.hud_revision += 1;
                ApiReply::new(
                    StatusCode::CREATED,
                    json!({
                        "session_id": id, "accepted_schema_version": self.config.accepted_schema_version,
                        "server_time": now, "event_endpoint": "/api/v1/game-events",
                        "batch_endpoint": "/api/v1/game-events/batch", "heartbeat_interval_ms": self.config.heartbeat_interval_ms,
                        "max_batch_size": self.config.max_batch_size,
                    }),
                )
            }
            Operation::Heartbeat(id, request) => {
                let Some(session) = self.data.sessions.get_mut(&id) else {
                    return ApiReply::error(StatusCode::NOT_FOUND, json!("unknown session_id"));
                };
                session.last_seen = Utc::now(); // クライアント時計で接続状態を延長しない。
                if let Some(sequence) = request.last_sequence {
                    if session.last_sequence != Some(sequence) {
                        self.data.hud_revision += 1;
                    }
                    session.last_sequence = Some(sequence);
                }
                self.data.runtime_revision += 1;
                ApiReply::ok(json!({"ok": true, "session_id": id, "server_time": Utc::now()}))
            }
            Operation::Close(id) => {
                if self.data.sessions.remove(&id).is_some() {
                    self.data.runtime_revision += 1;
                    self.data.hud_revision += 1;
                    self.record(
                        "adapter_session_closed",
                        format!("接続を終了しました session_id={id}"),
                    );
                }
                ApiReply::ok(json!({"ok": true, "session_id": id}))
            }
            Operation::GameEvent {
                session_id,
                batch_size,
            } => {
                if batch_size.is_some_and(|size| size > self.config.max_batch_size) {
                    return ApiReply::error(
                        StatusCode::BAD_REQUEST,
                        json!(format!(
                            "events exceeds max_batch_size={}",
                            self.config.max_batch_size
                        )),
                    );
                }
                if let Some(id) = session_id {
                    let Some(session) = self.data.sessions.get_mut(&id) else {
                        return ApiReply::error(
                            StatusCode::CONFLICT,
                            json!({"code": "unknown_session_id", "session_id": id}),
                        );
                    };
                    // 受信は接続の根拠になるが、世界処理・sequence消費の成功にはしない。
                    session.last_seen = Utc::now();
                    self.data.runtime_revision += 1;
                }
                // 観測を解釈・処理していないため、202や架空のnormal状態を返さない。
                ApiReply::unsupported("game_event_processing")
            }
            Operation::PlayerInput(request) => {
                if request.text.trim().is_empty() {
                    ApiReply::ok(json!({"accepted": false, "reason": "empty_text"}))
                } else if self.data.sessions.is_empty() {
                    ApiReply::ok(json!({"accepted": false, "reason": "no_active_session"}))
                } else {
                    ApiReply::unsupported("player_dialogue")
                }
            }
            Operation::Unsupported(feature) => ApiReply::unsupported(feature),
            Operation::Shutdown => {
                self.data.sessions.clear();
                self.data.runtime_revision += 1;
                self.data.hud_revision += 1;
                self.record("server_stopped", "接続テストを終了しました。".into());
                ApiReply::ok(json!({"ok": true}))
            }
            #[cfg(test)]
            Operation::Pause { .. } => unreachable!("test pause is handled before applying"),
        }
    }
}

fn new_id(prefix: &str) -> String {
    format!("{prefix}_{}", &Uuid::new_v4().simple().to_string()[..12])
}

pub(super) fn spawn(
    config: ServerConfig,
) -> (
    mpsc::UnboundedSender<Command>,
    watch::Receiver<Arc<Published>>,
    tokio::task::JoinHandle<()>,
) {
    let mut runtime = Runtime::new(config);
    let (sender, mut receiver) = mpsc::unbounded_channel::<Command>();
    let (publish, views) = watch::channel(runtime.snapshot());
    let worker = tokio::spawn(async move {
        while let Some(command) = receiver.recv().await {
            #[cfg(test)]
            let command = match command.operation {
                Operation::Pause { entered, release } => {
                    let _ = entered.send(());
                    let _ = release.await;
                    let _ = command.reply.send(ApiReply::ok(json!({"ok": true})));
                    continue;
                }
                _ => command,
            };
            let stopping = matches!(command.operation, Operation::Shutdown);
            let result = runtime.apply(command.operation);
            // HTTP側の切断・取消でも、状態変更と投影公開までは一度実行する。
            publish.send_replace(runtime.snapshot());
            let _ = command.reply.send(result);
            if stopping {
                break;
            }
        }
    });
    (sender, views, worker)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presence_expires_without_mutating_the_published_state() {
        let mut runtime = Runtime::new(ServerConfig::default());
        let registration: SessionRequest = serde_json::from_value(json!({
            "adapter_name": "fabric", "adapter_version": "test", "schema_version": "2026-05-24",
            "player_name": "試験", "capabilities": ["visual_threats"],
            "execution_capabilities": ["client.hotbar.select.v1"], "adapter_build": "future"
        }))
        .unwrap();
        let reply = runtime.apply(Operation::Create(Box::new(registration)));
        let id = reply.body["session_id"].as_str().unwrap();
        let session = &runtime.data.sessions[id];
        assert_eq!(session.registration.capabilities, ["visual_threats"]);
        assert_eq!(
            session.registration.execution_capabilities,
            ["client.hotbar.select.v1"]
        );
        assert_eq!(session.registration.extra["adapter_build"], "future");
        let published = runtime.snapshot();
        let now = session.last_seen;
        assert_eq!(
            published.display(None, now)["minecraft"]["state"],
            "connected"
        );
        assert_eq!(
            published.display(None, now + chrono::Duration::seconds(16))["minecraft"]["state"],
            "stale"
        );
        assert_eq!(published.runtime_revision, 1);
        assert_eq!(published.sessions.len(), 1);
    }
}
