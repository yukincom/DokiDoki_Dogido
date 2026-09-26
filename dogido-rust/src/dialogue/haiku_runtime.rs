//! 発句の開始・取消・再生・保存・表示の所有者。材料と辞書だけPythonへ委譲する。
use super::*;
use crate::{
    haiku::{self, Backend},
    haiku_bridge::{Helper, LiveBackend, Route, RouteConfig},
    haiku_record::{MemoryStore, PreparedEmission, Workshop, project_workshop},
};
use anyhow::ensure;
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
        }
    }
}
pub(super) struct Routes {
    chat: Route,
    haiku: Route,
}
impl Routes {
    pub fn new(c: &DialogueConfig) -> Result<Self> {
        let h = &c.haiku;
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
        Ok(Self {
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
        })
    }
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
    cycle: Option<Instant>,
    workshop_generation: String,
}
impl Default for State {
    fn default() -> Self {
        Self {
            active: None,
            workshop: None,
            last_activity: Instant::now(),
            material_turns: VecDeque::new(),
            cycle: None,
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
        s.status = "cancelled".into();
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
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        let now = Instant::now();
        let h = &mut s.haiku;
        if let Some(w) = h.workshop.as_mut() {
            let before = (w.is_open(), w.combat_paused());
            if s.mode != crate::combat::model::Mode::Normal || !s.chat_allowed {
                w.pause(now);
            } else {
                w.resume(now);
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
    }
    pub(super) fn workshop_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
        text: &str,
    ) -> Option<Value> {
        let w = d
            .sessions
            .get_mut(sid)?
            .haiku
            .workshop
            .as_mut()
            .filter(|w| w.is_open())?;
        let command = text.trim().trim_end_matches(['。', '！', '!', '？', '?']);
        if text.trim().ends_with(['？', '?']) {
            return Some(
                json!({"accepted":false,"reason":"workshop_editing_not_migrated","detail":"句の相談・共同編集は次の移行段階です。現在の句は掛け軸で確認できます。"}),
            );
        }
        let reply = match command {
            "終了" | "終わり" | "川柳は終了" | "川柳終わり" | "句はここまで" =>
            {
                w.close("explicit_close");
                "ほな、この句はここまでにしよか。".to_owned()
            }
            "今の句" | "今の川柳" | "もう一度読んで" => {
                w.record_activity(Instant::now());
                w.emission()
                    .prepared
                    .reading_text
                    .clone()
                    .unwrap_or_default()
            }
            _ => {
                return Some(
                    json!({"accepted":false,"reason":"workshop_editing_not_migrated","detail":"句の相談・共同編集は次の移行段階です。「終了」で通常会話へ戻れます。"}),
                );
            }
        };
        self.queue_fixed_reply(d, jobs, sid, reply, Some(text));
        d.revision += 1;
        Some(json!({"accepted":true,"session_id":sid,"reason":"workshop_fixed_reply"}))
    }
    pub fn workshop_snapshot(&self, sid: &str, sequence: u64) -> Option<Value> {
        let d = self.data.lock().unwrap();
        let s = d.sessions.get(sid)?;
        let mode = serde_json::to_value(s.mode).unwrap();
        let mut hud = project_workshop(
            s.haiku.workshop.as_ref(),
            sid,
            i64::try_from(sequence).ok(),
            mode.as_str().unwrap_or("normal"),
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
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        let now = Instant::now();
        let cycle = *s.haiku.cycle.get_or_insert(now);
        let casual = s.casual_foreground
            && !crate::combat::model::elapsed(
                self.clock.elapsed().as_millis() as u64,
                s.last_player_input,
                self.config.combat.ms("conversation_active_ttl_ms"),
            );
        if !safe(s)
            || s.haiku.foreground()
            || s.cancel.is_some()
            || s.warning.is_some()
            || s.pending_warning.is_some()
            || s.assist_pending.is_some()
            || s.deferred_input.is_some()
            || cycle.elapsed() < Duration::from_millis(self.config.haiku.interval_ms)
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
        let completed = if casual {
            s.haiku.material_turns.iter().cloned().collect()
        } else {
            vec![]
        };
        Self::cancel_light(&mut d, sid);
        let s = d.sessions.get_mut(sid).unwrap();
        let job = id("haiku");
        let (cancel, rx) = watch::channel(false);
        s.haiku.active = Some(Active {
            id: job.clone(),
            cancel,
        });
        s.status = "generating".into();
        d.revision += 1;
        let this = self.clone();
        let sid = sid.to_owned();
        jobs.push(tokio::spawn(async move {
            this.run_haiku(sid, job, event, completed, rx).await;
        }));
    }
    fn haiku_row(&self, sid: &str, job: &str, part: &str, text: &str, status: &str) -> bool {
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
        s.status = status.into();
        s.haiku.last_activity = Instant::now();
        d.revision += 1;
        tracing::info!(
            event = "haiku_status",
            session_id = sid,
            job_id = job,
            part,
            status,
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
            self.haiku_row(sid, job, part, text, "queued"),
            "haiku superseded"
        );
        self.audio
            .speak(&self.config, spoken, cancel, || {
                self.haiku_row(sid, job, part, text, "started");
            })
            .await?;
        ensure!(
            self.haiku_row(sid, job, part, text, "completed"),
            "haiku superseded"
        );
        Ok(())
    }
    async fn haiku_pipeline(
        self: &Arc<Self>,
        sid: &str,
        job: &str,
        event: GameEvent,
        completed: Vec<Value>,
        helper: &mut Helper,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<()> {
        // AI/補助のfutureは取消で捨て、その後run_haikuがhelperを必ずkill+waitする。
        // 音声は外側selectで捨てず、Audio自身の取消・player waitを最後まで待つ。
        macro_rules! step {($future:expr)=>{tokio::select!{biased;_=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("haiku cancelled")),r=$future=>r}?};}
        let h = &self.config.haiku;
        let context=step!(helper.exchange(json!({"op":"haiku_context","event":event,"completed_turns":completed,
            "settings":{"llm_enabled":h.llm_enabled,"haiku_structured_max_tokens":h.structured_max_tokens,"haiku_grounding_max_tokens":h.grounding_max_tokens,"haiku_generation_strategy":h.generation_strategy,"haiku_max_regeneration_rounds":h.max_regeneration_rounds}})));
        let mut backend = LiveBackend {
            helper,
            chat: &self.haiku_routes.chat,
            haiku: &self.haiku_routes.haiku,
            requests: vec![],
            reports: vec![],
        };
        let generated = if let Some(fixed) = context["fixed_text"].as_str() {
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
            let request = serde_json::from_value(context["request"].clone())?;
            let irony = step!(backend.generate(request));
            let inspiration = step!(
                backend
                    .helper
                    .exchange(json!({"op":"haiku_inspiration","payload":irony}))
            );
            let spoken = inspiration["spoken_text"].as_str().unwrap_or("");
            self.haiku_speak(sid, job, "inspiration", spoken, spoken, cancel)
                .await?;
            let request = serde_json::from_value(inspiration["request"].clone())?;
            let scene = step!(backend.generate(request));
            let materials = step!(
                backend
                    .helper
                    .exchange(json!({"op":"haiku_materials","payload":scene}))
            );
            let input = serde_json::from_value(materials["input"].clone())?;
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
        let projection = step!(
            backend
                .helper
                .exchange(json!({"op":"haiku_emission","result":generated}))
        );
        let prepared: PreparedEmission = serde_json::from_value(projection)?;
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
            s.haiku.cycle = Some(completed_clock);
            s.casual_foreground = false;
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
        self.haiku_speak(sid, job, "poem", &text, &spoken, cancel)
            .await
    }
    async fn run_haiku(
        self: Arc<Self>,
        sid: String,
        job: String,
        event: GameEvent,
        completed: Vec<Value>,
        mut cancel: watch::Receiver<bool>,
    ) {
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let monitor_job = job.clone();
        let mut monitor_cancel = cancel.clone();
        let began = Instant::now();
        let monitor = tokio::spawn(async move {
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
            let script=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("scripts/haiku_helper.py");
            let mut helper=Helper::start(&self.config.python,&script)?;
            let result=self.haiku_pipeline(&sid,&job,event,completed,&mut helper,&mut cancel).await;
            let cleanup=helper.finish(result.is_err()).await;result?;cleanup
        }.await;
        monitor.abort();
        let _ = monitor.await;
        {
            let mut d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get_mut(&sid)
                && s.haiku.active.as_ref().is_some_and(|a| a.id == job)
            {
                let was_cancelled = s.haiku.active.as_ref().is_some_and(|a| *a.cancel.borrow());
                s.haiku.active = None;
                if !was_cancelled && s.haiku.workshop_generation != job {
                    s.haiku.cycle = Some(Instant::now());
                }
                s.haiku.last_activity = Instant::now();
                if !was_cancelled {
                    s.status = if outcome.is_ok() {
                        "completed"
                    } else {
                        "failed"
                    }
                    .into();
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
