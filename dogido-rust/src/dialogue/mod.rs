//! 平時の一往復用。戦闘・川柳・世界操作の代替にはしない。
mod audio;
mod bridge;
mod history;
mod sentences;

use crate::{
    events::GameEvent,
    ingress::{Admission, SequenceLedger},
    llm::RigLlm,
    planner::repair::Repair,
};
use anyhow::Result;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, VecDeque},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::{Semaphore, watch};

#[derive(Clone)]
pub struct DialogueConfig {
    pub python: PathBuf,
    pub helper: PathBuf,
    pub model: String,
    pub base_url: String,
    pub max_tokens: u64,
    pub timeout_ms: u64,
    pub reading_engine: String,
    pub voicevox_url: String,
    pub speaker: u32,
    pub speed: f64,
    pub pitch: f64,
    pub volume: f64,
    pub audio_dir: PathBuf,
    pub player: PathBuf,
    pub audio_enabled: bool,
}
impl Default for DialogueConfig {
    fn default() -> Self {
        Self {
            python: "python3".into(),
            helper: PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/dialogue_helper.py"),
            model: "default_model".into(),
            base_url: "http://127.0.0.1:8080/v1".into(),
            max_tokens: 72,
            timeout_ms: 20_000,
            reading_engine: "auto".into(),
            voicevox_url: "http://127.0.0.1:50021".into(),
            speaker: 21,
            speed: 0.88,
            pitch: 0.0,
            volume: 1.0,
            audio_dir: PathBuf::from(".dogido_tmp/rust-dialogue"),
            player: "/usr/bin/afplay".into(),
            audio_enabled: true,
        }
    }
}
struct Session {
    name: String,
    preview: bool,
    latest: Option<GameEvent>,
    received: Option<Instant>,
    sequences: SequenceLedger,
    history: history::History,
    epoch: u64,
    current_turn: String,
    status: String,
    cancel: Option<watch::Sender<bool>>,
}
#[derive(Default)]
struct Data {
    sessions: HashMap<String, Session>,
    rows: VecDeque<Value>,
    revision: u64,
    stopped: bool,
}
pub struct Dialogue {
    config: DialogueConfig,
    llm: RigLlm,
    audio: audio::Audio,
    data: Mutex<Data>,
    serial: Semaphore,
    jobs: Mutex<Vec<tokio::task::JoinHandle<()>>>,
}
fn id(prefix: &str) -> String {
    format!("{prefix}_{}", uuid::Uuid::new_v4().simple())
}
fn danger(event: &GameEvent) -> bool {
    !event.visual_threats.is_empty()
        || !event.auditory_threats.is_empty()
        || event.combat.combat_active_hint == Some(true)
        || event.combat.recent_damage_ms.is_some_and(|ms| ms < 8000)
        || event
            .world
            .danger_darkness_score
            .is_some_and(|score| score >= 0.72)
}
fn recent_observation(event: &GameEvent) -> bool {
    match &event.observed_at {
        crate::events::EventTime::Aware(at) => {
            (chrono::Utc::now() - at.with_timezone(&chrono::Utc))
                .num_milliseconds()
                .abs()
                <= 10_000
        }
        crate::events::EventTime::Naive(_) => false,
    }
}
fn fresh(session: &Session) -> bool {
    session.preview
        || (session
            .received
            .is_some_and(|at| at.elapsed() <= Duration::from_secs(10))
            && session
                .latest
                .as_ref()
                .is_some_and(|e| recent_observation(e) && !danger(e)))
}
fn empty_event(name: &str) -> Value {
    json!({"schema_version":"2026-05-24","adapter":"rust-conversation-preview","observed_at":chrono::Utc::now(),
    "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"name":name}})
}

impl Dialogue {
    pub fn new(config: DialogueConfig) -> Result<Arc<Self>> {
        anyhow::ensure!(config.helper.is_file(), "dialogue helper missing");
        anyhow::ensure!(
            config.max_tokens > 0 && config.max_tokens <= 512,
            "invalid reply token budget"
        );
        anyhow::ensure!(
            config.speed.is_finite()
                && config.speed > 0.0
                && config.volume.is_finite()
                && config.volume >= 0.0
                && config.pitch.is_finite(),
            "invalid voice settings"
        );
        let key = std::env::var("DOGIDO_LLM_API_KEY").ok();
        Ok(Arc::new(Self {
            llm: RigLlm::new(
                &config.base_url,
                key.as_deref(),
                Duration::from_millis(config.timeout_ms),
            )?,
            audio: audio::Audio::new()?,
            config,
            data: Mutex::new(Data::default()),
            serial: Semaphore::new(1),
            jobs: Mutex::new(vec![]),
        }))
    }
    pub fn register(&self, session_id: &str, name: &str, preview: bool) {
        let mut d = self.data.lock().unwrap();
        d.sessions.insert(
            session_id.into(),
            Session {
                name: name.into(),
                preview,
                latest: None,
                received: None,
                sequences: SequenceLedger::default(),
                history: history::History::default(),
                epoch: 0,
                current_turn: String::new(),
                status: "ready".into(),
                cancel: None,
            },
        );
        d.revision += 1;
    }
    pub fn close(&self, session_id: &str) {
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.remove(session_id)
            && let Some(c) = s.cancel
        {
            let _ = c.send(true);
        }
        d.revision += 1;
    }
    pub fn only_session(&self) -> Option<String> {
        let d = self.data.lock().unwrap();
        if d.sessions.len() == 1 {
            d.sessions.keys().next().cloned()
        } else {
            None
        }
    }
    pub fn observe(
        self: &Arc<Self>,
        session_id: &str,
        event: GameEvent,
        key: Option<&str>,
    ) -> Value {
        let sequence = event.sequence;
        let recent = recent_observation(&event);
        let text = event.meta.user_text.clone().unwrap_or_default();
        let mut d = self.data.lock().unwrap();
        let Some(s) = d.sessions.get_mut(session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        let duplicate = s.sequences.admit(sequence, key) != Admission::New;
        if !duplicate {
            if danger(&event) {
                s.epoch += 1;
                if let Some(c) = s.cancel.take() {
                    let _ = c.send(true);
                }
                s.status = "危険中の会話は試験対象外".into();
            }
            s.latest = Some(event);
            s.received = recent.then(Instant::now);
        }
        drop(d);
        let input = if !duplicate && !text.trim().is_empty() {
            self.submit(Some(session_id), &text, "text")
        } else {
            Value::Null
        };
        json!({"accepted":true,"event_id":id("evt"),"session_id":session_id,"sequence":sequence,"deduplicated":duplicate,
            "state":null,"outputs":null,"commands":[],"acknowledged_command_ids":[],"server_time":chrono::Utc::now(),"phase":"dialogue_preview","player_input":input})
    }
    pub fn submit(self: &Arc<Self>, selected: Option<&str>, text: &str, source: &str) -> Value {
        if text.trim().is_empty() {
            return json!({"accepted":false,"reason":"empty_text"});
        }
        if text.chars().count() > 1000 {
            return json!({"accepted":false,"reason":"text_too_long"});
        }
        let session_id = selected.map(str::to_owned).or_else(|| self.only_session());
        let Some(session_id) = session_id else {
            return json!({"accepted":false,"reason":"select_one_session"});
        };
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        if jobs.len() >= 16 {
            return json!({"accepted":false,"reason":"input_queue_full"});
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get_mut(&session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        if !fresh(s) {
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        let active_turn = s.current_turn.clone();
        // 同じ進行中入力の二重配送を、割り込みやLLM再呼出しにしない。
        if let Some(last) = d
            .rows
            .iter()
            .rev()
            .find(|r| r["turn_id"] == active_turn && r["player_input_text"] == text)
            && matches!(
                last["playback_status"].as_str(),
                Some("generating" | "queued" | "started")
            )
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":last["turn_id"]});
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if let Some(c) = s.cancel.take() {
            let _ = c.send(true);
        }
        s.epoch += 1;
        let epoch = s.epoch;
        let turn = id("turn");
        s.current_turn = turn.clone();
        s.status = "generating".into();
        let event = s
            .latest
            .as_ref()
            .map(|e| serde_json::to_value(e).unwrap())
            .unwrap_or_else(|| empty_event(&s.name));
        let input = json!({"model":self.config.model,"max_tokens":self.config.max_tokens,"reading_engine":self.config.reading_engine,
            "text":text,"history":s.history.rows(),"conversation_history":s.history.lines(),"event":event});
        s.history.push(&turn, "user", text);
        let (cancel, rx) = watch::channel(false);
        s.cancel = Some(cancel);
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":session_id,"category":"speech","text":"",
            "created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"both","player_input_text":text,"source":source,"playback_status":"generating"}));
        d.revision += 1;
        drop(d);
        tracing::info!(
            event = "dialogue_input",
            session_id,
            turn_id = turn,
            text,
            source
        );
        let this = self.clone();
        let sid = session_id.clone();
        let tid = turn.clone();
        let job = tokio::spawn(async move {
            this.run_turn(sid, tid, epoch, input, rx).await;
        });
        jobs.push(job);
        json!({"accepted":true,"session_id":session_id,"turn_id":turn})
    }
    pub fn interrupt(&self, session_id: &str) {
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d.sessions.get_mut(session_id) {
            s.epoch += 1;
            s.status = "cancelled".into();
            if let Some(c) = s.cancel.take() {
                let _ = c.send(true);
            }
        }
        d.revision += 1;
    }
    fn update(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        status: &str,
        result: Option<&Value>,
    ) -> bool {
        let mut d = self.data.lock().unwrap();
        let current = !d.stopped && d.sessions.get(sid).is_some_and(|s| s.epoch == epoch);
        let status = if !current { "cancelled" } else { status };
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
            if let Some(result) = result {
                if let Some(text) = result.get("text") {
                    row["text"] = text.clone();
                }
                if let Some(error) = result.get("error") {
                    row["error"] = error.clone();
                }
                if let Some(reports) = result.get("llm_reports") {
                    row["llm_reports"] = reports.clone();
                }
            }
        }
        if current {
            let s = d.sessions.get_mut(sid).unwrap();
            s.status = status.into();
            if let Some(result) = result {
                if status == "queued"
                    && let Ok(repair) = serde_json::from_value::<Repair>(result["repair"].clone())
                {
                    s.history.annotate(turn, &repair);
                }
                if status == "completed" {
                    s.history
                        .push(turn, "assistant", result["text"].as_str().unwrap_or(""));
                }
            }
        }
        d.revision += 1;
        tracing::info!(
            event = "dialogue_status",
            session_id = sid,
            turn_id = turn,
            playback_status = status
        );
        current
    }
    async fn run_turn(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        input: Value,
        mut cancel: watch::Receiver<bool>,
    ) {
        let started = Instant::now();
        let permit = tokio::select! { _=bridge::cancelled(&mut cancel)=>{self.update(&sid,&turn,epoch,"cancelled",None);return;}, p=self.serial.acquire()=>p.unwrap() };
        // 観測更新が途切れた場合も、古い場所の返答・音声を続けない。
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let mut monitor_cancel = cancel.clone();
        let monitor = tokio::spawn(async move {
            loop {
                tokio::select! {
                    _=bridge::cancelled(&mut monitor_cancel)=>break,
                    _=tokio::time::sleep(Duration::from_millis(200))=>{
                        let stale={let d=owner.data.lock().unwrap();d.sessions.get(&monitor_sid).is_some_and(|s|s.epoch==epoch && !fresh(s))};
                        if stale {owner.interrupt(&monitor_sid);break;}
                    }
                }
            }
        });
        let result = bridge::render(&self.config, &self.llm, input, &mut cancel).await;
        match result {
            Ok(mut result) => {
                if let Some(unsupported) = result.get("unsupported").cloned() {
                    result["error"] = unsupported;
                    self.update(&sid, &turn, epoch, "unsupported", Some(&result));
                } else if result["text"].as_str().is_none_or(|s| s.is_empty()) {
                    self.update(&sid, &turn, epoch, "quiet", None);
                } else if self.update(&sid, &turn, epoch, "queued", Some(&result)) {
                    tracing::info!(
                        event = "dialogue_reply",
                        session_id = sid,
                        turn_id = turn,
                        text = result["text"].as_str().unwrap_or(""),
                        generation_ms = started.elapsed().as_millis() as u64
                    );
                    let playback = self
                        .audio
                        .speak(
                            &self.config,
                            result["spoken_text"].as_str().unwrap_or(""),
                            &mut cancel,
                            || {
                                self.update(&sid, &turn, epoch, "started", None);
                            },
                        )
                        .await;
                    let status = match playback {
                        Ok(()) => "completed",
                        Err(error) => {
                            result["error"] = error.to_string().into();
                            if *cancel.borrow() {
                                "cancelled"
                            } else {
                                "failed"
                            }
                        }
                    };
                    self.update(&sid, &turn, epoch, status, Some(&result));
                }
            }
            Err(error) => {
                self.update(
                    &sid,
                    &turn,
                    epoch,
                    if *cancel.borrow() {
                        "cancelled"
                    } else {
                        "failed"
                    },
                    Some(&json!({"error":error.to_string()})),
                );
            }
        }
        monitor.abort();
        let _ = monitor.await;
        drop(permit);
        tracing::info!(
            event = "dialogue_finished",
            session_id = sid,
            turn_id = turn,
            total_ms = started.elapsed().as_millis() as u64
        );
    }
    pub fn snapshot(&self, selected: Option<&str>) -> Value {
        let d = self.data.lock().unwrap();
        json!({"revision":d.revision,"phase":"dialogue_preview","audio_enabled":self.config.audio_enabled,
            "sessions":d.sessions.iter().map(|(id,s)|json!({"session_id":id,"name":s.name,"status":s.status,"observation_mode":if s.preview{"none"}else{"minecraft"},"history":s.history.rows()})).collect::<Vec<_>>(),
            "utterances":d.rows.iter().filter(|r|selected.is_none_or(|id|r["session_id"]==id)).collect::<Vec<_>>()})
    }
    pub fn cancel_all(&self) {
        {
            let mut d = self.data.lock().unwrap();
            d.stopped = true;
            for s in d.sessions.values_mut() {
                s.epoch += 1;
                if let Some(c) = s.cancel.take() {
                    let _ = c.send(true);
                }
            }
        }
    }
    pub async fn shutdown(&self) {
        self.cancel_all();
        let jobs = std::mem::take(&mut *self.jobs.lock().unwrap());
        for job in jobs {
            let _ = job.await;
        }
        tracing::info!(
            event = "dialogue_stopped",
            message = "会話補助・音声の終了を確認しました"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn late_playback_completion_cannot_commit_after_global_shutdown() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("test-session", "試験", true);
        {
            let mut data = dialogue.data.lock().unwrap();
            data.sessions.get_mut("test-session").unwrap().history.push(
                "test-turn",
                "user",
                "こんにちは",
            );
        }
        dialogue.cancel_all();
        // SIGINTとplayer成功終了が同時に届く場合を、完了通知を遅らせて再現。
        assert!(!dialogue.update(
            "test-session",
            "test-turn",
            0,
            "completed",
            Some(&json!({"text":"こんにちは。"}))
        ));
        let snapshot = dialogue.snapshot(None);
        assert_eq!(
            snapshot["sessions"][0]["history"].as_array().unwrap().len(),
            1
        );
        assert_eq!(snapshot["sessions"][0]["history"][0]["role"], "user");
        dialogue.shutdown().await;
    }
}
