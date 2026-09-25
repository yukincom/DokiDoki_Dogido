//! 平時会話と移植済み視認警告。全戦闘・川柳・世界操作の代替にはしない。
mod audio;
mod bridge;
mod history;
mod sentences;
mod warnings;

use crate::{
    events::{EventName, GameEvent, SourceKind},
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
    pub warnings: crate::threats::Settings,
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
            warnings: crate::threats::Settings::default(),
        }
    }
}
struct Session {
    name: String,
    preview: bool,
    // 全視認を含む観測とその受信時刻。部分通知で消去・延命しない。
    latest: Option<GameEvent>,
    received: Option<Instant>,
    partial_danger: bool,
    sequences: SequenceLedger,
    history: history::History,
    epoch: u64,
    current_turn: String,
    status: String,
    cancel: Option<watch::Sender<bool>>,
    threat_policy: crate::threats::Policy,
    warning: Option<warnings::Active>,
    pending_warning: Option<crate::threats::Warning>,
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
    clock: Instant,
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
// Fabricの音・ambient通知はvisual_threats=[]を送るが、視認消失の証拠ではない。
// 分類は送信側の観測範囲で決める。配列が空かどうかで推測しない。
fn complete_observation(event: &GameEvent) -> bool {
    match event.event.name {
        EventName::StatusSnapshot
        | EventName::ThreatApproaching
        | EventName::PlayerDied
        | EventName::HostileDefeated
        | EventName::CreeperDetonated
        | EventName::CombatEnded => true,
        // レガシーのthreat_detectedは視認と聴覚の両方を受け付ける。
        EventName::ThreatDetected => event.event.source_kind == SourceKind::Visual,
        EventName::HostileAudioDetected
        | EventName::AmbientMobDetected
        | EventName::DangerDarknessChanged
        | EventName::ResourceOptionFound
        | EventName::TimePhaseChanged => false,
    }
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
        || (!session.partial_danger
            && session
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
        config.warnings.validate()?;
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
            clock: Instant::now(),
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
                partial_danger: false,
                sequences: SequenceLedger::default(),
                history: history::History::default(),
                epoch: 0,
                current_turn: String::new(),
                status: "ready".into(),
                cancel: None,
                threat_policy: crate::threats::Policy::default(),
                warning: None,
                pending_warning: None,
            },
        );
        d.revision += 1;
    }
    pub fn close(&self, session_id: &str) {
        let mut d = self.data.lock().unwrap();
        Self::cancel_chat(&mut d, session_id, "session_closed");
        Self::cancel_warning(&mut d, session_id, "session_closed");
        d.sessions.remove(session_id);
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
        // submitと同じlock順序。shutdownが取りこぼす未登録jobを作らない。
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get_mut(session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        let duplicate = s.sequences.admit(sequence, key) != Admission::New;
        if !duplicate {
            if let Some(reason) = warnings::interruption_reason(&event) {
                Self::cancel_chat(&mut d, session_id, reason);
                d.sessions.get_mut(session_id).unwrap().status =
                    "危険を観測（戦闘中の会話は未移植）".into();
            }
            if complete_observation(&event) {
                let s = d.sessions.get(session_id).unwrap();
                let changed = s
                    .warning
                    .as_ref()
                    .is_some_and(|w| !recent || !w.plan.applicable(&event, &self.config.warnings));
                let relocated = if changed && recent {
                    s.warning.as_ref().and_then(|w| {
                        let cue_started = d
                            .rows
                            .iter()
                            .find(|r| r["turn_id"] == w.turn)
                            .is_some_and(|r| r.get("started_at").is_some());
                        w.plan.relocated(&event, &self.config.warnings, cue_started)
                    })
                } else {
                    None
                };
                if changed {
                    tracing::info!(event="warning_invalidated",session_id,
                    source_event=?event.event.name,visual_count=event.visual_threats.len(),recent);
                    Self::cancel_warning(
                        &mut d,
                        session_id,
                        if recent {
                            "target_changed_or_gone"
                        } else {
                            "stale_observation"
                        },
                    );
                }
                let s = d.sessions.get_mut(session_id).unwrap();
                if changed {
                    s.pending_warning = relocated;
                }
                if !recent {
                    s.pending_warning = None;
                } else if let Some(pending) = s.pending_warning.take() {
                    // 配送枠を待つ間にさらに向きが変わっても、未完了の本文を失わない。
                    // 既に開始したcueは最初のrelocatedで除いている。
                    s.pending_warning = pending.relocated(&event, &self.config.warnings, false);
                }
                let plan = if recent && jobs.len() < 16 {
                    let new_plan = s.threat_policy.observe(
                        &event,
                        self.clock.elapsed().as_millis() as u64,
                        s.warning.is_some(),
                        &self.config.warnings,
                    );
                    new_plan.or_else(|| s.pending_warning.take())
                } else {
                    None
                };
                s.partial_danger = false;
                s.latest = Some(event);
                s.received = recent.then(Instant::now);
                if let Some(plan) = plan {
                    d.sessions.get_mut(session_id).unwrap().pending_warning = None;
                    Self::cancel_warning(&mut d, session_id, "higher_priority_warning");
                    let (turn, rx) = Self::queue_warning(&mut d, session_id, &plan);
                    let this = self.clone();
                    let sid = session_id.to_owned();
                    jobs.push(tokio::spawn(async move {
                        this.run_warning(sid, turn, plan, rx).await;
                    }));
                }
            } else {
                // 部分通知は危険を加えることだけできる。安全な全観測まで会話を再開しない。
                d.sessions.get_mut(session_id).unwrap().partial_danger |= danger(&event);
            }
            d.revision += 1;
        }
        drop(d);
        drop(jobs);
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
        if !fresh(s) || s.warning.is_some() {
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
        Self::cancel_chat(&mut d, &session_id, "new_player_input");
        let s = d.sessions.get_mut(&session_id).unwrap();
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
        Self::cancel_chat(&mut d, session_id, "manual_interrupt");
        Self::cancel_warning(&mut d, session_id, "manual_interrupt");
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
            if matches!(
                status,
                "completed" | "cancelled" | "failed" | "unsupported" | "quiet"
            ) {
                s.cancel = None;
            }
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
                        let mut d=owner.data.lock().unwrap();
                        let stale=d.sessions.get(&monitor_sid).is_some_and(|s|s.epoch==epoch && !fresh(s));
                        if stale {Self::cancel_chat(&mut d,&monitor_sid,"stale_observation");break;}
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
            let ids = d.sessions.keys().cloned().collect::<Vec<_>>();
            for sid in ids {
                Self::cancel_chat(&mut d, &sid, "server_shutdown");
                Self::cancel_warning(&mut d, &sid, "server_shutdown");
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
    async fn pending_group_uses_latest_composition_after_queue_saturation() {
        let dialogue = Dialogue::new(DialogueConfig {
            audio_enabled: false,
            ..DialogueConfig::default()
        })
        .unwrap();
        dialogue.register("s", "試験", false);
        let event = |sequence, types: &[&str]| {
            GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture",
                "observed_at":chrono::Utc::now(),"sequence":sequence,
                "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                "visual_threats":types.iter().enumerate().map(|(i,t)| json!({"type":t,"entity_id":format!("{t}{i}"),
                    "distance":8.2,"direction":{"horizontal":"front"}})).collect::<Vec<_>>()
            })).unwrap()
        };
        {
            let mut d = dialogue.data.lock().unwrap();
            let plan = d
                .sessions
                .get_mut("s")
                .unwrap()
                .threat_policy
                .observe(
                    &event(1, &["zombie"; 3]),
                    0,
                    false,
                    &dialogue.config.warnings,
                )
                .unwrap();
            Dialogue::queue_warning(&mut d, "s", &plan);
            d.rows.back_mut().unwrap()["started_at"] = chrono::Utc::now().to_rfc3339().into();
        }
        for _ in 0..16 {
            dialogue
                .jobs
                .lock()
                .unwrap()
                .push(tokio::spawn(std::future::pending()));
        }
        dialogue.observe("s", event(2, &["zombie"; 2]), None);
        dialogue.observe("s", event(3, &["zombie", "skeleton"]), None);
        let received = dialogue.data.lock().unwrap().sessions["s"].received;
        let mut partial = empty_event("試験");
        partial["sequence"] = 4.into();
        partial["event"]["name"] = "hostile_audio_detected".into();
        partial["event"]["source_kind"] = "auditory".into();
        partial["auditory_threats"] = json!([{"label":"zombie"}]);
        dialogue.observe("s", GameEvent::parse(partial).unwrap(), None);
        {
            let d = dialogue.data.lock().unwrap();
            assert_eq!(d.sessions["s"].received, received);
            assert!(d.sessions["s"].partial_danger);
            let pending = d.sessions["s"].pending_warning.as_ref().unwrap();
            assert_eq!(pending.text, "スケルトン1体、ゾンビ1体おるで。");
            assert!(pending.cue.is_none());
        }
        let jobs = std::mem::take(&mut *dialogue.jobs.lock().unwrap());
        for job in jobs {
            job.abort();
            let _ = job.await;
        }
        dialogue.observe("s", event(5, &["zombie"; 2]), None);
        let view = dialogue.snapshot(None);
        let rows = view["utterances"].as_array().unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["playback_status"], "cancelled");
        assert_eq!(rows[1]["text"], "ゾンビ2体おるで。");
        dialogue.shutdown().await;
    }

    #[tokio::test]
    async fn pending_warning_tracks_repeated_direction_changes_while_jobs_are_full() {
        for (kind, fuse) in [("zombie", false), ("creeper", true)] {
            let dialogue = Dialogue::new(DialogueConfig {
                audio_enabled: false,
                ..DialogueConfig::default()
            })
            .unwrap();
            dialogue.register("s", "試験", false);
            let event = |sequence, direction| {
                GameEvent::parse(json!({
                "schema_version":"2026-05-24","adapter":"fixture","observed_at":chrono::Utc::now(),"sequence":sequence,
                "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                "visual_threats":[{"type":kind,"entity_id":"z1","distance":8.2,"direction":{"horizontal":direction},"fuse_active":fuse}]
            })).unwrap()
            };
            {
                let mut d = dialogue.data.lock().unwrap();
                let plan = d
                    .sessions
                    .get_mut("s")
                    .unwrap()
                    .threat_policy
                    .observe(&event(1, "front"), 0, false, &dialogue.config.warnings)
                    .unwrap();
                Dialogue::queue_warning(&mut d, "s", &plan);
            }
            // 実モデル・playerを使わず、配送枠だけ埋める。
            for _ in 0..16 {
                dialogue
                    .jobs
                    .lock()
                    .unwrap()
                    .push(tokio::spawn(std::future::pending()));
            }
            dialogue.observe("s", event(2, "right"), None);
            dialogue.observe("s", event(3, "left"), None);
            {
                let d = dialogue.data.lock().unwrap();
                let pending = d.sessions["s"].pending_warning.as_ref().unwrap();
                assert_eq!(
                    pending.horizontal,
                    Some(crate::events::HorizontalDirection::Left)
                );
                assert!(pending.text.contains("左"));
            }
            let jobs = std::mem::take(&mut *dialogue.jobs.lock().unwrap());
            for job in jobs {
                job.abort();
                let _ = job.await;
            }
            dialogue.observe("s", event(4, "back_left"), None);
            let view = dialogue.snapshot(None);
            let rows = view["utterances"].as_array().unwrap();
            assert_eq!(rows.len(), 2);
            assert_eq!(rows[0]["playback_status"], "cancelled");
            assert_eq!(rows[1]["warning"]["horizontal"], "back_left");
            assert!(rows[1]["text"].as_str().unwrap().contains("左後ろ"));
            dialogue.shutdown().await;
        }
    }

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
