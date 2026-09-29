//! 中断中の入力を別jobで分類し、現在の句・敵・入力世代へ再照合する。
use super::*;
use crate::{
    combat::model::{Delivery, Scope, Speech},
    workshop_combat_input::Action,
};

pub(super) struct Pending {
    pub turn: String,
    pub text: String,
    source: String,
    generation: u64,
    workshop_id: String,
    version: u64,
    threat_generation: u64,
    pub cancel: watch::Sender<bool>,
}

pub(super) fn ready(s: &Session, now: u64, config: &DialogueConfig) -> Option<String> {
    if s.preview
        || !observation_fresh(s)
        || s.audio_latest
            .as_ref()
            .is_some_and(|e| !e.auditory_threats.is_empty())
    {
        return None;
    }
    s.stable_threat.ready(
        s.latest.as_ref()?,
        now,
        config.haiku.low_threat_resume_delay_ms,
        config.warnings.recent_damage_window_ms,
    )
}
pub(super) fn provisional(s: &Session) -> bool {
    observation_fresh(s)
        && s.haiku
            .workshop
            .as_ref()
            .is_some_and(|w| w.open && !w.combat_paused() && w.provisional.is_some())
}
pub(super) fn allowed(s: &Session) -> bool {
    fresh(s) || provisional(s)
}

