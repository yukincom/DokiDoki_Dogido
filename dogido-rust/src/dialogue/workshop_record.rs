//! Append-only observations of every workshop input route. Never read by a model.
use super::*;
use crate::haiku_record::{Workshop, append_line, locked_file};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc::{self, SyncSender},
};

#[derive(Clone, Copy, Default)]
pub(super) struct Admission {
    pub forwarded: bool,
    pub private: bool,
}

/// Captured at admission, never reconstructed from a later session/ASR state.
#[derive(Clone, Default)]
pub(super) struct Input {
    pub raw: String,
    pub semantic: Option<String>,
    pub private: bool,
    pub epoch: Option<u64>,
}
impl Input {
    pub fn from_row(row: &Value) -> Self {
        Self {
            raw: row["player_input_text"].as_str().unwrap_or("").into(),
            semantic: row["interpreted_player_input_text"]
                .as_str()
                .map(str::to_owned),
            private: row["workshop_record_private"] == true,
            epoch: row["epoch"].as_u64(),
        }
    }
}
/// One bounded owner per active turn/queue entry. Clones share terminal ownership;
/// replay constructs a new attempt with a new epoch, not an ever-growing ID ledger.
#[derive(Clone)]
pub(super) struct Attempt {
    pub before: Value,
    pub input: Input,
    finished: Arc<AtomicBool>,
}
impl Attempt {
    pub fn new(before: Value, input: Input) -> Self {
        Self {
            before,
            input,
            finished: Arc::new(AtomicBool::new(false)),
        }
    }
    pub fn transferred(&self) {
        self.finished.store(true, Ordering::Relaxed);
    }
}

pub(super) fn state(w: Option<&Workshop>) -> Value {
    w.map_or(Value::Null, |w| json!({
        "workshop_id":w.hud_id,"entry_id":w.entry_id.clone().unwrap_or_else(||w.emission.entry_id()),
        "open":w.open,"combat_paused":w.combat_paused(),"close_reason":w.close_reason,
        "version":w.version,"canonical":w.current_lines.iter().map(|l|l.surface_text.as_str()).collect::<Vec<_>>().join("\n"),
        "reading":crate::workshop_edit::reading(&w.current_lines),
        "pending":w.pending.as_ref().map(|p|p.lines.iter().map(|l|l.surface_text.as_str()).collect::<Vec<_>>().join("\n")),
        "pending_id":w.pending.as_ref().map(|p|&p.id),
        "player_idea":w.conversation_candidate.as_ref().map(|p|p.view())
    }))
}
fn relevant(before: &Value, after: &Value) -> bool {
    before["open"] == true || after["open"] == true || (before.is_object() && before != after)
}
fn limited(value: &Value, depth: usize) -> Value {
    if depth == 0 {
        return Value::Null;
    }
    match value {
        Value::String(s) => s.chars().take(1000).collect::<String>().into(),
        Value::Array(a) => a.iter().take(16).map(|v| limited(v, depth - 1)).collect(),
        Value::Object(o) => o
            .iter()
            .take(24)
            .map(|(k, v)| (k.clone(), limited(v, depth - 1)))
            .collect(),
        _ => value.clone(),
    }
}
fn result_fields(result: &Value) -> Value {
    let mut out = json!({});
    for key in [
        "accepted",
        "reason",
        "queued",
        "turn_id",
        "source",
        "epoch",
        "routing_status",
        "resolution",
        "workshop_action",
        "workshop_outcome",
        "workshop_reason",
        "memory_action",
        "memory_outcome",
        "workshop_revision_id",
        "playback_status",
        "cancel_reason",
        "combat_input_outcome",
        "forwarded_input",
    ] {
        if let Some(value) = result.get(key) {
            out[key] = limited(value, 4);
        }
    }
    if let Some(steps) = result["workshop_steps"].as_array() {
        out["steps"] = steps
            .iter()
            .take(6)
            .map(|s| {
                let mut step = json!({});
                for key in [
                    "phase",
                    "action",
                    "purpose",
                    "outcome",
                    "validation_codes",
                    "checks",
                    "evidence",
                    "close_after_action",
                    "close_evidence",
                ] {
                    if let Some(v) = s.get(key) {
                        step[key] = limited(v, 3);
                    }
                }
                step
            })
            .collect();
    }
    if result["combat_input_result"]["analysis"]["action"].is_string() {
        out["combat_action"] = result["combat_input_result"]["analysis"]["action"].clone();
    }
    out["processing_error"] = result.get("error").is_some().into();
    out
}

