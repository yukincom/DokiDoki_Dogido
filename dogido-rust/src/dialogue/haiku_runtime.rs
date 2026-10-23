//! 発句の開始・取消・文脈・再生・保存・表示の所有者。Pythonは既存UniDic tokenだけ。
use super::*;
use crate::{
    haiku::{
        self, Backend,
        preparation::{Preparation, RuntimeSnapshot},
    },
    haiku_record::{MemoryStore, PreparedEmission, Workshop, project_workshop},
    python_worker::{Helper, LiveBackend, Route, RouteConfig},
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
    pub generation_strategy: String,
    pub max_regeneration_rounds: i64,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub max_tokens: Option<u64>,
    pub timeout_ms: Option<u64>,
    pub job_timeout_ms: u64,
    pub memory_enabled: bool,
    pub memory_dir: PathBuf,
    pub screenshot_dir: Option<PathBuf>,
    pub workshop_idle_ms: u64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            enabled: true,
            llm_enabled: true,
            interval_ms: 600_000,
            quiet_time_ms: 30_000,
            structured_max_tokens: 192,
            generation_strategy: "three_slot".into(),
            max_regeneration_rounds: 6,
            base_url: None,
            model: None,
            max_tokens: None,
            timeout_ms: None,
            job_timeout_ms: 600_000,
            memory_enabled: true,
            memory_dir: PathBuf::from(".dogido_memory/rust-migration"),
            screenshot_dir: None,
            workshop_idle_ms: crate::haiku_record::DEFAULT_IDLE_TIMEOUT.as_millis() as u64,
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
        ensure!(
            h.structured_max_tokens > 0 && h.job_timeout_ms > 0 && h.workshop_idle_ms > 0,
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
    pub(super) id: String,
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
fn safe(s: &Session, allow_scanned_hostiles: bool) -> bool {
    let scanned_hostiles = allow_scanned_hostiles
        && s.latest
            .as_ref()
            .is_some_and(super::workshop_focus::scanned_hostiles_only);
    let mode_safe = s.mode == crate::combat::model::Mode::Normal
        || scanned_hostiles && s.mode != crate::combat::model::Mode::Aftermath;
    // combat_runtimeは視認由来のpanicでもchat_allowedをfalseにする。
    // 採取中だけ、最新観測が視認／スキャンだけなら観測鮮度を保ったまま発句を継続する。
    let observation_safe = fresh(s) || scanned_hostiles && observation_fresh(s);
    let event_safe = |e: &crate::events::GameEvent| {
        warnings::interruption_reason(e).is_none()
            || allow_scanned_hostiles && super::workshop_focus::scanned_hostiles_only(e)
    };
    !s.preview
        && !s.foreground.game_paused
        && s.warning.is_none()
        && s.pending_warning.is_none()
        && observation_safe
        && mode_safe
        && (!s.foreground.combat_active || scanned_hostiles)
        && s.latest.as_ref().is_some_and(event_safe)
        && s.audio_latest.as_ref().is_none_or(event_safe)
}
fn current(d: &Data, sid: &str, job: &str, allow_scanned_hostiles: bool) -> bool {
    !d.stopped
        && d.sessions.get(sid).is_some_and(|s| {
            safe(s, allow_scanned_hostiles)
                && s.haiku
                    .active
                    .as_ref()
                    .is_some_and(|a| a.id == job && !*a.cancel.borrow())
        })
}
impl Dialogue {
    pub(super) fn cancel_haiku(d: &mut Data, sid: &str, reason: &str) {
        // adapterの文字イベントはsubmitより先に届く。開始済み発句をここで切らない。
        if reason == "new_player_input" && super::reply_queue::playback_started(d, sid) {
            return;
        }
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
        // まだ句が完成していない取消だけ撮影予約も終える。保存job開始後は結果を待つ。
        if s.haiku.workshop_generation != active.id {
            s.screenshots.discard_unbound(&active.id);
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
        // 移動・道具使用・snapshot受信では延長しない。この句の返答処理中だけ時計を止める。
        let reply_in_progress = d.sessions.get(sid).is_some_and(|s| {
            s.haiku.workshop.as_ref().is_some_and(|w| {
                s.haiku
                    .active
                    .as_ref()
                    .is_some_and(|a| a.id == s.haiku.workshop_generation)
                    || (!s.status.is_terminal()
                        && d.rows.iter().any(|r| {
                            r["session_id"] == sid
                                && r["turn_id"] == s.current_turn
                                && r["workshop_id"] == w.hud_id
                        }))
            })
        });
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        let now = Instant::now();
        let observed = observation_fresh(s);
        let workshop_quiet = super::workshop_focus::quiet(s);
        let h = &mut s.haiku;
        if let Some(w) = h.workshop.as_mut() {
            let before = (w.is_open(), w.combat_paused());
            if !workshop_quiet && (s.mode != crate::combat::model::Mode::Normal || !s.chat_allowed)
            {
                w.pause(now);
            }
            if w.combat_paused()
                && observed
                && let Some(e) = &s.latest
            {
                w.recovery.observe(e);
            }
            if reply_in_progress {
                w.record_activity(now);
            } else {
                w.expire(
                    now,
                    Duration::from_millis(self.config.haiku.workshop_idle_ms),
                );
            }
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
        if !safe(
            s,
            super::workshop_focus::TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION,
        ) || s.foreground.route.blocks_haiku()
            || s.haiku.foreground()
            || s.cancel.is_some()
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.assist_pending.is_some()
            || s.deferred_input.is_some()
            || !s.reply_queue.is_empty()
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
        let event = super::body_runtime::event(s, &event);
        // 身体の感覚が未取得・失効した瞬間は、所持品だけを残した観測で発句を始めない。
        // 時計やqueueを消費せず戻り、次の有効な身体観測で同じ発句機会を再確認する。
        if event.body_origin().is_some_and(|o| !o.available) {
            return;
        }
        let runtime = s.haiku_context.clone();
        let observation = Observation { event, runtime };
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
        let turn = format!("{job}:{part}");
        // 開始後の観測失効だけで、実際に完走した同じ行を失敗にしない。
        // 次の行の開始・生成・保存は引き続きcurrentの鮮度・安全検査を通す。
        let completed_owned = status == PlaybackStatus::Completed
            && !d.stopped
            && d.sessions.get(sid).is_some_and(|s| {
                s.haiku
                    .active
                    .as_ref()
                    .is_some_and(|a| a.id == job && !*a.cancel.borrow())
            })
            && d.rows.iter().any(|r| {
                r["session_id"] == sid && r["turn_id"] == turn && r["playback_status"] == "started"
            });
        if !completed_owned
            && !current(
                &d,
                sid,
                job,
                super::workshop_focus::TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION,
            )
        {
            return false;
        }
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["text"] = text.into();
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
        } else {
            if d.rows.len() == 200 {
                d.rows.pop_front();
            }
            let session_instance = d.sessions[sid].instance_id.clone();
            d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":turn,"haiku_job_id":job,"session_id":sid,"session_instance":session_instance,"category":if part=="poem"{"haiku"}else{"speech"},"text":text,"created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"both","source":"automatic_haiku","playback_status":status}));
        }
        let s = d.sessions.get_mut(sid).unwrap();
        s.status = status;
        s.haiku.last_activity = Instant::now();
        if part == "poem"
            && matches!(
                status,
                PlaybackStatus::Completed | PlaybackStatus::AudioDisabled
            )
            && let Some(w) = s.haiku.workshop.as_mut().filter(|w| w.is_open())
        {
            w.record_activity(Instant::now());
        }
        if status == PlaybackStatus::Completed {
            self.confirm_haiku_delivery(&mut d, sid, &turn, part, false);
        }
        if status == PlaybackStatus::Queued {
            Self::publish_text(
                &mut d,
                sid,
                &turn,
                super::text_delivery::Kind::Haiku { part: part.into() },
            );
        }
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
    pub(super) fn confirm_haiku_delivery(
        &self,
        d: &mut Data,
        sid: &str,
        turn: &str,
        part: &str,
        text: bool,
    ) -> bool {
        let Some(row) = d
            .rows
            .iter()
            .find(|r| r["session_id"] == sid && r["turn_id"] == turn)
            .cloned()
        else {
            return false;
        };
        let Some(s) = d.sessions.get_mut(sid) else {
            return false;
        };
        if d.stopped
            || (!row["session_instance"].is_null() && row["session_instance"] != s.instance_id)
        {
            return false;
        }
        if row["delivery_confirmed"] == true {
            return true;
        }
        if text
            && (row["playback_status"] == "cancelled"
                || !s
                    .haiku
                    .active
                    .as_ref()
                    .is_some_and(|active| row["haiku_job_id"] == active.id))
        {
            return false;
        }
        let body = row["text"].as_str().unwrap_or("");
        if body.is_empty() {
            return false;
        }
        if part != "poem" {
            s.history.push(turn, "assistant", body);
        } else {
            let verb = if text { "示した" } else { "詠んだ" };
            self.share_story(
                sid,
                s,
                super::story::StoryEvent {
                    id: turn,
                    workshop: None,
                    kind: "haiku_delivered",
                    text: &format!("ドギドが川柳を{verb}。「{body}」"),
                    details: json!({"text":body,"delivery":if text {"text"} else {"audio"}}),
                },
            );
        }
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["delivery_confirmed"] = true.into();
        }
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
        let mut voice = self.output_config(sid);
        if !voice.audio_enabled {
            self.wait_for_text(sid, &format!("{job}:{part}"), cancel)
                .await;
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
    #[tracing::instrument(skip_all, fields(session_id = sid, job_id = job))]
    /// 前置き生成の直前に、記録用の身体視点を既存撮影台帳へ予約する。
    /// 将来はここで受け取る同じ画像を川柳材料へ加える予定。現在は画像を生成モデルへ渡さない。
    /// 撮影完了か開始期限を待つため、有効な撮影が前置きの出力より後に始まることはない。
    async fn prepare_haiku_photo(
        &self,
        sid: &str,
        job: &str,
        observation: &Observation,
    ) -> Result<()> {
        {
            let mut data = self.data.lock().unwrap();
            let s = data.sessions.get_mut(sid).context("haiku session closed")?;
            ensure!(
                s.haiku
                    .active
                    .as_ref()
                    .is_some_and(|active| active.id == job),
                "haiku cancelled"
            );
            if !self.config.haiku.memory_enabled || s.preview || s.text_workshop {
                return Ok(());
            }
            let Some(context) = observation
                .event
                .body_origin()
                .and_then(|origin| origin.context.as_ref())
            else {
                return Ok(());
            };
            s.screenshots.prepare(
                sid,
                job,
                context,
                chrono::Utc::now(),
                self.config.haiku.job_timeout_ms,
            );
        }
        loop {
            {
                let data = self.data.lock().unwrap();
                let s = data.sessions.get(sid).context("haiku session closed")?;
                ensure!(
                    s.haiku
                        .active
                        .as_ref()
                        .is_some_and(|active| active.id == job),
                    "haiku cancelled"
                );
                if s.screenshots.capture_settled(job, chrono::Utc::now()) {
                    return Ok(());
                }
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
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
        step!(self.prepare_haiku_photo(sid, job, &observation));
        let (mut preparation, context) = Preparation::capture(haiku::preparation::Start {
            event: observation.event,
            runtime: observation.runtime,
            settings: haiku::preparation::Settings {
                llm_enabled: self.config.llm_enabled && h.llm_enabled,
                structured_max_tokens: h.structured_max_tokens,
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
        // 各モデル返答は完了時に出力済み。末尾では結果だけを表示する。
        tracing::info!(event="haiku_generation",session_id=sid,job_id=job,result=%json!(generated),llm_call_count=backend.reports.len());
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
            ensure!(
                current(
                    &d,
                    sid,
                    job,
                    super::workshop_focus::TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION,
                ),
                "haiku superseded before commit"
            );
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
            let capture_instance = s.instance_id.clone();
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
                            if let Some(s)=d.sessions.get_mut(&save_sid) && s.instance_id==capture_instance {
                                if saved.inserted {
                                    s.screenshots.bind(&save_job,&save_sid,&format!("session:{save_sid}:{}",saved.entry_id),chrono::Utc::now());
                                } else { s.screenshots.discard_unbound(&save_job); }
                                if s.haiku.workshop_generation==save_job
                                    && let Some(w)=s.haiku.workshop.as_mut() {w.entry_id=Some(saved.entry_id);}
                            }
                        },
                        error=>{
                            tracing::error!(event="haiku_save_failed",session_id=save_sid,?error);
                            if let Some(s)=owner.data.lock().unwrap().sessions.get_mut(&save_sid)
                                && s.instance_id==capture_instance { s.screenshots.discard_unbound(&save_job); }
                        },
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
                    if (!current(&d,&monitor_sid,&monitor_job,super::workshop_focus::TEMPORARY_ALLOW_SCANNED_HOSTILES_FOR_SENRYU_COLLECTION) || began.elapsed()>=Duration::from_millis(owner.config.haiku.job_timeout_ms))
                        && !super::reply_queue::playback_started(&d,&monitor_sid) {
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
                if s.haiku.workshop_generation != job {
                    s.screenshots.discard_unbound(&job);
                }
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
        self.resume_reply_input(&sid);
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

#[cfg(test)]
mod completion_tests {
    use super::*;
    #[tokio::test]
    async fn completed_audio_survives_stale_observation_but_next_part_does_not() {
        let mut config = super::super::DialogueConfig::default();
        config.haiku.memory_enabled = false;
        let d = Dialogue::new(config).unwrap();
        d.register("s", "試験", false);
        let (tx, rx) = watch::channel(false);
        {
            let mut data = d.data.lock().unwrap();
            data.sessions.get_mut("s").unwrap().haiku.active = Some(Active {
                id: "job".into(),
                cancel: tx,
            });
            data.rows.push_back(
                json!({"session_id":"s","haiku_job_id":"job","turn_id":"job:preface",
                "playback_status":"started","text":"一句できたで。"}),
            );
            Dialogue::cancel_haiku(&mut data, "s", "new_player_input");
            assert!(!*rx.borrow());
        }
        assert!(d.haiku_row(
            "s",
            "job",
            "preface",
            "一句できたで。",
            PlaybackStatus::Completed
        ));
        assert!(!d.haiku_row("s", "job", "poem", "古い句", PlaybackStatus::Queued));
        assert_eq!(d.data.lock().unwrap().rows[0]["delivery_confirmed"], true);
        Dialogue::cancel_haiku(&mut d.data.lock().unwrap(), "s", "manual_interrupt");
        assert!(!d.haiku_row("s", "job", "preface", "旧結果", PlaybackStatus::Completed));
        d.shutdown().await;
    }
}