impl Dialogue {
    pub(super) fn cancel_combat_input(d: &mut Data, sid: &str) {
        if let Some(p) = d.sessions.get_mut(sid).and_then(|s| s.combat_input.take()) {
            let _ = p.cancel.send(true);
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == p.turn) {
                row["playback_status"] = "cancelled".into();
            }
            d.revision += 1;
        }
    }
    pub(super) fn classify_paused_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Option<Value> {
        let s = d.sessions.get_mut(sid)?;
        let w = s
            .haiku
            .workshop
            .as_ref()
            .filter(|w| w.open && w.combat_paused())?;
        let turn = id("combat_input");
        let attempt = super::workshop_record::Attempt::new(
            super::workshop_record::state(Some(w)),
            super::workshop_record::Input {
                raw: text.into(),
                semantic: None,
                private: s.web.state.research.is_some()
                    || s.record_private_generation
                        .is_some_and(|(g, private)| g == s.input_generation && private),
                epoch: Some(s.epoch),
            },
        );
        let input = json!({"text":text,"verse":crate::workshop_edit::reading(w.pending.as_ref().map_or(&w.current_lines, |p| &p.lines)),
            "workshop":{"emission":w.emission,"materials":w.materials,"current_lines":w.current_lines,
                "pending":w.pending,"dialogue":w.dialogue,"agent_steps":w.agent_steps}});
        let (cancel, rx) = watch::channel(false);
        s.combat_input = Some(Pending {
            turn: turn.clone(),
            text: text.into(),
            source: source.into(),
            generation: s.input_generation,
            workshop_id: w.hud_id.clone(),
            version: w.version,
            threat_generation: s.stable_threat.generation,
            cancel,
        });
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,"category":"workshop_input",
            "player_input_text":text,"source":source,"text":"","created_at":chrono::Utc::now(),
            "epoch":attempt.input.epoch,"workshop_record_private":attempt.input.private,
            "reference_ids":[],"output_mode":"text","playback_status":"generating"}));
        d.revision += 1;
        let this = self.clone();
        let session = sid.to_owned();
        let tid = turn.clone();
        jobs.push(tokio::spawn(async move {
            this.run_combat_input(session, tid, input, rx, attempt)
                .await;
        }));
        Some(
            json!({"accepted":true,"session_id":sid,"turn_id":turn,"reason":"combat_workshop_input"}),
        )
    }
    pub(super) fn combat_workshop_feedback(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<tokio::task::JoinHandle<()>>,
        sid: &str,
        input: &str,
        mut speech: Speech,
    ) {
        let s = d.sessions.get_mut(sid).unwrap();
        if s.warning.is_some() || s.pending_warning.is_some() {
            return;
        }
        speech.delivery = Delivery::PlayerReply;
        s.pending_warning = Some(vec![speech]);
        s.pending_input = Some(input.into());
        self.start_pending(d, jobs, sid);
    }
    async fn run_combat_input(
        self: Arc<Self>,
        sid: String,
        turn: String,
        input: Value,
        cancel: watch::Receiver<bool>,
        attempt: super::workshop_record::Attempt,
    ) {
        self.clone()
            .run_combat_input_body(sid.clone(), turn.clone(), input, cancel)
            .await;
        self.record_workshop_turn(&sid, &turn, &attempt);
    }
    async fn run_combat_input_body(
        self: Arc<Self>,
        sid: String,
        turn: String,
        input: Value,
        mut cancel: watch::Receiver<bool>,
    ) {
        let result = {
            let work = self
                .combat_classifier
                .run(&self.config, &self.llm, input, &mut cancel);
            tokio::pin!(work);
            loop {
                tokio::select! {
                    result=&mut work=>break result,
                    _=tokio::time::sleep(Duration::from_millis(200))=> {
                        let mut d = self.data.lock().unwrap();
                        let valid = d.sessions.get(&sid).is_some_and(|s| observation_fresh(s)
                            && s.combat_input.as_ref().is_some_and(|p| p.turn == turn && p.generation == s.input_generation
                                && s.haiku.workshop.as_ref().is_some_and(|w| w.open && w.hud_id == p.workshop_id && w.version == p.version)));
                        if !valid { Self::cancel_combat_input(&mut d,&sid); }
                    }
                }
            }
        };
        if *cancel.borrow() {
            return;
        }
        let result = result
            .unwrap_or_else(|e| json!({"error":e.to_string(),"analysis":{"action":"uncertain"}}));
        let action: Action =
            serde_json::from_value(result["analysis"]["action"].clone()).unwrap_or_default();
        // 再開の声かけ・実入力の処理は既存警告の後。分類jobは音声permitを占有しない。
        if matches!(
            action,
            Action::ResumeWorkshop | Action::WorkshopInput | Action::Unrelated
        ) {
            let deadline = Instant::now() + Duration::from_secs(30);
            loop {
                let busy = self
                    .data
                    .lock()
                    .unwrap()
                    .sessions
                    .get(&sid)
                    .is_some_and(|s| s.warning.is_some() || s.pending_warning.is_some());
                if !busy || Instant::now() >= deadline {
                    break;
                }
                tokio::select! { _=bridge::cancelled(&mut cancel)=>return, _=tokio::time::sleep(Duration::from_millis(100))=>{} }
            }
        }
        let forward = {
            let mut jobs = self.jobs.lock().unwrap();
            jobs.retain(|j| !j.is_finished());
            let mut d = self.data.lock().unwrap();
            if d.stopped || *cancel.borrow() {
                return;
            }
            self.tick_workshop(&mut d, &sid);
            let Some(s) = d.sessions.get_mut(&sid) else {
                return;
            };
            if !s.combat_input.as_ref().is_some_and(|p| p.turn == turn) {
                return;
            }
            let p = s.combat_input.take().unwrap();
            let valid = observation_fresh(s)
                && s.input_generation == p.generation
                && s.haiku.workshop.as_ref().is_some_and(|w| {
                    w.open
                        && w.combat_paused()
                        && w.hud_id == p.workshop_id
                        && w.version == p.version
                });
            let clear = super::workshop_combat_runtime::clear_for_resume(s);
            let key = ready(s, self.clock.elapsed().as_millis() as u64, &self.config)
                .filter(|_| s.stable_threat.generation == p.threat_generation);
            let quiet = s.warning.is_none() && s.pending_warning.is_none();
            let mut forward = None;
            let mut feedback = None;
            let mut outcome = "held";
            if valid {
                let w = s.haiku.workshop.as_mut().unwrap();
                match action {
                    Action::CloseWorkshop => {
                        feedback = Some(Speech::new(
                            "workshop_close_feedback",
                            if w.pending.is_some() {
                                "未採用の案があるで。採用するか、元の句に戻すか教えてな。"
                            } else {
                                w.close("combat_interrupted_close");
                                "おけ、句はここまでにしよか。"
                            },
                        ));
                        outcome = "close_checked";
                    }
                    Action::ResumeWorkshop | Action::WorkshopInput
                        if quiet && (clear || key.is_some()) =>
                    {
                        w.resume(Instant::now());
                        w.provisional = if clear { None } else { key };
                        w.recovery = crate::workshop_combat::Recovery::default();
                        w.drift_count = 0;
                        outcome = if clear {
                            "resumed"
                        } else {
                            "provisional_resume"
                        };
                        if action == Action::WorkshopInput {
                            forward = Some((p.text.clone(), p.source.clone(), p.generation));
                        } else {
                            let verse = crate::workshop_edit::reading(
                                w.pending.as_ref().map_or(&w.current_lines, |p| &p.lines),
                            );
                            let mut speech = Speech::new(
                                "workshop_direct_resume",
                                format!(
                                    "{}\n{verse}",
                                    if clear {
                                        "戻ろか。中断してた句はこれやで。"
                                    } else {
                                        "敵はまだ見えとるけど、今は近づいてきてへん。句は戻すで。"
                                    }
                                ),
                            );
                            speech.scope = Scope::WorkshopReply {
                                id: w.hud_id.clone(),
                                version: w.version,
                            };
                            feedback = Some(speech);
                        }
                    }
                    Action::ResumeWorkshop | Action::WorkshopInput => {
                        feedback = Some(Speech::new(
                            "workshop_resume_blocked",
                            "まだ近づかれるかもしれん。句はしまっとくで。",
                        ));
                        outcome = "threat_not_stable";
                    }
                    Action::Unrelated if clear && quiet => {
                        forward = Some((p.text.clone(), p.source.clone(), p.generation));
                        outcome = "ordinary_chat";
                    }
                    _ => {}
                }
            } else {
                outcome = "stale_input";
            }
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = if valid { "quiet" } else { "cancelled" }.into();
                row["combat_input_result"] = result;
                row["combat_input_outcome"] = outcome.into();
            }
            if let Some(speech) = feedback {
                self.combat_workshop_feedback(&mut d, &mut jobs, &sid, &p.text, speech);
            }
            d.revision += 1;
            tracing::info!(
                event = "workshop_combat_input",
                session_id = sid,
                ?action,
                outcome
            );
            forward
        };
        if let Some((text, source, generation)) = forward {
            let reply = self.submit_inner(
                Some(&sid),
                &text,
                &source,
                true,
                Some((generation, None)),
                true,
            );
            let mut d = self.data.lock().unwrap();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["forwarded_input"] = reply;
            }
        }
    }
}
