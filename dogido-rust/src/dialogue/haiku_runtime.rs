//! 発句の開始・取消・文脈・再生・保存・表示の所有者。Pythonは既存UniDic tokenだけ。
use super::*;
use crate::{
    haiku::{
        self, Backend,
        preparation::{Preparation, RuntimeSnapshot},
    },
    haiku_bridge::{Helper, LiveBackend, Route, RouteConfig},
    haiku_record::{MemoryStore, PreparedEmission, Workshop, project_workshop},
};
use anyhow::{Context, ensure};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub llm_enabled: bool,
    pub interval_ms: u64,
    pub quiet_time_ms: u64,
    pub structured_max_tokens: u64,
    pub grounding_max_tokens: u64,
    pub generation_strategy: String,
    pub max_regeneration_rounds: i64,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub max_tokens: Option<u64>,
    pub timeout_ms: Option<u64>,
    pub job_timeout_ms: u64,
    pub memory_enabled: bool,
    pub memory_dir: PathBuf,
    pub workshop_open_ms: u64,
    pub workshop_idle_ms: u64,
    pub low_threat_resume_delay_ms: u64,
    pub platform_ai: super::combat_classifier::Settings,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            enabled: true,
            llm_enabled: true,
            interval_ms: 600_000,
            quiet_time_ms: 30_000,
            structured_max_tokens: 192,
            grounding_max_tokens: 512,
            generation_strategy: "three_slot".into(),
            max_regeneration_rounds: 6,
            base_url: None,
            model: None,
            max_tokens: None,
            timeout_ms: None,
            job_timeout_ms: 600_000,
            memory_enabled: true,
            memory_dir: PathBuf::from(".dogido_memory/rust-migration"),
            workshop_open_ms: 240_000,
            workshop_idle_ms: 120_000,
            low_threat_resume_delay_ms: 8_000,
            platform_ai: super::combat_classifier::Settings::default(),
        }
    }
}
pub(super) struct Routes {
    pub(super) chat: Route,
    pub(super) haiku: Route,
}
impl Routes {
    pub fn new(c: &DialogueConfig) -> Result<Self> {
        let h = &c.haiku;
        h.platform_ai.validate()?;
        ensure!(
            h.low_threat_resume_delay_ms >= 1000,
            "invalid workshop resume delay"
        );
        ensure!(
            h.structured_max_tokens > 0
                && h.grounding_max_tokens > 0
                && h.job_timeout_ms > 0
                && h.workshop_open_ms > 0
                && h.workshop_idle_ms > 0,
            "invalid haiku settings"
        );
        ensure!(
            ["whole_poem", "three_slot", "one_plus_two", "two_plus_one"]
                .contains(&h.generation_strategy.as_str()),
            "invalid haiku strategy"
        );
        let key = std::env::var("DOGIDO_LLM_API_KEY").ok();
        let haiku_key = std::env::var("DOGIDO_LLM_HAIKU_API_KEY")
            .ok()
            .or_else(|| key.clone());
        let mut routes = Self {
            chat: Route::new(
                RouteConfig {
                    base_url: c.base_url.clone(),
                    model: c.model.clone(),
                    max_tokens: c.max_tokens,
                    timeout_ms: c.timeout_ms,
                },
                key.as_deref(),
            )?,
            haiku: Route::new(
                RouteConfig {
                    base_url: h.base_url.clone().unwrap_or_else(|| c.base_url.clone()),
                    model: h.model.clone().unwrap_or_else(|| c.model.clone()),
                    max_tokens: h.max_tokens.unwrap_or(c.max_tokens),
                    timeout_ms: h.timeout_ms.unwrap_or(c.timeout_ms),
                },
                haiku_key.as_deref(),
            )?,
        };
        routes.chat.llm.set_enabled(c.llm_enabled && h.llm_enabled);
        routes.haiku.llm.set_enabled(c.llm_enabled && h.llm_enabled);
        Ok(routes)
    }
}
struct Observation {
    event: GameEvent,
    runtime: RuntimeSnapshot,
}

