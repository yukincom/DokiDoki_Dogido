//! 通常会話、冒険中の判断、限定操作と自動川柳の配送。
mod address_runtime;
mod web_adapter;
mod web_runtime;
pub use web_runtime::Settings as WebSettings;
mod assist_runtime;
mod audio;
mod bridge;
mod chat_context;
mod chat_leaf_runtime;
mod chat_runtime;
mod combat_runtime;
mod environment_runtime;
mod episode_runtime;
mod foreground_runtime;
mod haiku_runtime;
mod history;
mod knowledge_display;
mod knowledge_queue;
mod language_runtime;
mod memory_runtime;
mod monitor;
mod poem_runtime;
mod reaction_runtime;
mod reading_runtime;
mod tts_runtime;
pub use haiku_runtime::Settings as HaikuSettings;
mod combat_classifier;
mod sentences;
mod voice_input_runtime;
mod warnings;
mod workshop_combat_input;
mod workshop_combat_runtime;
mod workshop_edits;
mod workshop_focus;
mod workshop_record;
mod workshop_runtime;
mod workshop_text;

use crate::{
    events::{EventName, GameEvent, SourceKind},
    ingress::{Admission, SequenceLedger},
    llm::RigLlm,
    planner::repair::Repair,
    playback::Status as PlaybackStatus,
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
    pub haiku_speed: f64,
    pub pitch: f64,
    pub volume: f64,
    pub output_sampling_rate: Option<u32>,
    pub audio_dir: PathBuf,
    pub player: PathBuf,
    pub audio_enabled: bool,
    pub llm_enabled: bool,
    pub language_enabled: bool,
    pub web: WebSettings,
    pub warnings: crate::threats::Settings,
    pub combat: crate::combat::model::Settings,
    pub haiku: HaikuSettings,
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
            haiku_speed: 0.80,
            pitch: 0.0,
            volume: 1.0,
            output_sampling_rate: None,
            audio_dir: PathBuf::from(".dogido_tmp/rust-dialogue"),
            player: "/usr/bin/afplay".into(),
            audio_enabled: true,
            llm_enabled: true,
            language_enabled: true,
            web: WebSettings::default(),
            warnings: crate::threats::Settings::default(),
            combat: crate::combat::model::Settings::default(),
            haiku: HaikuSettings::default(),
        }
    }
}
struct Session {
    name: String,
    preview: bool,
    text_workshop: bool,
    text_reply: Option<(String, u64, Value)>,
    text_last_request: Option<Value>,
    // 全視認を含む観測とその受信時刻。部分通知で消去・延命しない。
    latest: Option<GameEvent>,
    received: Option<Instant>,
    audio_latest: Option<GameEvent>,
    audio_received: Option<Instant>,
    chat_allowed: bool,
    mode: crate::combat::model::Mode,
    sequences: SequenceLedger,
    history: history::History,
    chat_observation: crate::chat_observation::ChatObservationMemory,
    haiku_context: crate::haiku::preparation::RuntimeSnapshot,
    pending_vocalization: Option<voice_input_runtime::Pending>,
    haiku: haiku_runtime::State,
    combat_digest: VecDeque<String>,
    stable_threat: crate::workshop_combat_input::StableThreat,
    combat_input: Option<workshop_combat_input::Pending>,
    epoch: u64,
    current_turn: String,
    status: PlaybackStatus,
    cancel: Option<watch::Sender<bool>>,
    combat: crate::combat::core::Engine,
    assist: crate::assist::AssistState,
    assist_pending: Option<assist_runtime::Pending>,
    danger: crate::environment::danger::Danger,
    ambient: crate::environment::ambient::Ambient,
    environment_latest: Option<GameEvent>,
    last_player_input: Option<u64>,
    foreground: crate::foreground::State,
    language: crate::language::State,
    web: web_runtime::State,
    knowledge_queue: VecDeque<knowledge_queue::Pending>,
    knowledge_checked: Option<knowledge_queue::Checked>,
    address: Option<crate::address::Pending>,
    address_checked: Option<address_runtime::Checked>,
    last_completed_conversation: Option<u64>,
    input_generation: u64,
    // Privacy belongs to the admitted generation, including its internal forwards.
    record_private_generation: Option<(u64, bool)>,
    light_cancel: Option<watch::Sender<bool>>,
    deferred_input: Option<environment_runtime::DeferredInput>,
    warning: Option<warnings::Active>,
    pending_warning: Option<Vec<crate::combat::model::Speech>>,
    pending_input: Option<String>,
}
#[derive(Default)]
struct Data {
    sessions: HashMap<String, Session>,
    rows: VecDeque<Value>,
    revision: u64,
    stopped: bool,
    text_prompts: Option<Value>,
    text_prompt_settings: Option<Value>,
    text_prompt_version: u64,
}
pub struct Dialogue {
    config: DialogueConfig,
    workshop_records: workshop_record::Recorder,
    llm: RigLlm,
    audio: audio::Audio,
    haiku_routes: haiku_runtime::Routes,
    combat_classifier: combat_classifier::Classifier,
    episodes: Option<Arc<crate::episode_log::Recorder>>,
    data: Mutex<Data>,
    serial: Semaphore,
    jobs: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    clock: Instant,
}
fn id(prefix: &str) -> String {
    format!("{prefix}_{}", uuid::Uuid::new_v4().simple())
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
fn observation_fresh(session: &Session) -> bool {
    session.preview
        || session
            .received
            .is_some_and(|at| at.elapsed() <= Duration::from_secs(10))
            && session.latest.as_ref().is_some_and(recent_observation)
}
fn fresh(session: &Session) -> bool {
    observation_fresh(session) && session.chat_allowed
}
fn empty_event(name: &str) -> Value {
    json!({"schema_version":"2026-05-24","adapter":"rust-conversation-preview","observed_at":chrono::Utc::now(),
    "event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},"player":{"name":name}})
}

impl Dialogue {
    pub fn llm_enabled(&self) -> bool {
        self.config.llm_enabled
    }

    pub fn new(mut config: DialogueConfig) -> Result<Arc<Self>> {
        config.warnings.validate()?;
        config.combat = crate::combat::model::Settings::merged(&config.combat.0)?;
        anyhow::ensure!(config.helper.is_file(), "dialogue helper missing");
        anyhow::ensure!(
            config.max_tokens > 0 && config.max_tokens <= 512,
            "invalid reply token budget"
        );
        anyhow::ensure!(
            config.speed.is_finite()
                && config.speed > 0.0
                && config.haiku_speed.is_finite()
                && config.haiku_speed > 0.0
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
            )?
            .with_enabled(config.llm_enabled),
            audio: audio::Audio::new()?,
            haiku_routes: haiku_runtime::Routes::new(&config)?,
            combat_classifier: combat_classifier::Classifier::default(),
            episodes: if config.haiku.memory_enabled {
                match crate::episode_log::Recorder::new(config.haiku.memory_dir.clone()) {
                    Ok(recorder) => Some(Arc::new(recorder)),
                    Err(error) => {
                        tracing::warn!(event="episode_writer_start_failed", %error);
                        None
                    }
                }
            } else {
                None
            },
            workshop_records: workshop_record::Recorder::new(config.haiku.memory_enabled),
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
                text_workshop: false,
                text_reply: None,
                text_last_request: None,
                latest: None,
                received: None,
                audio_latest: None,
                audio_received: None,
                chat_allowed: true,
                mode: crate::combat::model::Mode::Normal,
                sequences: SequenceLedger::default(),
                history: history::History::default(),
                chat_observation: chat_context::memory(&self.config.combat),
                haiku_context: crate::haiku::preparation::RuntimeSnapshot::default(),
                pending_vocalization: None,
                haiku: haiku_runtime::State::default(),
                combat_digest: VecDeque::new(),
                stable_threat: crate::workshop_combat_input::StableThreat::default(),
                combat_input: None,
                epoch: 0,
                current_turn: String::new(),
                status: PlaybackStatus::Ready,
                cancel: None,
                combat: crate::combat::core::Engine::default(),
                assist: crate::assist::AssistState::new([]),
                assist_pending: None,
                danger: crate::environment::danger::Danger::default(),
                ambient: crate::environment::ambient::Ambient::default(),
                environment_latest: None,
                last_player_input: None,
                foreground: crate::foreground::State::default(),
                language: crate::language::State::default(),
                web: web_runtime::State::default(),
                knowledge_queue: VecDeque::new(),
                knowledge_checked: None,
                address: None,
                address_checked: None,
                last_completed_conversation: None,
                input_generation: 0,
                record_private_generation: None,
                light_cancel: None,
                deferred_input: None,
                warning: None,
                pending_warning: None,
                pending_input: None,
            },
        );
        d.revision += 1;
        self.workshop_records.register(session_id);
    }
    pub fn close(&self, session_id: &str) {
        let before = self.workshop_record_state(session_id);
        let mut d = self.data.lock().unwrap();
        self.cancel_knowledge_queue(&mut d, session_id, "session_closed");
        Self::cancel_haiku(&mut d, session_id, "session_closed");
        Self::cancel_chat(&mut d, session_id, "session_closed");
        Self::cancel_combat_input(&mut d, session_id);
        Self::cancel_warning(&mut d, session_id, "session_closed");
        Self::cancel_assist(&mut d, session_id);
        Self::cancel_light(&mut d, session_id);
        d.sessions.remove(session_id);
        d.revision += 1;
        drop(d);
        self.record_workshop(
            session_id,
            "lifecycle",
            &before,
            &Value::Null,
            &workshop_record::Input::default(),
            &json!({"reason":"session_closed"}),
        );
        self.workshop_records.forget(session_id);
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
        let before = self.workshop_record_state(session_id);
        let text = event.meta.user_text.clone().unwrap_or_default();
        let private = self
            .data
            .lock()
            .unwrap()
            .sessions
            .get(session_id)
            .is_some_and(|s| s.web.state.research.is_some());
        let mut result = self.observe_inner(session_id, event, key, private);
        let direct_input = result
            .as_object_mut()
            .and_then(|r| r.remove("_workshop_direct_input"))
            == Some(Value::Bool(true));
        let after = self.workshop_record_state(session_id);
        if result["deduplicated"] != true {
            if direct_input && !text.is_empty() {
                self.record_workshop(
                    session_id,
                    "input_admission",
                    &before,
                    &after,
                    &workshop_record::Input {
                        raw: text.clone(),
                        private,
                        ..Default::default()
                    },
                    &result["player_input"],
                );
            } else if before != after {
                self.record_workshop(
                    session_id,
                    "lifecycle",
                    &before,
                    &after,
                    &workshop_record::Input::default(),
                    &json!({"reason":"game_event"}),
                );
            }
        }
        result
    }
    fn observe_inner(
        self: &Arc<Self>,
        session_id: &str,
        event: GameEvent,
        key: Option<&str>,
        private: bool,
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
        let results = s.assist.observe_results(
            &event.command_results,
            chrono::Utc::now(),
            s.warning.is_some() || s.pending_warning.is_some() || s.cancel.is_some(),
        );
        for observed in &results.observed {
            tracing::info!(event="assist_result",session_id=session_id,result=%json!(observed));
        }
        let episode_before = (!duplicate && self.episodes.is_some())
            .then(|| episode_runtime::Before::capture(&d, session_id));
        let mut input_handled = false;
        let mut input_generation = None;
        if !duplicate {
            let now = self.clock.elapsed().as_millis() as u64;
            let complete = complete_observation(&event);
            let s = d.sessions.get_mut(session_id).unwrap();
            if complete {
                s.latest = Some(event.clone());
                s.received = recent.then(Instant::now);
            }
            if complete && recent {
                s.foreground
                    .set_game_paused(now, event.world.game_paused.unwrap_or(false));
            }
            if complete || recent && event.event.source_kind == SourceKind::Auditory {
                s.audio_latest = Some(event.clone());
                s.audio_received = recent.then(Instant::now);
            }
            if complete {
                if recent {
                    s.stable_threat.observe(
                        &event,
                        now,
                        self.config.warnings.recent_damage_window_ms,
                    );
                } else {
                    s.stable_threat.reset();
                }
            } else if recent && warnings::interruption_reason(&event).is_some() {
                s.stable_threat.reset();
            }
            if recent {
                s.environment_latest = Some(environment_runtime::context(s, &event, complete));
                let (boss, ominous) = s.combat.environmental_presence(now, &self.config.combat);
                s.danger.set_presence(boss, ominous);
                s.danger.update(&event, now, complete, &self.config.combat);
                s.ambient.update(&event, now, complete, &self.config.combat);
                if complete {
                    chat_context::update_haiku(s, &event, &self.config.combat);
                }
                s.combat.set_dark_push_context(
                    s.danger.dark_push_active(),
                    s.warning
                        .as_ref()
                        .is_some_and(|w| w.actions.iter().any(environment_runtime::dark_audio))
                        || s.pending_warning
                            .as_ref()
                            .is_some_and(|a| a.iter().any(environment_runtime::dark_audio)),
                );
            }
            if recent
                && warnings::interruption_reason(&event).is_some()
                && !workshop_focus::owns_input(&d.sessions[session_id])
            {
                Self::cancel_haiku(&mut d, session_id, "current_threat");
                Self::cancel_address(&mut d, session_id, "attention_interrupted");
            }
            if recent && !text.trim().is_empty() {
                Self::cancel_combat_input(&mut d, session_id);
                Self::cancel_haiku(&mut d, session_id, "new_player_input");
                Self::cancel_assist(&mut d, session_id);
                Self::cancel_light(&mut d, session_id);
                let s = d.sessions.get_mut(session_id).unwrap();
                s.input_generation = s.input_generation.wrapping_add(1);
                input_generation = Some(s.input_generation);
                s.record_private_generation = Some((s.input_generation, private));
                s.last_player_input = Some(now);
                s.ambient.note_player_input(now);
            }
            self.tick_workshop(&mut d, session_id);
            self.tick_foreground(&mut d, session_id);
            self.refresh_combat_audio(&mut d, session_id);
            if recent {
                let s = d.sessions.get_mut(session_id).unwrap();
                let busy = s.warning.is_some() || s.pending_warning.is_some() || jobs.len() >= 16;
                let workshop_event = (!text.trim().is_empty() && workshop_focus::quiet(s))
                    .then(|| event.without_player_input());
                let decision = s.combat.observe(
                    workshop_event.as_ref().unwrap_or(&event),
                    now,
                    complete,
                    busy,
                    &self.config.combat,
                    &self.config.warnings,
                );
                let names = s.combat.take_name_updates();
                if let Err(error) =
                    s.chat_observation
                        .observe(&event, &names, &chat_context::CatalogLabels)
                {
                    tracing::warn!(event="chat_observation_rejected", session_id, %error);
                }
                let conversation_threat = warnings::interruption_reason(&event).is_some()
                    && !workshop_focus::owns_input(s);
                if event.event.name == EventName::PlayerDied
                    || decision.dimension_changed
                    || (event.event.name == EventName::CombatEnded && !conversation_threat)
                {
                    s.foreground.finish_combat();
                    s.history.end_danger(
                        self.config
                            .combat
                            .ms("conversation_post_danger_player_turns"),
                    );
                }
                // 暗所だけでもmodeはalertになる。敵等の根拠なしに
                // combat_ended待ちへ入ると、明るくなっても発句が止まる。
                if event.event.name != EventName::PlayerDied && conversation_threat {
                    s.history.begin_danger();
                    s.foreground.start_combat(
                        now,
                        self.config.combat.ms("conversation_suspended_player_turns"),
                    );
                }
                input_handled = decision.input_handled;
                let combat_priority = if decision
                    .actions
                    .iter()
                    .any(|a| a.kind == "dark_push_forward")
                {
                    environment_runtime::CombatPriority::FrontAmbush
                } else if !decision.actions.is_empty() || !decision.chat_allowed {
                    environment_runtime::CombatPriority::Selected
                } else {
                    environment_runtime::CombatPriority::None
                };
                if decision.dimension_changed {
                    Self::cancel_combat_input(&mut d, session_id);
                    d.sessions
                        .get_mut(session_id)
                        .unwrap()
                        .stable_threat
                        .reset();
                    Self::cancel_assist(&mut d, session_id);
                    let s = d.sessions.get_mut(session_id).unwrap();
                    s.input_generation = s.input_generation.wrapping_add(1);
                    s.deferred_input = None;
                }
                if event.event.name == EventName::PlayerDied || decision.dimension_changed {
                    self.cancel_knowledge_queue(&mut d, session_id, "world_context_changed");
                    Self::cancel_address(&mut d, session_id, "attention_interrupted");
                }
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    session_id,
                    decision,
                    input_handled.then_some(text.as_str()),
                    warnings::interruption_reason(&event),
                );
                self.process_environment(
                    &mut d,
                    &mut jobs,
                    session_id,
                    &event,
                    now,
                    combat_priority,
                );
                let s = d.sessions.get_mut(session_id).unwrap();
                s.danger.finish_frame(s.mode);
            } else {
                let s = d.sessions.get_mut(session_id).unwrap();
                if let Err(error) = s.chat_observation.observe(
                    &event,
                    &Default::default(),
                    &chat_context::CatalogLabels,
                ) {
                    tracing::warn!(event="chat_observation_rejected", session_id, %error);
                }
            }
            self.resolve_vocalization(d.sessions.get_mut(session_id).unwrap(), Some(&event));
            self.start_pending(&mut d, &mut jobs, session_id);
            self.tick_workshop(&mut d, session_id);
            if recent && complete && text.trim().is_empty() {
                self.start_workshop_recovery(&mut d, &mut jobs, session_id);
            }
            d.revision += 1;
        }
        if let Some(feedback) = results.feedback.clone() {
            self.queue_fixed_reply(&mut d, &mut jobs, session_id, feedback, None);
        }
        let mut episode_snapshot = episode_before.map(|mut before| {
            let actions = before.actions(&d, session_id);
            let s = &d.sessions[session_id];
            (before, json!(s.mode), s.foreground.combat_active, actions)
        });
        drop(d);
        drop(jobs);
        if !duplicate && recent && complete_observation(&event) && text.trim().is_empty() {
            self.resume_knowledge_input(session_id);
        }
        if !duplicate
            && recent
            && event.event.name == EventName::StatusSnapshot
            && text.trim().is_empty()
        {
            self.try_start_haiku(session_id, false);
        }
        let input = if input_handled {
            json!({"accepted":true,"reason":"combat_input"})
        } else if !duplicate && !text.trim().is_empty() {
            self.submit_recorded(
                Some(session_id),
                &text,
                "text",
                false,
                input_generation.map(|g| (g, None)),
                false,
                workshop_record::Admission {
                    forwarded: false,
                    private,
                },
            )
        } else {
            Value::Null
        };
        let commands = {
            let d = self.data.lock().unwrap();
            if let Some((before, _, _, actions)) = episode_snapshot.as_mut() {
                actions.extend(before.input_actions(&d, session_id, &text));
            }
            d.sessions
                .get(session_id)
                .map(|s| s.assist.pending_commands(chrono::Utc::now()))
                .unwrap_or_default()
        };
        let event_id = id("evt");
        let recorded_at = chrono::Utc::now();
        if let Some((before, mode_after, combat_active, actions)) = episode_snapshot {
            // ACKs may repeat. Only newly consumed real receipts establish execution evidence.
            let command_results = results
                .observed
                .iter()
                .filter(|o| {
                    event
                        .command_results
                        .iter()
                        .any(|r| r.command_id == o.result.command_id)
                })
                .map(|o| json!(o.result))
                .collect();
            self.record_episode(crate::episode_log::Record {
                event: event.clone(),
                event_id: event_id.clone(),
                session_id: session_id.into(),
                recorded_at: recorded_at.to_rfc3339(),
                state_before: before.state,
                mode_after,
                combat_active,
                actions,
                adapter_commands: commands.iter().map(|c| json!(c)).collect(),
                command_results,
            });
        }
        json!({"accepted":true,"event_id":event_id,"session_id":session_id,"sequence":sequence,"deduplicated":duplicate,
            "state":null,"outputs":null,"commands":commands,"acknowledged_command_ids":results.acknowledged_ids,"server_time":recorded_at,"phase":"dialogue_preview","player_input":input,"_workshop_direct_input":input_handled})
    }
    pub fn submit(self: &Arc<Self>, selected: Option<&str>, text: &str, source: &str) -> Value {
        self.submit_recorded(
            selected,
            text,
            source,
            false,
            None,
            false,
            workshop_record::Admission::default(),
        )
    }
    fn submit_inner(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
    ) -> Value {
        self.submit_recorded(
            selected,
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            workshop_record::Admission {
                forwarded: true,
                private: false,
            },
        )
    }
    #[allow(clippy::too_many_arguments)] // The last value is recording metadata, not routing authority.
    fn submit_recorded(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        origin: workshop_record::Admission,
    ) -> Value {
        let sid = selected.map(str::to_owned).or_else(|| self.only_session());
        let (before, private) = {
            let d = self.data.lock().unwrap();
            let s = sid.as_deref().and_then(|sid| d.sessions.get(sid));
            (
                workshop_record::state(s.and_then(|s| s.haiku.workshop.as_ref())),
                origin.private
                    || s.is_some_and(|s| {
                        s.web.state.research.is_some()
                            || origin.forwarded
                                && s.record_private_generation.is_some_and(|(g, private)| {
                                    private
                                        && expected_generation
                                            .as_ref()
                                            .is_some_and(|(expected, _)| *expected == g)
                                })
                    }),
            )
        };
        let result = self.submit_impl(
            sid.as_deref(),
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            private,
        );
        if let Some(sid) = sid {
            let (after, mut recorded_input) = {
                let d = self.data.lock().unwrap();
                let row = d
                    .rows
                    .iter()
                    .find(|r| r["session_id"] == sid && r["turn_id"] == result["turn_id"]);
                let mut input = workshop_record::Input {
                    raw: text.into(),
                    private,
                    ..Default::default()
                };
                if let Some(row) = row {
                    input.private |= row["workshop_record_private"] == true;
                    input.epoch = row["epoch"].as_u64();
                    if row["player_input_text"] == text {
                        input.semantic = row["interpreted_player_input_text"]
                            .as_str()
                            .map(str::to_owned);
                    }
                }
                (
                    workshop_record::state(
                        d.sessions.get(&sid).and_then(|s| s.haiku.workshop.as_ref()),
                    ),
                    input,
                )
            };
            // Early rejection/classification has no semantic interpretation yet.
            if result["deduplicated"] == true {
                recorded_input.private = true;
            }
            self.record_workshop(
                &sid,
                if origin.forwarded {
                    "forwarded_admission"
                } else {
                    "input_admission"
                },
                &before,
                &after,
                &recorded_input,
                &result,
            );
        }
        result
    }
    #[allow(clippy::too_many_arguments)]
    fn submit_impl(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        private: bool,
    ) -> Value {
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
        let vocalization =
            source.trim().eq_ignore_ascii_case("voice") && crate::vocalization::is_pure(text);
        if jobs.len() >= 16 && !vocalization {
            let mut d = self.data.lock().unwrap();
            self.tick_address(&mut d, &session_id);
            if d.sessions
                .get(&session_id)
                .and_then(|s| s.address.as_ref())
                .is_some_and(|p| p.input(text) == crate::address::Action::Accept)
            {
                Self::cancel_address(&mut d, &session_id, "host_chat_queue_full");
            }
            return json!({"accepted":false,"reason":"input_queue_full"});
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get_mut(&session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        if expected_generation
            .as_ref()
            .is_some_and(|(g, _)| *g != s.input_generation)
        {
            return json!({"accepted":false,"reason":"superseded_input"});
        }
        if vocalization {
            return self.accept_vocalization(&mut d, &session_id, text);
        }
        self.resolve_vocalization(s, None);
        if !observation_fresh(s) {
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        if let Some(p) = s.knowledge_queue.iter().find(|p| p.request.text == text) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,
                "turn_id":p.request.turn,"session_id":session_id});
        }
        let queued_knowledge = s
            .knowledge_checked
            .as_ref()
            .filter(|c| {
                expected_generation
                    .as_ref()
                    .is_some_and(|(g, _)| c.generation == *g)
            })
            .and_then(|c| c.original.clone());
        if let Some(p) = s.combat_input.as_ref().filter(|p| p.text == text) {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if let Some(p) = s
            .assist_pending
            .as_ref()
            .filter(|p| p.input.raw_text == text)
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if s.deferred_input.as_ref().is_some_and(|p| p.text == text) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        s.deferred_input = None;
        if let Some(w) = s
            .warning
            .as_ref()
            .filter(|w| w.input.as_deref() == Some(text))
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":w.turn,"session_id":session_id});
        }
        if s.pending_input.as_deref() == Some(text) && s.pending_warning.is_some() {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        if expected_generation.is_none() {
            s.input_generation = s.input_generation.wrapping_add(1);
        }
        s.record_private_generation = Some((s.input_generation, private));
        Self::cancel_combat_input(&mut d, &session_id);
        Self::cancel_haiku(&mut d, &session_id, "new_player_input");
        Self::cancel_assist(&mut d, &session_id);
        Self::cancel_light(&mut d, &session_id);
        let now = self.clock.elapsed().as_millis() as u64;
        let s = d.sessions.get_mut(&session_id).unwrap();
        s.last_player_input = Some(now);
        s.ambient.note_player_input(now);
        if s.warning.as_ref().is_some_and(|w| {
            w.actions
                .iter()
                .all(|a| a.delivery == crate::combat::model::Delivery::Ambient)
        }) {
            Self::cancel_warning(&mut d, &session_id, "new_player_input");
        }
        if !skip_assist
            && !workshop_focus::quiet(&d.sessions[&session_id])
            && let Some(result) = self.assist_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if let Some(event) = s.environment_latest.as_ref().or(s.latest.as_ref())
            && s.chat_allowed
            && !workshop_focus::active(s)
            && let Some(mut speech) = s.ambient.smell_query(event, text, now)
        {
            speech.delivery = crate::combat::model::Delivery::PlayerReply;
            s.pending_warning = Some(vec![speech]);
            s.pending_input = Some(text.to_owned());
            Self::cancel_chat(&mut d, &session_id, "smell_query");
            self.start_pending(&mut d, &mut jobs, &session_id);
            return json!({"accepted":true,"session_id":session_id,"reason":"smell_query"});
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !workshop_focus::quiet(s)
            && let Some(event) = s.latest.clone()
        {
            let decision = s.combat.input(
                &event,
                text,
                self.clock.elapsed().as_millis() as u64,
                &self.config.combat,
                &self.config.warnings,
            );
            if let Some(decision) = decision {
                Self::cancel_chat(&mut d, &session_id, "combat_input");
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    &session_id,
                    decision,
                    Some(text),
                    None,
                );
                self.start_pending(&mut d, &mut jobs, &session_id);
                let s = &d.sessions[&session_id];
                return json!({"accepted":true,"session_id":session_id,"reason":"combat_input",
                    "turn_id":if s.pending_warning.is_some(){None}else{s.warning.as_ref().map(|w|w.turn.clone())},"queued":s.pending_warning.is_some(),"state":s.mode});
            }
        }
        if let Some(result) = self.close_paused_workshop(&mut d, &mut jobs, &session_id, text) {
            return result;
        }
        if let Some(result) =
            self.queue_knowledge_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if s.warning
            .as_ref()
            .is_some_and(|w| w.actions.iter().any(|a| a.kind == "workshop_resume"))
        {
            Self::cancel_warning(&mut d, &session_id, "new_player_input");
        }
        if !combat_classified
            && let Some(result) =
                self.classify_paused_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !workshop_combat_input::allowed(s) || s.warning.is_some() {
            if fresh(s)
                && s.warning.as_ref().is_some_and(|w| {
                    w.actions
                        .iter()
                        .all(|a| a.delivery == crate::combat::model::Delivery::UrgentEnvironment)
                })
            {
                s.deferred_input = Some(environment_runtime::DeferredInput {
                    text: text.to_owned(),
                    source: source.to_owned(),
                    previous_turn: None,
                });
                return json!({"accepted":true,"queued":true,"session_id":session_id,"reason":"after_environment_warning"});
            }
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        self.tick_workshop(&mut d, &session_id);
        if let Some(result) = self.check_address_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let (address_reply, replay) = match self.address_input(&mut d, &session_id, text, source) {
            address_runtime::Input::Pass => (None, None),
            address_runtime::Input::Repair(reply) => (Some(reply), None),
            address_runtime::Input::Replay(original) => (None, Some(original)),
            address_runtime::Input::Consumed(result) => return result,
        };
        let host_chat_confirmed = replay.is_some();
        let replay = replay.or(queued_knowledge);
        let text = replay.as_ref().map_or(text, |p| p.text.as_str());
        let source = replay.as_ref().map_or(source, |p| p.source.as_str());
        let private = private || replay.as_ref().is_some_and(|p| p.record_private);
        let s = d.sessions.get_mut(&session_id).unwrap();
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
        self.tick_foreground(&mut d, &session_id);
        let text_prompt = d.text_prompts.clone();
        let text_prompt_version = d.text_prompt_version;
        let s = d.sessions.get_mut(&session_id).unwrap();
        let workshop = Self::workshop_view(s, text);
        let (interpreted_text, asr_corrections) =
            crate::contextual_asr::for_workshop(text, source, &json!(workshop));
        if !asr_corrections.is_empty() {
            tracing::info!(event="asr_fix_conversation",session_id,original=text,
                interpreted=interpreted_text,applied=?asr_corrections);
        }
        let poem_input = crate::poem_input::parse(text, workshop.is_some());
        let poem_reference = s
            .haiku
            .workshop
            .as_ref()
            .map(|w| json!({"id":w.hud_id,"version":w.version,"open":w.open}));
        let event = s
            .latest
            .as_ref()
            .cloned()
            .unwrap_or_else(|| GameEvent::parse(empty_event(&s.name)).expect("preview event"));
        if let Some((_, Some(previous))) = expected_generation {
            s.history.replace_unanswered(&previous);
        }
        let resume_context = s.foreground.resume_prompt(text);
        let event_digest = s
            .history
            .situation_lines()
            .iter()
            .map(|n| format!("- {n}"))
            .chain(s.combat_digest.iter().map(|n| format!("- {n}")))
            .chain((!resume_context.is_empty()).then_some(resume_context))
            .collect::<Vec<_>>()
            .join("\n");
        let history = s.history.rows();
        let conversation_history = s.history.lines();
        let chat_native = match chat_context::capture(
            s,
            &event,
            &self.config.combat,
            crate::chat_materials::CompletedHistory {
                conversation_history: conversation_history.clone(),
                conversation_turns: history.clone(),
                event_digest: event_digest.clone(),
            },
            workshop.as_ref(),
        ) {
            Ok(native) => native,
            Err(error) => {
                tracing::warn!(event="chat_snapshot_rejected", session_id, %error);
                return json!({"accepted":false,"reason":"chat_context_unavailable"});
            }
        };
        if (poem_input.is_some() || crate::haiku_memory::clear_requested(text))
            && let Some(w) = s.haiku.workshop.as_mut()
        {
            w.followup = crate::workshop_followup::Stage::Discussion;
        }
        let audit_before = workshop_record::state(s.haiku.workshop.as_ref());
        s.epoch += 1;
        let epoch = s.epoch;
        let turn = replay
            .as_ref()
            .map_or_else(|| id("turn"), |p| p.turn.clone());
        if address_reply.is_some()
            && let Some(p) = s.address.as_mut()
        {
            p.repair = Some((turn.clone(), false));
        }
        if let Some(checked) = s.knowledge_checked.as_ref()
            && checked.original.as_ref().is_some_and(|r| r.turn == turn)
            && let Some(audit) = &checked.audit
        {
            audit.transferred();
        }
        s.current_turn = turn.clone();
        s.status = PlaybackStatus::Generating;
        let (language_active, language_state) = language_runtime::context(s, text);
        let input = json!({"audit_before":audit_before,"audit_private":private,"model":self.config.model,"max_tokens":self.config.max_tokens,"reading_engine":self.config.reading_engine,"workshop":workshop,
            "poem_input":poem_input,"poem_reference":poem_reference,"operation_id":turn,
            "source":source,"language_active":language_active,"language_state":language_state,
            "address_reply":address_reply,"host_chat_confirmed":host_chat_confirmed,
            "input_at_ms":now,"previous_activity_ms":s.foreground.last_player_at.max(s.last_completed_conversation),
            "text":text,"interpreted_text":interpreted_text,"history":history,"conversation_history":conversation_history,
            "event_digest":event_digest,"event":event});
        let mut input = input;
        if s.text_workshop {
            input["text_workshop_prompt"] = text_prompt.unwrap_or(Value::Null);
            input["text_prompt_version"] = text_prompt_version.into();
        }
        let input = bridge::Input::native(input, event, chat_native);
        if workshop.is_none()
            && s.web.state.research.is_none()
            && poem_input.is_none()
            && !crate::haiku_memory::memory_candidate(text)
        {
            s.history.push(&turn, "user", text);
        }
        let (cancel, rx) = watch::channel(false);
        s.cancel = Some(cancel);
        if replay.is_none() && d.rows.len() == 200 {
            d.rows.pop_front();
        }
        let mut new_row = json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":session_id,"category":"speech","text":"",
            "created_at":chrono::Utc::now(),"input_at_ms":now,"epoch":epoch,"reference_ids":[],"output_mode":"both","player_input_text":text,"source":source,"playback_status":"generating",
            "interpreted_player_input_text":interpreted_text,"asr_corrections":asr_corrections,
            "workshop_id":workshop.as_ref().map(|w| &w["workshop_id"]),"workshop_fixed_close":false,"workshop_state_before":audit_before,"workshop_record_private":private});
        if let Some(original) = &replay {
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = "generating".into();
                row["epoch"] = epoch.into();
                row["interpreted_player_input_text"] =
                    new_row["interpreted_player_input_text"].clone();
                row["asr_corrections"] = new_row["asr_corrections"].clone();
                row["workshop_state_before"] = new_row["workshop_state_before"].clone();
                row["workshop_record_private"] = new_row["workshop_record_private"].clone();
                if host_chat_confirmed {
                    row["language_status"] = "host_chat".into();
                    row["conversation_route"] = "casual".into();
                } else {
                    row["category"] = "speech".into();
                    row["workshop_id"] = new_row["workshop_id"].clone();
                    row["workshop_fixed_close"] = new_row["workshop_fixed_close"].clone();
                    row["resolution"] = "knowledge_resumed".into();
                }
                row["input_at_ms"] = original.input_at.into();
                row["resumed_at"] = chrono::Utc::now().to_rfc3339().into();
            } else {
                // 長い戦闘で表示履歴200件から落ちても、保留質問のIDを復元する。
                if d.rows.len() == 200 {
                    d.rows.pop_front();
                }
                new_row["input_at_ms"] = original.input_at.into();
                new_row["resumed_at"] = chrono::Utc::now().to_rfc3339().into();
                d.rows.push_back(new_row);
            }
        } else {
            d.rows.push_back(new_row);
        }
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
        self.cancel_knowledge_queue(&mut d, session_id, "manual_interrupt");
        Self::cancel_haiku(&mut d, session_id, "manual_interrupt");
        Self::cancel_chat(&mut d, session_id, "manual_interrupt");
        Self::cancel_combat_input(&mut d, session_id);
        Self::cancel_warning(&mut d, session_id, "manual_interrupt");
        Self::cancel_assist(&mut d, session_id);
        Self::cancel_light(&mut d, session_id);
        if let Some(s) = d.sessions.get_mut(session_id) {
            s.pending_vocalization = None;
            if !s.foreground.combat_active {
                s.history.end_danger(
                    self.config
                        .combat
                        .ms("conversation_post_danger_player_turns"),
                );
            }
            s.deferred_input = None;
            s.input_generation = s.input_generation.wrapping_add(1);
            if let Some(w) = s.haiku.workshop.as_mut() {
                w.followup = crate::workshop_followup::Stage::Discussion;
            }
        }
        d.revision += 1;
    }
    fn update(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        status: PlaybackStatus,
        result: Option<&Value>,
    ) -> bool {
        let mut d = self.data.lock().unwrap();
        let current = !d.stopped && d.sessions.get(sid).is_some_and(|s| s.epoch == epoch);
        let row = d.rows.iter().find(|r| r["turn_id"] == turn);
        if row.is_some_and(|r| r["epoch"].as_u64().is_some_and(|owner| owner != epoch)) {
            return false;
        }
        let workshop_id = row
            .and_then(|r| r["workshop_id"].as_str())
            .map(str::to_owned);
        let fixed_close = row.is_some_and(|r| r["workshop_fixed_close"] == true);
        let closed_by_turn = row.is_some_and(|r| r["workshop_closed_by_turn"] == true);
        let text_displayed = status == PlaybackStatus::AudioDisabled
            && result.is_some_and(|r| r["text_displayed"] == true)
            && d.sessions.get(sid).is_some_and(|s| s.text_workshop);
        let player_text = row
            .and_then(|r| r["player_input_text"].as_str())
            .unwrap_or("")
            .to_owned();
        if current
            && (status == PlaybackStatus::Queued || text_displayed)
            && let Some(wid) = workshop_id.as_ref()
        {
            let valid = d
                .sessions
                .get(sid)
                .and_then(|s| s.haiku.workshop.as_ref())
                .is_some_and(|w| {
                    w.hud_id == *wid
                        && !w.combat_paused()
                        && result.is_none_or(|r| {
                            r["workshop_action"] != "knowledge"
                                || r["workshop_version"] == w.version
                        })
                        && (result.is_none_or(|r| {
                            !matches!(
                                r["workshop_action"].as_str(),
                                Some("confirm_close" | "decline_resume")
                            )
                        }) || (w.pending.is_none()
                            && result.is_some_and(|r| r["workshop_version"] == w.version)))
                        && (w.is_open()
                            || ((fixed_close
                                || closed_by_turn
                                || result.is_some_and(|r| r["workshop_closed_by_turn"] == true))
                                && w.close_reason.as_deref() == Some("explicit_close")))
                });
            if !valid {
                Self::cancel_chat(&mut d, sid, "workshop_changed");
                return false;
            }
        }
        let status = if !current {
            PlaybackStatus::Cancelled
        } else {
            status
        };
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
            if let Some(result) = result {
                if status == PlaybackStatus::Queued
                    && workshop_id.is_some()
                    && matches!(
                        result["workshop_action"].as_str(),
                        Some("close_workshop" | "confirm_close" | "decline_resume")
                    )
                {
                    // This validated queued transition closes the pin below.
                    // Preserve ownership for its later text-display acknowledgement.
                    row["workshop_closed_by_turn"] = true.into();
                }
                if text_displayed {
                    row["text_displayed"] = true.into();
                    row["output_mode"] = "text".into();
                }
                knowledge_display::attach(row, result);
                if status == PlaybackStatus::Queued
                    && row["conversation_route"].is_null()
                    && result["memory_action"].is_null()
                    && (workshop_id.is_none()
                        || matches!(
                            result["workshop_action"].as_str(),
                            Some("unrelated" | "knowledge")
                        ))
                {
                    row["conversation_route"] = if result["knowledge_status"].is_null() {
                        "casual"
                    } else {
                        "learning"
                    }
                    .into();
                }
                if let Some(text) = result.get("text") {
                    row["text"] = text.clone();
                }
                if let Some(error) = result.get("error") {
                    row["error"] = error.clone();
                }
                if let Some(reports) = result.get("llm_reports") {
                    row["llm_reports"] = reports.clone();
                }
                for key in [
                    "memory_action",
                    "memory_outcome",
                    "workshop_action",
                    "workshop_steps",
                    "workshop_reason",
                    "workshop_outcome",
                    "workshop_revision_id",
                    "workshop_followup",
                    "workshop_feedback_outcome",
                ] {
                    if let Some(value) = result.get(key) {
                        row[key] = value.clone();
                    }
                }
            }
        }
        if current {
            let row = d.rows.iter().find(|r| r["turn_id"] == turn);
            let route = row
                .and_then(|r| {
                    serde_json::from_value::<crate::foreground::Route>(
                        r["conversation_route"].clone(),
                    )
                    .ok()
                })
                .unwrap_or_default();
            let now = self.clock.elapsed().as_millis() as u64;
            let player_at = row.and_then(|r| r["input_at_ms"].as_u64()).unwrap_or(now);
            let s = d.sessions.get_mut(sid).unwrap();
            s.status = status;
            s.haiku.last_activity = Instant::now();
            if status.is_terminal() {
                s.cancel = None;
            }
            if let Some(p) = s.address.as_mut() {
                p.playback(turn, status);
            }
            if let Some(result) = result {
                if s.text_workshop && status == PlaybackStatus::AudioDisabled && !text_displayed {
                    s.text_reply = Some((turn.into(), epoch, result.clone()));
                }
                let knowledge_detour =
                    workshop_id.is_some() && result["workshop_action"] == "knowledge";
                let workshop_reply = workshop_id.is_some()
                    && result["workshop_action"] != "unrelated"
                    && !knowledge_detour;
                if status == PlaybackStatus::Queued
                    && !workshop_reply
                    && result["memory_action"].is_null()
                    && result["web_private"] != true
                {
                    s.history.push(turn, "user", &player_text);
                    if !knowledge_detour {
                        s.foreground
                            .select(turn, &player_text, route, now, player_at);
                    }
                }
                if status == PlaybackStatus::Queued && workshop_id.is_some() && !knowledge_detour {
                    if !workshop_reply {
                        s.history.push(turn, "user", &player_text);
                    }
                    if let Some(w) = s
                        .haiku
                        .workshop
                        .as_mut()
                        .filter(|w| Some(&w.hud_id) == workshop_id.as_ref())
                    {
                        w.record_activity(Instant::now());
                        if w.open
                            && !w.combat_paused()
                            && w.pending.is_none()
                            && result["workshop_version"] == w.version
                            && let Ok(draft) = serde_json::from_value(
                                result["workshop_conversation_candidate"].clone(),
                            )
                            && let Some(candidate) =
                                crate::workshop_candidate::Candidate::from_player(
                                    draft,
                                    &w.current_lines,
                                    w.version,
                                    &player_text,
                                )
                        {
                            // Only the player's idea: independent of Dogido's reply playback.
                            w.conversation_candidate = Some(candidate);
                        }
                        if let Some(steps) = result["workshop_steps"].as_array() {
                            w.agent_steps.extend(steps.iter().cloned());
                            while w.agent_steps.len() > 12 {
                                w.agent_steps.pop_front();
                            }
                        }
                        if result["workshop_action"] == "close_workshop"
                            || result["workshop_action"] == "confirm_close"
                            || result["workshop_action"] == "decline_resume"
                        {
                            w.close("explicit_close");
                        }
                    }
                }
                if (status == PlaybackStatus::Completed || text_displayed)
                    && !knowledge_detour
                    && let Some(w) = s
                        .haiku
                        .workshop
                        .as_mut()
                        .filter(|w| Some(&w.hud_id) == workshop_id.as_ref())
                {
                    if workshop_reply {
                        let lines = w
                            .pending
                            .as_ref()
                            .map_or(w.current_lines.as_slice(), |p| &p.lines);
                        if w.open
                            && !w.combat_paused()
                            && let Some(target) = crate::workshop_target::Target::from_view(
                                &json!({"discussion_target":result["workshop_discussion_target"]}),
                                lines,
                            )
                        {
                            w.discussion_target = Some(target);
                        }
                        if w.open
                            && !w.combat_paused()
                            && result["workshop_version"] == w.version
                            && w.pending.is_none()
                        {
                            w.followup =
                                serde_json::from_value(result["workshop_followup"].clone())
                                    .unwrap_or_default();
                        }
                        w.drift_count = 0;
                        w.record_activity(Instant::now());
                        if !w.dialogue.iter().any(|p| p["turn_id"] == turn) {
                            w.dialogue.push_back(json!({"turn_id":turn,"player_text":player_text,"dogido_text":result["text"]}));
                            while w.dialogue.len() > 4 {
                                w.dialogue.pop_front();
                            }
                        }
                    } else {
                        w.drift_count += 1;
                        if w.drift_count >= 2 {
                            w.close("drift");
                        }
                    }
                }
                if status == PlaybackStatus::Queued
                    && !workshop_reply
                    && let Ok(repair) = serde_json::from_value::<Repair>(result["repair"].clone())
                {
                    s.history.annotate(turn, &repair);
                }
                if status == PlaybackStatus::Completed {
                    web_runtime::completed(s, turn, &player_text, result);
                }
                if (status == PlaybackStatus::Completed || text_displayed)
                    && !workshop_reply
                    && result["memory_action"].is_null()
                    && result["web_private"] != true
                {
                    s.last_completed_conversation = Some(now);
                    language_runtime::completed(s, result);
                    s.history
                        .push(turn, "assistant", result["text"].as_str().unwrap_or(""));
                    s.foreground.completed(
                        turn,
                        &player_text,
                        result["text"].as_str().unwrap_or(""),
                        route,
                    );
                    if let Some(pair) = s
                        .history
                        .completed_pairs()
                        .into_iter()
                        .find(|p| p["turn_id"] == turn)
                        && result["knowledge_status"].is_null()
                        && route == crate::foreground::Route::Casual
                        && !s.haiku.material_turns.iter().any(|p| p["turn_id"] == turn)
                    {
                        if s.haiku.material_turns.len() == 3 {
                            s.haiku.material_turns.pop_front();
                        }
                        s.haiku.material_turns.push_back(pair);
                    }
                }
            }
        }
        d.revision += 1;
        let record = if status == PlaybackStatus::Queued {
            d.rows
                .iter()
                .find(|r| r["turn_id"] == turn)
                .cloned()
                .map(|row| {
                    let after = workshop_record::state(
                        d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref()),
                    );
                    (row, after)
                })
        } else {
            None
        };
        drop(d);
        if let Some((row, after)) = record {
            self.record_workshop(
                sid,
                "decision",
                &row["workshop_state_before"],
                &after,
                &workshop_record::Input::from_row(&row),
                &row,
            );
        }
        tracing::info!(
            event = "dialogue_status",
            session_id = sid,
            turn_id = turn,
            playback_status = status.as_str()
        );
        current
    }
    async fn run_turn(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        mut input: bridge::Input,
        cancel: watch::Receiver<bool>,
    ) {
        let before = input
            .as_object_mut()
            .and_then(|o| o.remove("audit_before"))
            .unwrap_or(Value::Null);
        let private = input
            .as_object_mut()
            .and_then(|o| o.remove("audit_private"))
            == Some(Value::Bool(true));
        let attempt = workshop_record::Attempt::new(
            before,
            workshop_record::Input {
                raw: input["text"].as_str().unwrap_or("").into(),
                semantic: input["interpreted_text"].as_str().map(str::to_owned),
                private,
                epoch: Some(epoch),
            },
        );
        self.clone()
            .run_turn_body(sid.clone(), turn.clone(), epoch, input, cancel)
            .await;
        self.record_workshop_turn(&sid, &turn, &attempt);
    }
    async fn run_turn_body(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        mut input: bridge::Input,
        mut cancel: watch::Receiver<bool>,
    ) {
        let started = Instant::now();
        let permit = tokio::select! { _=bridge::cancelled(&mut cancel)=>{self.update(&sid,&turn,epoch,PlaybackStatus::Cancelled,None);return;}, p=self.serial.acquire()=>p.unwrap() };
        // An earlier authorized save may have completed while this turn waited.
        // Rebuild the read-only context before planning against that new version.
        if input["workshop"].is_object() && input["poem_input"].is_null() {
            let mut d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get_mut(&sid)
                && s.epoch == epoch
                && s.haiku.workshop.as_ref().is_some_and(|w| {
                    w.open
                        && input["workshop"]["workshop_id"] == w.hud_id
                        && input["workshop"]["version"] != w.version
                })
            {
                let text = input["text"].as_str().unwrap_or("").to_owned();
                if let Some(view) = Self::workshop_view(s, &text) {
                    input["workshop"] = view;
                }
            }
        }
        // 観測更新が途切れた場合も、古い場所の返答・音声を続けない。
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let mut monitor_cancel = cancel.clone();
        let workshop_id = input["workshop"]["workshop_id"].as_str().map(str::to_owned);
        let provisional_target = input["workshop"]["provisional"].as_str().map(str::to_owned);
        let monitor = monitor::Monitor::spawn(async move {
            loop {
                tokio::select! {
                    _=bridge::cancelled(&mut monitor_cancel)=>break,
                    _=tokio::time::sleep(Duration::from_millis(200))=>{
                        let mut d=owner.data.lock().unwrap();
                        owner.tick_workshop(&mut d, &monitor_sid);
                        let stale = d.sessions.get(&monitor_sid).is_some_and(|s| {
                            // 閉じる返答の配送中も、開始時と同じ安定した敵なら最後まで話せる。
                            let same_threat = provisional_target.as_ref().is_some_and(|key| {
                                workshop_combat_input::ready(s, owner.clock.elapsed().as_millis() as u64, &owner.config).as_ref() == Some(key)
                            });
                            let workshop_allowed = workshop_id.is_some()
                                && (workshop_focus::quiet_observation(s)
                                    || workshop_combat_input::provisional(s) || same_threat);
                            s.epoch == epoch && !(fresh(s) || workshop_allowed)
                        });
                        if stale {Self::cancel_chat(&mut d,&monitor_sid,"stale_observation");break;}
                        let expired = workshop_id.as_ref().is_some_and(|wid| d.sessions.get(&monitor_sid)
                            .filter(|s| s.epoch==epoch).is_some_and(|s| s.haiku.workshop.as_ref()
                                .is_none_or(|w| w.hud_id != *wid || (!w.is_open() && w.close_reason.as_deref()!=Some("explicit_close")))));
                        if expired {Self::cancel_chat(&mut d,&monitor_sid,"workshop_changed");break;}
                    }
                }
            }
        });
        let mut completed = false;
        self.prepare_web(&sid, epoch, &mut input, &cancel).await;
        let web_result = self.web_turn(&sid, epoch, &input, &mut cancel).await;
        let result = if let Err(error) = web_result {
            Err(error)
        } else if let Some(result) = web_result.unwrap() {
            Ok(result)
        } else if input["address_reply"].is_string() {
            match self.select_foreground(&sid, &turn, epoch, crate::foreground::Route::Learning) {
                Ok(()) => bridge::render(&self.config, &self.llm, input.clone(), &mut cancel).await,
                Err(error) => Err(error),
            }
        } else if input["poem_input"].is_object() {
            self.save_poem_input(&sid, epoch, &input, &mut cancel).await
        } else if crate::haiku_memory::clear_requested(input["text"].as_str().unwrap_or("")) {
            self.clear_lessons(&sid, epoch, &input).await
        } else if input["workshop"].is_object() {
            match self.render_workshop(&input, &mut cancel).await {
                Ok(workshop) if workshop["workshop_action"] == "unrelated" => {
                    let safe = self
                        .data
                        .lock()
                        .unwrap()
                        .sessions
                        .get(&sid)
                        .is_some_and(fresh);
                    if !safe {
                        Err(anyhow::anyhow!("combat_workshop_only"))
                    } else {
                        let mut ordinary = input.clone();
                        // 対話plannerには過去の通常会話と現在入力だけを渡す。
                        ordinary["workshop"] = Value::Null;
                        ordinary["workshop_fallback"] = true.into();
                        match bridge::render(&self.config, &self.llm, ordinary, &mut cancel).await {
                            Ok(mut result) => {
                                let mut reports = workshop["llm_reports"]
                                    .as_array()
                                    .cloned()
                                    .unwrap_or_default();
                                reports.extend(
                                    result["llm_reports"]
                                        .as_array()
                                        .cloned()
                                        .unwrap_or_default(),
                                );
                                for key in [
                                    "workshop_id",
                                    "workshop_action",
                                    "workshop_steps",
                                    "workshop_reason",
                                ] {
                                    result[key] = workshop[key].clone();
                                }
                                result["llm_reports"] = json!(reports);
                                Ok(result)
                            }
                            Err(error) => Err(error),
                        }
                    }
                }
                result => result,
            }
        } else {
            bridge::render_with_route(
                &self.config,
                &self.llm,
                input.clone(),
                &mut cancel,
                |route| self.select_foreground(&sid, &turn, epoch, route),
                |outcome| self.hold_language_handoff(&sid, &turn, epoch, &input, outcome),
            )
            .await
        };
        let result = match result {
            Ok(r) if r["memory_query"].is_object() => {
                self.recall_poems(&sid, epoch, &input, &r["memory_query"], &mut cancel)
                    .await
            }
            r => r,
        };
        match result {
            Ok(mut result) => {
                if input["host_chat_confirmed"] == true {
                    result["language_status"] = "host_chat".into();
                    result["language_state"] = json!(crate::language::State::default());
                }
                self.apply_language_result(&sid, &turn, epoch, &result);
                self.record_feedback(&sid, epoch, &input, &mut result).await;
                if let Err(error) = self.apply_workshop_edit(&sid, epoch, &mut result).await {
                    result["text"] =
                        "句が変わったか、編集を続けられん状態になったわ。もう一度確認してな。"
                            .into();
                    result["spoken_text"] = result["text"].clone();
                    result["workshop_reason"] = error.to_string().into();
                    result["workshop_action"] = "fallback".into();
                }
                if let Some(outcome) = result.get("workshop_outcome").cloned()
                    && let Some(last) = result
                        .get_mut("workshop_steps")
                        .and_then(Value::as_array_mut)
                        .and_then(|s| s.last_mut())
                {
                    last["outcome"] = outcome;
                }
                if let Some(unsupported) = result.get("unsupported").cloned() {
                    result["error"] = unsupported;
                    self.update(
                        &sid,
                        &turn,
                        epoch,
                        PlaybackStatus::Unsupported,
                        Some(&result),
                    );
                } else if result["language_status"] == "awaiting_address" {
                    self.update(
                        &sid,
                        &turn,
                        epoch,
                        PlaybackStatus::NotSelected,
                        Some(&result),
                    );
                } else if result["text"].as_str().is_none_or(|s| s.is_empty()) {
                    self.update(&sid, &turn, epoch, PlaybackStatus::Quiet, Some(&result));
                } else if self.update(&sid, &turn, epoch, PlaybackStatus::Queued, Some(&result)) {
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
                                self.update(&sid, &turn, epoch, PlaybackStatus::Started, None);
                            },
                        )
                        .await;
                    let status = match playback {
                        Ok(()) => PlaybackStatus::Completed,
                        Err(error) => {
                            result["error"] = error.to_string().into();
                            if *cancel.borrow() {
                                PlaybackStatus::Cancelled
                            } else if !self.config.audio_enabled {
                                result.as_object_mut().unwrap().remove("error");
                                PlaybackStatus::AudioDisabled
                            } else {
                                PlaybackStatus::Failed
                            }
                        }
                    };
                    // Web開始は音声プロセスの実終了と同じepochの確認後だけ。
                    self.web_playback(&sid, epoch, &result, status);
                    completed = self.update(&sid, &turn, epoch, status, Some(&result))
                        && status == PlaybackStatus::Completed
                        && result["workshop_action"].is_null()
                        && result["knowledge_status"].is_null()
                        && result["memory_action"].is_null();
                }
            }
            Err(error) => {
                self.update(
                    &sid,
                    &turn,
                    epoch,
                    if *cancel.borrow() {
                        PlaybackStatus::Cancelled
                    } else {
                        PlaybackStatus::Failed
                    },
                    Some(&json!({"error":error.to_string()})),
                );
            }
        }
        monitor.finish().await;
        drop(permit);
        if completed {
            self.try_start_haiku(&sid, true);
        }
        tracing::info!(
            event = "dialogue_finished",
            session_id = sid,
            turn_id = turn,
            total_ms = started.elapsed().as_millis() as u64
        );
    }
    pub fn snapshot(&self, selected: Option<&str>) -> Value {
        let d = self.data.lock().unwrap();
        let rows = d
            .rows
            .iter()
            .filter(|r| selected.is_none_or(|id| r["session_id"] == id))
            .collect::<Vec<_>>();
        json!({"revision":d.revision,"phase":"dialogue_preview","audio_enabled":self.config.audio_enabled,"llm_enabled":self.config.llm_enabled,
            "sessions":d.sessions.iter().map(|(id,s)|json!({"session_id":id,"name":s.name,"status":s.status,"observation_mode":if s.preview{"none"}else{"minecraft"},"history":s.history.rows(),"history_retention":s.history.retention_status(),"foreground":s.foreground.snapshot(self.clock.elapsed().as_millis() as u64),"web":s.web.state.snapshot(),"workshop_history":s.haiku.workshop.as_ref().map(|w| &w.dialogue),"workshop_followup":s.haiku.workshop.as_ref().map(|w| w.followup),"workshop_discussion_target":s.haiku.workshop.as_ref().and_then(|w| w.discussion_target.as_ref()),"state":s.mode,"chat_allowed":fresh(s)})).collect::<Vec<_>>(),
            "utterances":rows,"references":knowledge_display::collect(&rows)})
    }
    pub fn cancel_all(&self) {
        {
            let mut d = self.data.lock().unwrap();
            d.stopped = true;
            let ids = d.sessions.keys().cloned().collect::<Vec<_>>();
            for sid in ids {
                self.cancel_knowledge_queue(&mut d, &sid, "server_shutdown");
                Self::cancel_haiku(&mut d, &sid, "server_shutdown");
                Self::cancel_chat(&mut d, &sid, "server_shutdown");
                Self::cancel_combat_input(&mut d, &sid);
                Self::cancel_warning(&mut d, &sid, "server_shutdown");
                Self::cancel_assist(&mut d, &sid);
                Self::cancel_light(&mut d, &sid);
            }
        }
    }
    pub async fn shutdown(&self) {
        self.cancel_all();
        let jobs = std::mem::take(&mut *self.jobs.lock().unwrap());
        for job in jobs {
            let _ = job.await;
        }
        let open_workshops: Vec<_> = {
            let d = self.data.lock().unwrap();
            d.sessions
                .iter()
                .filter_map(|(sid, s)| {
                    s.haiku
                        .workshop
                        .as_ref()
                        .filter(|w| w.open)
                        .map(|w| (sid.clone(), workshop_record::state(Some(w))))
                })
                .collect()
        };
        for (sid, before) in open_workshops {
            self.record_workshop(
                &sid,
                "lifecycle",
                &before,
                &Value::Null,
                &workshop_record::Input::default(),
                &json!({"reason":"server_shutdown"}),
            );
        }
        self.workshop_records.flush().await;
        self.workshop_records.forget_all();
        self.combat_classifier.close().await;
        if let Some(recorder) = &self.episodes {
            let recorder = recorder.clone();
            let _ = tokio::task::spawn_blocking(move || recorder.close()).await;
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
    async fn only_fresh_complete_nonduplicate_observations_change_game_pause() {
        let mut config = DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            ..Default::default()
        };
        config.haiku.enabled = false;
        let d = Dialogue::new(config).unwrap();
        d.register("pause", "試験", false);
        let frame = |seq, paused: Option<bool>, partial: bool, stale: bool| {
            let mut e = empty_event("試験");
            e["sequence"] = json!(seq);
            e["observed_at"] =
                json!(chrono::Utc::now() - chrono::Duration::seconds(if stale { 30 } else { 0 }));
            e["world"]["time_of_day"] = json!(13713);
            e["player"]["dimension"] = json!("minecraft:the_nether");
            if let Some(p) = paused {
                e["world"]["game_paused"] = json!(p);
            }
            if partial {
                e["event"]["name"] = json!("ambient_mob_detected");
                e["event"]["source_kind"] = json!("auditory");
            }
            GameEvent::parse(e).unwrap()
        };
        let paused = || {
            d.data.lock().unwrap().sessions["pause"]
                .foreground
                .game_paused
        };
        d.observe("pause", frame(1, Some(true), false, false), None);
        assert!(paused());
        for e in [
            frame(2, Some(false), true, false),
            frame(3, Some(false), false, true),
            frame(1, Some(false), false, false),
        ] {
            d.observe("pause", e, None);
            assert!(
                paused(),
                "partial, stale and duplicate observations cannot unpause"
            );
        }
        d.observe("pause", frame(4, None, false, false), None);
        assert!(
            !paused(),
            "legacy complete snapshots keep the former behavior"
        );
        d.observe("pause", frame(5, Some(true), false, false), None);
        assert!(paused());
        d.observe("pause", frame(6, Some(false), false, false), None);
        assert!(!paused(), "fixed world time and Nether do not imply pause");
        d.shutdown().await;
    }

    #[tokio::test]
    async fn knowledge_history_requires_playback_and_never_becomes_poem_material() {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("knowledge-session", "試験", true);
        let result = json!({"text":"定型的な言葉やで。", "knowledge_status":"found", "references":[{
            "source_id":"src.example", "title_ja":"資料", "citation_label_ja":"文部科学省",
            "locator":"1頁", "url":"https://example.org/reference", "source_kind":"organization_authored_or_issued"}]});
        {
            let mut data = dialogue.data.lock().unwrap();
            for (sid, turn) in [("knowledge-session", "first"), ("other-session", "other")] {
                data.rows
                    .push_back(json!({"session_id":sid, "turn_id":turn,"utterance_id":turn,
                    "player_input_text":"枕詞って何？", "created_at":"2026-09-27T00:00:00Z"}));
            }
        }
        assert!(dialogue.update(
            "knowledge-session",
            "first",
            0,
            PlaybackStatus::Queued,
            Some(&result)
        ));
        assert!(dialogue.update(
            "knowledge-session",
            "first",
            0,
            PlaybackStatus::Failed,
            Some(&result)
        ));
        assert!(
            dialogue.data.lock().unwrap().sessions["knowledge-session"]
                .history
                .completed_pairs()
                .is_empty()
        );
        assert!(dialogue.update(
            "knowledge-session",
            "first",
            0,
            PlaybackStatus::Completed,
            Some(&result)
        ));
        {
            let data = dialogue.data.lock().unwrap();
            let session = &data.sessions["knowledge-session"];
            assert_eq!(session.history.completed_pairs().len(), 1);
            assert!(session.haiku.material_turns.is_empty());
        }
        let view = dialogue.snapshot(Some("knowledge-session"));
        assert_eq!(view["references"][0]["utterance_ids"], json!(["first"]));
        assert_eq!(view["utterances"][0]["category"], "knowledge");
        assert_eq!(
            view["utterances"][0]["reference_ids"][0],
            view["references"][0]["reference_id"]
        );
        assert_eq!(
            dialogue.snapshot(Some("other-session"))["references"],
            json!([])
        );
        dialogue.data.lock().unwrap().rows.pop_front();
        assert_eq!(dialogue.snapshot(None)["references"], json!([]));
        dialogue.shutdown().await;
    }

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
            let plan = crate::threats::Policy::default()
                .observe(
                    &event(1, &["zombie"; 3]),
                    0,
                    false,
                    &dialogue.config.warnings,
                )
                .unwrap();
            Dialogue::queue_actions(&mut d, "s", &[warnings::from_warning(plan)], None);
            // 発声前の待機中は、最新の個体数・構成へ更新する。
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
            assert!(!d.sessions["s"].chat_allowed);
            let pending = d.sessions["s"].pending_warning.as_ref().unwrap()[0]
                .visual_plan
                .as_ref()
                .unwrap();
            assert_eq!(pending.text, "スケルトン1体、ゾンビ1体おるで。");
            assert!(pending.cue.is_some());
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
        assert_eq!(rows[1]["text"], "ひいっ！ ゾンビ2体おるで。");
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
                let plan = crate::threats::Policy::default()
                    .observe(&event(1, "front"), 0, false, &dialogue.config.warnings)
                    .unwrap();
                Dialogue::queue_actions(&mut d, "s", &[warnings::from_warning(plan)], None);
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
                let pending = d.sessions["s"].pending_warning.as_ref().unwrap()[0]
                    .visual_plan
                    .as_ref()
                    .unwrap();
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
            PlaybackStatus::Completed,
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