enum Message {
    Record(PathBuf, Value),
    Flush(tokio::sync::oneshot::Sender<()>),
}
pub(super) struct Recorder {
    sender: Option<SyncSender<Message>>,
    // Exactly one entry per registered session, cleared on close/shutdown.
    transitions: Mutex<HashMap<String, Option<(Value, Value)>>>,
}
impl Recorder {
    pub fn new(enabled: bool) -> Self {
        if !enabled {
            return Self {
                sender: None,
                transitions: Mutex::new(HashMap::new()),
            };
        }
        let (sender, receiver) = mpsc::sync_channel::<Message>(2048);
        // One writer per runtime. No disk I/O or model call under the session lock.
        if let Err(error) = std::thread::Builder::new()
            .name("workshop-records".into())
            .spawn(move || {
                while let Ok(message) = receiver.recv() {
                    match message {
                        Message::Flush(done) => {
                            let _ = done.send(());
                        }
                        Message::Record(path, row) => {
                            let saved = locked_file(&path)
                                .and_then(|mut file| append_line(&mut file, &row));
                            if let Err(error) = saved {
                                tracing::warn!(event="workshop_record_failed",%error);
                            }
                        }
                    }
                }
            })
        {
            tracing::warn!(event="workshop_record_failed",%error);
            return Self {
                sender: None,
                transitions: Mutex::new(HashMap::new()),
            };
        }
        Self {
            sender: Some(sender),
            transitions: Mutex::new(HashMap::new()),
        }
    }
    pub fn register(&self, sid: &str) {
        if self.sender.is_some() {
            self.transitions.lock().unwrap().insert(sid.into(), None);
        }
    }
    pub fn forget(&self, sid: &str) {
        self.transitions.lock().unwrap().remove(sid);
    }
    pub fn forget_all(&self) {
        self.transitions.lock().unwrap().clear();
    }
    fn admit_transition(&self, sid: &str, before: &Value, after: &Value) -> bool {
        let mut transitions = self.transitions.lock().unwrap();
        let Some(last) = transitions.get_mut(sid) else {
            return true;
        };
        if last
            .as_ref()
            .is_some_and(|(b, a)| b == before && a == after)
        {
            return false;
        }
        *last = Some((before.clone(), after.clone()));
        true
    }
    fn append(&self, path: PathBuf, row: Value) {
        if let Some(sender) = &self.sender
            && let Err(error) = sender.try_send(Message::Record(path, row))
        {
            tracing::warn!(event="workshop_record_failed",reason="writer_unavailable",%error);
        }
    }
    pub async fn flush(&self) {
        if let Some(sender) = self.sender.clone() {
            let (tx, rx) = tokio::sync::oneshot::channel();
            let _ = tokio::task::spawn_blocking(move || sender.send(Message::Flush(tx))).await;
            let _ = rx.await;
        }
    }
}
impl Dialogue {
    pub(super) fn workshop_record_state(&self, sid: &str) -> Value {
        let d = self.data.lock().unwrap();
        state(d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref()))
    }
    pub(super) fn record_workshop(
        &self,
        sid: &str,
        kind: &str,
        before: &Value,
        after: &Value,
        input: &Input,
        result: &Value,
    ) {
        if !self.config.haiku.memory_enabled
            || input.private
            || result["web_private"] == true
            || result["workshop_record_private"] == true
            || !relevant(before, after)
            || result["deduplicated"] == true
        {
            return;
        }
        if sid.is_empty()
            || !sid
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        {
            tracing::warn!(
                event = "workshop_record_failed",
                reason = "invalid_session_id"
            );
            return;
        }
        if kind == "lifecycle" && !self.workshop_records.admit_transition(sid, before, after) {
            return;
        }
        let identity = if before.is_object() { before } else { after };
        let result = result_fields(result);
        let row = json!({"schema_version":"2","id":id("hwturn"),"created_at":chrono::Utc::now(),
            "session_id":sid,"entry_id":identity["entry_id"],"workshop_id":identity["workshop_id"],
            "event_kind":kind,"turn_id":result["turn_id"],"epoch":input.epoch,"player_text":input.raw.chars().take(1000).collect::<String>(),
            "semantic_player_text":input.semantic.as_ref().map(|s|s.chars().take(1000).collect::<String>()),
            "base_verse":before["canonical"],"canonical_after":after["canonical"],
            "pending_before":before["pending"],"pending_after":after["pending"],
            "state_before":before,"state_after":after,"steps":result["steps"],"result":result,
            "result_scope":"runtime_observation"});
        self.workshop_records.append(
            self.config
                .haiku
                .memory_dir
                .join("sessions")
                .join(sid)
                .join("long_term/haiku_workshop_turns.jsonl"),
            row,
        );
    }
    pub(super) fn record_workshop_result(
        &self,
        sid: &str,
        attempt: &Attempt,
        after: &Value,
        result: &Value,
    ) {
        if attempt.finished.swap(true, Ordering::Relaxed) {
            return;
        }
        self.record_workshop(
            sid,
            "turn_result",
            &attempt.before,
            after,
            &attempt.input,
            result,
        );
    }
    pub(super) fn record_workshop_turn(&self, sid: &str, turn: &str, attempt: &Attempt) {
        let (after, row) = {
            let d = self.data.lock().unwrap();
            let found = d
                .rows
                .iter()
                .find(|r| r["turn_id"] == turn && r["session_id"] == sid);
            let row = match found {
                Some(row) if row["epoch"].as_u64() == attempt.input.epoch => row.clone(),
                Some(_) => json!({"turn_id":turn,"epoch":attempt.input.epoch,
                    "playback_status":"cancelled","reason":"superseded_attempt"}),
                None => {
                    json!({"turn_id":turn,"epoch":attempt.input.epoch,"reason":"row_unavailable"})
                }
            };
            (
                state(d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref())),
                row,
            )
        };
        self.record_workshop_result(sid, attempt, &after, &row);
    }
}

#[cfg(test)]
pub(super) mod tests;