pub(super) struct Active {
    id: String,
    cancel: watch::Sender<bool>,
}
pub(super) struct State {
    pub active: Option<Active>,
    pub workshop: Option<Workshop>,
    pub last_activity: Instant,
    pub material_turns: VecDeque<Value>,
    workshop_generation: String,
}
impl Default for State {
    fn default() -> Self {
        Self {
            active: None,
            workshop: None,
            last_activity: Instant::now(),
            material_turns: VecDeque::new(),
            workshop_generation: String::new(),
        }
    }
}
impl State {
    pub fn foreground(&self) -> bool {
        self.active.is_some() || self.workshop.as_ref().is_some_and(Workshop::is_open)
    }
}
fn safe(s: &Session) -> bool {
    !s.preview
        && !s.foreground.game_paused
        && fresh(s)
        && s.mode == crate::combat::model::Mode::Normal
        && s.latest
            .as_ref()
            .is_some_and(|e| warnings::interruption_reason(e).is_none())
        && s.audio_latest
            .as_ref()
            .is_none_or(|e| warnings::interruption_reason(e).is_none())
}
fn current(d: &Data, sid: &str, job: &str) -> bool {
    !d.stopped
        && d.sessions.get(sid).is_some_and(|s| {
            safe(s)
                && s.haiku
                    .active
                    .as_ref()
                    .is_some_and(|a| a.id == job && !*a.cancel.borrow())
        })
}
impl Dialogue {
    pub(super) fn cancel_haiku(d: &mut Data, sid: &str, reason: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        s.haiku.last_activity = Instant::now();
        let Some(active) = s.haiku.active.as_ref() else {
            return;
        };
        if *active.cancel.borrow() {
            return;
        }
        let _ = active.cancel.send(true);
        s.status = PlaybackStatus::Cancelled;
        for row in d.rows.iter_mut().filter(|r| r["haiku_job_id"] == active.id) {
            if matches!(
                row["playback_status"].as_str(),
                Some("generating" | "queued" | "started")
            ) {
                row["playback_status"] = "cancelled".into();
                row["cancel_reason"] = reason.into();
            }
        }
        d.revision += 1;
        tracing::info!(
            event = "haiku_cancelled",
            session_id = sid,
            job_id = active.id,
            reason
        );
    }
    pub(super) fn tick_workshop(&self, d: &mut Data, sid: &str) {
        let audit_before = super::workshop_record::state(
            d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref()),
        );
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        let now = Instant::now();
        let observed = observation_fresh(s);
        let ready = super::workshop_combat_input::ready(
            s,
            self.clock.elapsed().as_millis() as u64,
            &self.config,
        );
        let clear = super::workshop_combat_runtime::clear_for_resume(s);
        let workshop_quiet = super::workshop_focus::quiet(s);
        let h = &mut s.haiku;
        if let Some(w) = h.workshop.as_mut() {
            let before = (w.is_open(), w.combat_paused());
            if clear {
                w.provisional = None;
            }
            let provisional = w.provisional.is_some() && w.provisional == ready;
            if !provisional
                && (w.provisional.is_some()
                    || !workshop_quiet
                        && (s.mode != crate::combat::model::Mode::Normal || !s.chat_allowed))
            {
                w.pause(now);
            }
            if w.combat_paused()
                && observed
                && let Some(e) = &s.latest
            {
                w.recovery.observe(e);
            }
            w.expire(
                now,
                Duration::from_millis(self.config.haiku.workshop_open_ms),
                Duration::from_millis(self.config.haiku.workshop_idle_ms),
            );
            if before != (w.is_open(), w.combat_paused()) {
                d.revision += 1;
            }
        }
        let audit_after = super::workshop_record::state(
            d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref()),
        );
        if audit_before != audit_after {
            self.record_workshop(
                sid,
                "lifecycle",
                &audit_before,
                &audit_after,
                &super::workshop_record::Input::default(),
                &json!({"reason":"workshop_tick"}),
            );
        }
    }
    pub fn workshop_snapshot(&self, sid: &str, sequence: u64) -> Option<Value> {
        let d = self.data.lock().unwrap();
        let s = d.sessions.get(sid)?;
        let mode = serde_json::to_value(s.mode).unwrap();
        // HUDのdangerは句の中断表示。環境だけのalert/panicを持ち込まない。
        let workshop_mode = if super::workshop_focus::owns_input(s) {
            "normal"
        } else {
            mode.as_str().unwrap_or("normal")
        };
        let mut hud = project_workshop(
            s.haiku.workshop.as_ref(),
            sid,
            i64::try_from(sequence).ok(),
            workshop_mode,
            s.haiku.active.is_some(),
        );
        hud["revision"] = d.revision.into();
        Some(hud)
    }
    pub(super) fn try_start_haiku(self: &Arc<Self>, sid: &str, boundary: bool) {
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let mut d = self.data.lock().unwrap();
        if d.stopped || !self.config.haiku.enabled || jobs.len() >= 16 {
            return;
        }
        self.tick_workshop(&mut d, sid);
        self.tick_foreground(&mut d, sid);
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        let now = self.clock.elapsed().as_millis() as u64;
        s.foreground.clock.start(now);
        let casual = s.foreground.route == crate::foreground::Route::Casual;
        if !safe(s)
            || s.foreground.combat_active
            || s.foreground.route.blocks_haiku()
            || s.haiku.foreground()
            || s.cancel.is_some()
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.assist_pending.is_some()
            || s.deferred_input.is_some()
            || !s.knowledge_queue.is_empty()
            || s.foreground.clock.elapsed(now) < self.config.haiku.interval_ms
            || (!(casual || boundary)
                && s.haiku.last_activity.elapsed()
                    < Duration::from_millis(self.config.haiku.quiet_time_ms))
        {
            return;
        }
        let event = s
            .environment_latest
            .as_ref()
            .or(s.latest.as_ref())
            .unwrap()
            .clone();
        let observation = Observation {
            event,
            runtime: s.haiku_context.clone(),
        };
        let completed = if casual {
            s.haiku.material_turns.iter().cloned().collect()
        } else {
            vec![]
        };
        Self::cancel_light(&mut d, sid);
        let s = d.sessions.get_mut(sid).unwrap();
        s.foreground.suspend(
            now,
            "auto_haiku",
            self.config.combat.ms("conversation_suspended_player_turns"),
        );
        s.foreground
            .activate(crate::foreground::Route::HaikuPreparation, now, None);
        let job = id("haiku");
        let (cancel, rx) = watch::channel(false);
        s.haiku.active = Some(Active {
            id: job.clone(),
            cancel,
        });
        s.status = PlaybackStatus::Generating;
        d.revision += 1;
        let this = self.clone();
        let sid = sid.to_owned();
        jobs.push(tokio::spawn(async move {
            this.run_haiku(sid, job, observation, completed, rx).await;
        }));
    }
    fn haiku_row(
        &self,
        sid: &str,
        job: &str,
        part: &str,
        text: &str,
        status: PlaybackStatus,
    ) -> bool {
        let mut d = self.data.lock().unwrap();
        if !current(&d, sid, job) {
            return false;
        }
        let turn = format!("{job}:{part}");
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["text"] = text.into();
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
        } else {
            if d.rows.len() == 200 {
                d.rows.pop_front();
            }
            d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":turn,"haiku_job_id":job,"session_id":sid,"category":if part=="poem"{"haiku"}else{"speech"},"text":text,"created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"both","source":"automatic_haiku","playback_status":status}));
        }
        let s = d.sessions.get_mut(sid).unwrap();
        s.status = status;
        s.haiku.last_activity = Instant::now();
        d.revision += 1;
        tracing::info!(
            event = "haiku_status",
            session_id = sid,
            job_id = job,
            part,
            status = status.as_str(),
            text
        );
        true
    }
    async fn haiku_speak(
        &self,
        sid: &str,
        job: &str,
        part: &str,
        text: &str,
        spoken: &str,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<()> {
        ensure!(
            self.haiku_row(sid, job, part, text, PlaybackStatus::Queued),
            "haiku superseded"
        );
        if !self.config.audio_enabled {
            ensure!(
                !*cancel.borrow() && cancel.has_changed().is_ok(),
                "cancelled"
            );
            ensure!(
                self.haiku_row(sid, job, part, text, PlaybackStatus::AudioDisabled),
                "haiku superseded"
            );
            return Ok(());
        }
        let mut voice = self.config.clone();
        voice.speed = voice.haiku_speed;
        self.audio
            .speak(&voice, spoken, cancel, || {
                self.haiku_row(sid, job, part, text, PlaybackStatus::Started);
            })
            .await?;
        ensure!(
            self.haiku_row(sid, job, part, text, PlaybackStatus::Completed),
            "haiku superseded"
        );
        Ok(())
    }
    async fn haiku_pipeline(
        self: &Arc<Self>,
        sid: &str,
        job: &str,
        observation: Observation,
        completed: Vec<Value>,
        helper: &mut Helper,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<()> {
        // AI/補助のfutureは取消で捨て、その後run_haikuがhelperを必ずkill+waitする。
        // 音声は外側selectで捨てず、Audio自身の取消・player waitを最後まで待つ。
        macro_rules! step {($future:expr)=>{tokio::select!{biased;_=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("haiku cancelled")),r=$future=>r}?};}
        let h = &self.config.haiku;
        let corrections = step!(self.reading_overlay());
        let lessons = step!(self.active_lessons());
        let (mut preparation, context) = Preparation::capture(haiku::preparation::Start {
            event: observation.event,
            runtime: observation.runtime,
            settings: haiku::preparation::Settings {
                llm_enabled: self.config.llm_enabled && h.llm_enabled,
                structured_max_tokens: h.structured_max_tokens,
                grounding_max_tokens: h.grounding_max_tokens,
                generation_strategy: h.generation_strategy.clone(),
                max_regeneration_rounds: h.max_regeneration_rounds,
            },
            reading_corrections: corrections,
            lessons,
            completed_turns: completed,
            dialogue_material: None,
        })?;
        let mut backend = LiveBackend {
            helper,
            chat: &self.haiku_routes.chat,
            haiku: &self.haiku_routes.haiku,
            requests: vec![],
            reports: vec![],
        };
        let generated = if let Some(fixed) = context.fixed_text.as_deref() {
            haiku::GroundedHaikuResult {
                text: fixed.into(),
                accepted: true,
                line_sources: vec![],
                failure_reason: None,
                generation_strategy: "fixed_catalog".into(),
                regeneration_rounds: 0,
                prompt_variant: haiku::PROMPT_VARIANT.into(),
            }
        } else {
            let request = context.request.context("missing haiku irony request")?;
            let irony = step!(backend.generate(request));
            let inspiration = preparation.inspiration(&irony)?;
            let spoken = inspiration.spoken_text.as_str();
            self.haiku_speak(sid, job, "inspiration", spoken, spoken, cancel)
                .await?;
            let request = inspiration.request;
            let scene = step!(backend.generate(request));
            let input = preparation.materials(&scene)?.input;
            step!(haiku::generate(&mut backend, input))
        };
        let completed_at = chrono::Utc::now();
        let completed_clock = Instant::now();
        tracing::info!(event="haiku_generation",session_id=sid,job_id=job,result=%json!(generated),llm_reports=%json!(backend.reports));
        if !generated.accepted {
            self.haiku_speak(
                sid,
                job,
                "failure",
                &generated.text,
                &generated.text,
                cancel,
            )
            .await?;
            return Ok(());
        }
        let prepared: PreparedEmission = step!(preparation.emission(&generated, backend.helper));
        let emission = prepared.complete(completed_at)?;
        let text = emission.prepared.surface_text.clone().unwrap_or_default();
        let spoken = format!(
            "{} {}",
            emission.prepared.preface.as_deref().unwrap_or(""),
            emission.prepared.reading_text.as_deref().unwrap_or("")
        );
        {
            // shutdownと同じjobs→data順。確定と保存job登録を一つの境界にする。
            let mut jobs = self.jobs.lock().unwrap();
            let mut d = self.data.lock().unwrap();
            ensure!(current(&d, sid, job), "haiku superseded before commit");
            let s = d.sessions.get_mut(sid).unwrap();
            s.haiku.workshop = Some(Workshop::open(emission.clone(), None, completed_clock));
            s.haiku.workshop_generation = job.into();
            let now = completed_clock
                .saturating_duration_since(self.clock)
                .as_millis() as u64;
            s.foreground.clock.reset(now);
            s.foreground
                .activate(crate::foreground::Route::HaikuWorkshop, now, None);
            s.haiku.material_turns.clear();
            let player_name = s.name.clone();
            d.revision += 1;
            tracing::info!(event="haiku_workshop_opened",session_id=sid,job_id=job,created_at=%completed_at);
            // 保存は実再生成功とは別。音声取消後も完成句の保存jobは継続する。
            // I/Oは音声permitを保持せず、shutdownがその完了まで待つ。
            if h.memory_enabled {
                let root = h.memory_dir.join("sessions").join(sid);
                let save_sid = sid.to_owned();
                let owner = self.clone();
                let save_job = job.to_owned();
                jobs.push(tokio::spawn(async move {
                    let write_sid=save_sid.clone();
                    let saved=tokio::task::spawn_blocking(move||{
                        let store=MemoryStore::new(root);
                        let saved=store.save_agent_haiku(&write_sid,&player_name,&emission);
                        if let Err(error)=store.append_haiku_emission(&write_sid,&emission){tracing::error!(event="haiku_episode_save_failed",session_id=write_sid,%error);}
                        saved
                    }).await;
                    match saved {
                        Ok(Ok(saved))=>{
                            let mut d=owner.data.lock().unwrap();
                            if let Some(s)=d.sessions.get_mut(&save_sid) && s.haiku.workshop_generation==save_job
                                && let Some(w)=s.haiku.workshop.as_mut() {w.entry_id=Some(saved.entry_id);}
                        },
                        error=>tracing::error!(event="haiku_save_failed",session_id=save_sid,?error),
                    }
                }));
            }
        }
        let opened = self.workshop_record_state(sid);
        self.record_workshop(
            sid,
            "lifecycle",
            &Value::Null,
            &opened,
            &super::workshop_record::Input::default(),
            &json!({"reason":"workshop_opened","turn_id":format!("{job}:poem")}),
        );
        self.haiku_speak(sid, job, "poem", &text, &spoken, cancel)
            .await
    }
    async fn run_haiku(
        self: Arc<Self>,
        sid: String,
        job: String,
        observation: Observation,
        completed: Vec<Value>,
        mut cancel: watch::Receiver<bool>,
    ) {
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let monitor_job = job.clone();
        let mut monitor_cancel = cancel.clone();
        let began = Instant::now();
        let monitor = super::monitor::Monitor::spawn(async move {
            loop {
                tokio::select! {_=bridge::cancelled(&mut monitor_cancel)=>break,_=tokio::time::sleep(Duration::from_millis(100))=>{
                    let mut d=owner.data.lock().unwrap();
                    if !current(&d,&monitor_sid,&monitor_job) || began.elapsed()>=Duration::from_millis(owner.config.haiku.job_timeout_ms) {
                        if d.sessions.get(&monitor_sid).is_some_and(|s|s.haiku.active.as_ref().is_some_and(|a|a.id==monitor_job)) { Self::cancel_haiku(&mut d,&monitor_sid,"stale_or_expired"); } break;
                    }
                }}
            }
        });
        let outcome:Result<()>=async {
            let _permit=tokio::select!{biased;_=bridge::cancelled(&mut cancel)=>anyhow::bail!("haiku cancelled"),p=self.serial.acquire()=>p?};
            let script=self.config.helper.with_file_name("haiku_tokens.py");
            let mut helper=Helper::start(&self.config.python,&script)?;
            let result=self.haiku_pipeline(&sid,&job,observation,completed,&mut helper,&mut cancel).await;
            let cleanup=helper.finish(result.is_err()).await;result?;cleanup
        }.await;
        monitor.finish().await;
        {
            let mut d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get_mut(&sid)
                && s.haiku.active.as_ref().is_some_and(|a| a.id == job)
            {
                let was_cancelled = s.haiku.active.as_ref().is_some_and(|a| *a.cancel.borrow());
                s.haiku.active = None;
                if !was_cancelled && s.haiku.workshop_generation != job {
                    s.foreground
                        .clock
                        .reset(self.clock.elapsed().as_millis() as u64);
                }
                s.haiku.last_activity = Instant::now();
                if !was_cancelled {
                    s.status = if outcome.is_ok() {
                        PlaybackStatus::Completed
                    } else {
                        PlaybackStatus::Failed
                    };
                }
                for row in d.rows.iter_mut().filter(|r| r["haiku_job_id"] == job) {
                    if matches!(
                        row["playback_status"].as_str(),
                        Some("queued" | "started" | "generating")
                    ) {
                        row["playback_status"] = "failed".into();
                        row["error"] = outcome.as_ref().err().map(ToString::to_string).into();
                    }
                }
                d.revision += 1;
            }
        }
        tracing::info!(event="haiku_finished",session_id=sid,job_id=job,error=?outcome.err(),total_ms=began.elapsed().as_millis() as u64);
        // GETに副作用を持たせず、観測が止まった場合も完成後の表示時計を進める。
        loop {
            tokio::time::sleep(Duration::from_millis(250)).await;
            let mut d = self.data.lock().unwrap();
            if d.stopped
                || d.sessions.get(&sid).is_none_or(|s| {
                    s.haiku.workshop_generation != job
                        || !s.haiku.workshop.as_ref().is_some_and(Workshop::is_open)
                })
            {
                break;
            }
            self.tick_workshop(&mut d, &sid);
        }
    }
}
