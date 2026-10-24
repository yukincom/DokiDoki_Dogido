//! 中断中の入力を保持し、現在観測と既存の警戒解除後に同じ受付へ戻す。
use super::*;
use crate::{
    combat::model::{Delivery, Speech},
    workshop_combat_input::Action,
};

pub(super) struct Pending {
    pub turn: String,
    pub text: String,
    pub request: super::reply_queue::Request,
    audit: super::workshop_record::Attempt,
    instance: String,
    generation: u64,
    workshop_id: String,
    version: u64,
    pub cancel: watch::Sender<bool>,
}

pub(super) fn allowed(s: &Session) -> bool {
    fresh(s) || super::workshop_focus::owns_input(s)
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

    pub(super) fn hold_paused_input(
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
        // FIFOから渡された一件は既に予約済み。それ以外は解除後の戻り先も確保する。
        let original = s
            .reply_checked
            .as_ref()
            .and_then(|c| c.original.as_ref())
            .filter(|r| r.text == text && r.source == source);
        if original.is_none() && super::reply_queue::occupied_slots(s) >= 9 {
            return Some(json!({"accepted":false,"reason":"reply_queue_full"}));
        }
        let turn = id("combat_input");
        let attempt = super::workshop_record::Attempt::new(
            super::workshop_record::state(Some(w)),
            super::workshop_record::Input {
                raw: text.into(),
                semantic: None,
                private: s
                    .record_private_generation
                    .is_some_and(|(g, p)| g == s.input_generation && p),
                epoch: Some(s.epoch),
            },
        );
        // 行3種・具体変更3種・終了はBERTへ変更済み。旧判定へ戻さない。
        let action = Action::from_classification(
            super::request_runtime::classification(s, text, source),
            text,
            source,
        )
        .unwrap_or_default();
        // not_target は保留する入力であり、再開意思や安全性の判定ではない。
        let result = json!({"provider":if action == Action::DeferredInput {"code"} else {"modernbert"}, "analysis":{"action":action}, "llm_reports":[]});
        let request = super::reply_queue::Request {
            turn: id("reply"),
            text: text.into(),
            source: source.into(),
            input_at: original
                .map_or_else(|| self.clock.elapsed().as_millis() as u64, |r| r.input_at),
            record_private: attempt.input.private,
            classification: super::request_runtime::classification(s, text, source).cloned(),
            binding: s
                .request_classification
                .binding(s.input_generation, text, source)
                .cloned()
                .or_else(|| super::request_runtime::Binding::capture(s)),
        };
        let (cancel, rx) = watch::channel(false);
        s.combat_input = Some(Pending {
            turn: turn.clone(),
            text: text.into(),
            request,
            audit: super::workshop_record::Attempt::new(
                attempt.before.clone(),
                attempt.input.clone(),
            ),
            instance: s.instance_id.clone(),
            generation: s.input_generation,
            workshop_id: w.hud_id.clone(),
            version: w.version,
            cancel,
        });
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,
            "category":"workshop_input","player_input_text":text,"source":source,"text":"",
            "created_at":chrono::Utc::now(),"epoch":attempt.input.epoch,
            "workshop_record_private":attempt.input.private,"reference_ids":[],"output_mode":"text",
            "playback_status":"queued","combat_input_result":result,"combat_input_outcome":"held"}),
        );
        d.revision += 1;
        let this = self.clone();
        let session = sid.to_owned();
        let tid = turn.clone();
        jobs.push(tokio::spawn(async move {
            this.clone()
                .run_combat_input(session.clone(), tid.clone(), action, rx)
                .await;
            this.record_workshop_turn(&session, &tid, &attempt);
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
        action: Action,
        mut cancel: watch::Receiver<bool>,
    ) {
        // 再開・継続は敵の現在観測と警戒解除条件でコードが決定できるため、AI分類を撤去。旧判定へ戻さない。
        // 句・版・入力世代を保持し、警告の配送中は音声permitを使わず待つ。
        let forwarded = loop {
            if *cancel.borrow() {
                return;
            }
            let completed = {
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
                let Some(p) = s.combat_input.as_ref().filter(|p| p.turn == turn) else {
                    return;
                };
                let valid = s.instance_id == p.instance
                    && s.input_generation == p.generation
                    && s.haiku.workshop.as_ref().is_some_and(|w| {
                        w.open
                            && w.combat_paused()
                            && w.hud_id == p.workshop_id
                            && w.version == p.version
                    });
                if !valid {
                    Self::cancel_combat_input(&mut d, &sid);
                    return;
                }
                let quiet = s.warning.is_none() && s.pending_warning.is_none();
                let ready = observation_fresh(s)
                    && quiet
                    && (action == Action::CloseWorkshop
                        || super::workshop_combat_runtime::clear_for_resume(s));
                if !ready {
                    None
                } else {
                    let p = s.combat_input.take().unwrap();
                    let w = s.haiku.workshop.as_mut().unwrap();
                    let mut feedback = None;
                    let (forward, outcome) = if action == Action::CloseWorkshop {
                        feedback = Some(Speech::new(
                            "workshop_close_feedback",
                            if w.pending.is_some() {
                                "未採用の案があるで。採用するか、元の句に戻すか教えてな。"
                            } else {
                                w.close("combat_interrupted_close");
                                "おけ、句はここまでにしよか。"
                            },
                        ));
                        (None, "close_checked")
                    } else {
                        w.resume(Instant::now());
                        w.recovery = crate::workshop_combat::Recovery::default();
                        w.drift_count = 0;
                        (Some((p.request, p.audit)), "resumed")
                    };
                    // 受理・再試行・接続個体の照合は既存FIFOへ一度だけ渡す。
                    // takeとこの移譲は同じロック内なので、取消所有者が不在の窓を作らない。
                    let response = forward.map(|(request, audit)| {
                        Self::queue_combat_reply(&mut d, &sid, request, audit)
                    });
                    if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                        row["playback_status"] = "quiet".into();
                        row["combat_input_outcome"] = outcome.into();
                        if let Some(response) = &response {
                            row["forwarded_input"] = response.clone();
                        }
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
                    Some(response.is_some())
                }
            };
            if let Some(forwarded) = completed {
                break forwarded;
            }
            tokio::select! {
                _ = bridge::cancelled(&mut cancel) => return,
                _ = tokio::time::sleep(Duration::from_millis(200)) => {}
            }
        };
        if forwarded {
            self.resume_reply_input(&sid);
        }
    }
}

#[cfg(test)]
#[path = "workshop_combat_input_tests.rs"]
mod tests;
