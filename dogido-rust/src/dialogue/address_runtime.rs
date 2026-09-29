use super::{Data, Dialogue, bridge, id};
use crate::address::{self, Action, Pending, Request};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::{sync::watch, task::JoinHandle};

pub(super) struct Checked {
    generation: u64,
    original: String,
    repair: Option<String>,
    action: Action,
    general: bool,
}

pub(super) enum Input {
    Pass,
    Repair(String),
    Replay(Request),
    Consumed(Value),
}

impl Dialogue {
    /// 宛先待ちの自由文だけ既存parserへ照会する。音声再生・観測workerを止めない。
    pub(super) fn check_address_input(
        self: &Arc<Self>,
        d: &mut Data,
        jobs: &mut Vec<JoinHandle<()>>,
        sid: &str,
        text: &str,
        source: &str,
    ) -> Option<Value> {
        self.tick_address(d, sid);
        let s = d.sessions.get_mut(sid)?;
        if s.address_checked
            .as_ref()
            .is_some_and(|c| c.generation == s.input_generation)
        {
            return None;
        }
        s.address_checked = None;
        let pending = s.address.as_ref()?;
        let action = pending.input(text);
        if action == Action::Pass || address::plain_control(text) {
            return None;
        }
        let checked = Checked {
            generation: s.input_generation,
            original: pending.original.turn.clone(),
            repair: pending.repair.as_ref().map(|r| r.0.clone()),
            action,
            general: false,
        };
        let epoch = s.epoch;
        let routing = id("address_route");
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":routing,"session_id":sid,
            "player_input_text":text,"source":source,"text":"","category":"routing",
            "created_at":chrono::Utc::now(),"playback_status":"routing"}),
        );
        d.revision += 1;
        let this = self.clone();
        let (sid, text, source, turn) = (
            sid.to_owned(),
            text.to_owned(),
            source.to_owned(),
            routing.clone(),
        );
        jobs.push(tokio::spawn(async move {
            this.run_address_check(sid, text, source, turn, epoch, checked)
                .await;
        }));
        Some(
            json!({"accepted":true,"queued":true,"turn_id":routing,"reason":"address_input_routing"}),
        )
    }
    async fn run_address_check(
        self: Arc<Self>,
        sid: String,
        text: String,
        source: String,
        turn: String,
        epoch: u64,
        mut checked: Checked,
    ) {
        let (cancel, mut rx) = watch::channel(false);
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let generation = checked.generation;
        let monitor = tokio::spawn(async move {
            let started = Instant::now();
            loop {
                tokio::time::sleep(Duration::from_millis(25)).await;
                let d = owner.data.lock().unwrap();
                if started.elapsed() >= Duration::from_secs(3)
                    || d.stopped
                    || d.sessions
                        .get(&monitor_sid)
                        .is_none_or(|s| s.epoch != epoch || s.input_generation != generation)
                {
                    let _ = cancel.send(true);
                    break;
                }
            }
        });
        let result = bridge::render(
            &self.config,
            &self.llm,
            json!({"op":"address_route","text":text}),
            &mut rx,
        )
        .await;
        monitor.abort();
        let _ = monitor.await;
        let forward = {
            let mut d = self.data.lock().unwrap();
            let valid = !d.stopped
                && d.sessions
                    .get(&sid)
                    .is_some_and(|s| s.epoch == epoch && s.input_generation == generation);
            let general = result
                .as_ref()
                .ok()
                .and_then(|r| r["general_conversation"].as_bool());
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = if !valid {
                    "cancelled"
                } else if general.is_none() {
                    "failed"
                } else {
                    "not_selected"
                }
                .into();
                if let Err(error) = &result {
                    row["error"] = error.to_string().into();
                }
            }
            d.revision += 1;
            if valid && let Some(general) = general {
                checked.general = general;
                d.sessions.get_mut(&sid).unwrap().address_checked = Some(checked);
                true
            } else {
                false
            }
        };
        if forward {
            let response = self.submit_inner(
                Some(&sid),
                &text,
                &source,
                true,
                Some((generation, None)),
                true,
            );
            let mut d = self.data.lock().unwrap();
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["forwarded_input"] = response;
            }
            d.revision += 1;
        }
    }
    pub(super) fn cancel_address(d: &mut Data, sid: &str, resolution: &str) {
        let pending = d.sessions.get_mut(sid).and_then(|s| s.address.take());
        if let Some(p) = pending {
            if let Some(s) = d.sessions.get_mut(sid) {
                s.history.replace_unanswered(&p.original.turn);
            }
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == p.original.turn) {
                row["routing_status"] = if resolution == "expired_unaddressed" {
                    "expired"
                } else {
                    "cancelled"
                }
                .into();
                row["resolution"] = resolution.into();
            }
            d.revision += 1;
            tracing::info!(
                event = "address_resolved",
                session_id = sid,
                turn_id = p.original.turn,
                resolution
            );
        }
    }
    pub(super) fn tick_address(&self, d: &mut Data, sid: &str) {
        if d.sessions.get(sid).is_some_and(|s| s.haiku.foreground()) {
            Self::cancel_address(d, sid, "attention_interrupted");
        }
        if d.sessions
            .get(sid)
            .and_then(|s| s.address.as_ref())
            .is_some_and(|p| p.expired(self.clock.elapsed().as_millis() as u64))
        {
            Self::cancel_address(d, sid, "expired_unaddressed");
        }
    }
    /// 生成開始前のactivityで判定する。モデルの待ち時間を「無会話2分」へ足さない。
    pub(super) fn hold_language_handoff(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        input: &Value,
        outcome: &Value,
    ) -> Result<bool> {
        let mut d = self.data.lock().unwrap();
        ensure!(
            !d.stopped && d.sessions.get(sid).is_some_and(|s| s.epoch == epoch),
            "stale language handoff"
        );
        let text = input["text"].as_str().unwrap_or("");
        let submitted = input["input_at_ms"].as_u64().unwrap_or(0);
        if !address::should_hold(
            text,
            &outcome["interpretation"],
            submitted,
            input["previous_activity_ms"].as_u64(),
            self.config.combat.ms("conversation_topic_fresh_ms"),
        ) {
            return Ok(false);
        }
        Self::cancel_address(&mut d, sid, "replaced_unaddressed");
        let s = d.sessions.get_mut(sid).unwrap();
        s.history.replace_unanswered(turn);
        s.address = Some(Pending::new(
            Request {
                turn: turn.into(),
                text: text.into(),
                source: input["source"].as_str().unwrap_or("text").into(),
                input_at: submitted,
            },
            self.config.combat.ms("conversation_pending_address_ttl_ms"),
        ));
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["routing_status"] = "awaiting_address".into();
        }
        self.tick_address(&mut d, sid);
        d.revision += 1;
        tracing::info!(event = "address_held", session_id = sid, turn_id = turn);
        Ok(true)
    }
    /// 確認前の相槌は音声を止めずに消費する。再送は正本入力をtakeして一度だけ。
    pub(super) fn address_input(&self, d: &mut Data, sid: &str, text: &str, source: &str) -> Input {
        self.tick_address(d, sid);
        let checked = d.sessions.get_mut(sid).and_then(|s| {
            s.address_checked
                .take()
                .filter(|c| c.generation == s.input_generation)
        });
        if checked.as_ref().is_some_and(|c| !c.general) {
            return Input::Pass;
        }
        let Some(pending) = d.sessions.get(sid).and_then(|s| s.address.as_ref()) else {
            return Input::Pass;
        };
        let action = match checked {
            Some(c)
                if c.original == pending.original.turn
                    && c.repair == pending.repair.as_ref().map(|r| r.0.clone()) =>
            {
                c.action
            }
            Some(_) => {
                return Input::Consumed(
                    json!({"accepted":false,"reason":"superseded_address_input"}),
                );
            }
            None => pending.input(text),
        };
        match action {
            Action::Pass => return Input::Pass,
            Action::Ask(text) => return Input::Repair(text),
            _ => (),
        }
        let confirmation = id("address_confirmation");
        let resolution = match &action {
            Action::Accept => "address_confirmed",
            Action::Decline => "address_declined",
            Action::Wait(reason) => reason,
            _ => unreachable!(),
        };
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"), "turn_id":confirmation, "session_id":sid,
            "category":"learning", "text":"", "player_input_text":text,"source":source,
            "created_at":chrono::Utc::now(), "playback_status":"not_selected",
            "routing_status":"address_confirmation", "resolution":resolution}),
        );
        d.revision += 1;
        match action {
            Action::Accept => {
                let original = d
                    .sessions
                    .get_mut(sid)
                    .unwrap()
                    .address
                    .take()
                    .unwrap()
                    .original;
                if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == original.turn) {
                    row["routing_status"] = "host_chat_confirmed".into();
                    Input::Replay(original)
                } else {
                    // 表示台帳の上限を越えるほど長い保留を復活させない。
                    Input::Consumed(json!({"accepted":false,"reason":"held_turn_expired"}))
                }
            }
            Action::Decline => {
                Self::cancel_address(d, sid, "declined_unaddressed");
                Input::Consumed(
                    json!({"accepted":true,"session_id":sid,"turn_id":confirmation,"reason":resolution}),
                )
            }
            _ => Input::Consumed(
                json!({"accepted":true,"session_id":sid,"turn_id":confirmation,"reason":resolution}),
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dialogue::DialogueConfig;

    fn held() -> std::sync::Arc<Dialogue> {
        let dialogue = Dialogue::new(DialogueConfig::default()).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.rows.push_back(json!({"session_id":"s","turn_id":"original","epoch":0,"playback_status":"not_selected"}));
            d.sessions
                .get_mut("s")
                .unwrap()
                .history
                .push("original", "user", "家を作りたい");
        }
        assert!(dialogue.hold_language_handoff("s", "original", 0,
            &json!({"text":"家を作りたい","input_at_ms":0,"previous_activity_ms":0,"source":"voice"}), &json!({})).unwrap());
        dialogue
    }
    #[tokio::test]
    async fn cancelled_generation_and_playback_cannot_restore_pending_or_overwrite_replay() {
        let dialogue = held();
        {
            let mut d = dialogue.data.lock().unwrap();
            assert!(d.sessions["s"].history.rows().is_empty());
            Dialogue::cancel_address(&mut d, "s", "attention_interrupted");
            d.sessions.get_mut("s").unwrap().epoch = 2;
            d.rows[0]["epoch"] = 2.into();
            d.rows[0]["playback_status"] = "generating".into();
        }
        assert!(
            dialogue
                .hold_language_handoff("s", "original", 0, &json!({}), &json!({}))
                .is_err()
        );
        assert!(!dialogue.update(
            "s",
            "original",
            0,
            "completed",
            Some(&json!({"text":"古い返答"}))
        ));
        {
            let d = dialogue.data.lock().unwrap();
            assert!(d.sessions["s"].address.is_none());
            assert!(d.sessions["s"].history.rows().is_empty());
            assert_eq!(d.rows[0]["playback_status"], "generating");
        }
        dialogue.shutdown().await;
    }
    #[tokio::test]
    async fn exact_expiry_and_shutdown_remove_pending_even_without_active_job() {
        let dialogue = held();
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions
                .get_mut("s")
                .unwrap()
                .address
                .as_mut()
                .unwrap()
                .expires_at = 0;
            assert!(matches!(
                dialogue.address_input(&mut d, "s", "ドギド", "voice"),
                Input::Pass
            ));
            assert_eq!(d.rows[0]["resolution"], "expired_unaddressed");
        }
        dialogue.shutdown().await;
        let dialogue = held();
        dialogue.shutdown().await;
        let d = dialogue.data.lock().unwrap();
        assert!(d.sessions["s"].address.is_none());
        assert_eq!(d.rows[0]["resolution"], "attention_interrupted");
    }
    #[tokio::test]
    async fn full_queue_finishes_original_without_polluting_history() {
        let dialogue = held();
        dialogue
            .data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .address
            .as_mut()
            .unwrap()
            .repair = Some(("repair".into(), true));
        for _ in 0..16 {
            dialogue
                .jobs
                .lock()
                .unwrap()
                .push(tokio::spawn(std::future::pending()));
        }
        let response = dialogue.submit(Some("s"), "うん", "voice");
        assert_eq!(response["accepted"], false);
        {
            let d = dialogue.data.lock().unwrap();
            assert_eq!(d.rows[0]["resolution"], "host_chat_queue_full");
            assert!(d.sessions["s"].address.is_none());
            assert!(d.sessions["s"].history.rows().is_empty());
        }
        for job in dialogue.jobs.lock().unwrap().iter() {
            job.abort();
        }
        dialogue.shutdown().await;
    }
    #[tokio::test]
    async fn parsing_does_not_turn_an_early_confirmation_into_a_late_acceptance() {
        let dialogue = held();
        {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("s").unwrap();
            s.address.as_mut().unwrap().repair = Some(("repair".into(), true));
            s.address_checked = Some(Checked {
                generation: 0,
                original: "original".into(),
                repair: Some("repair".into()),
                action: Action::Wait("confirmation_before_repair_completed"),
                general: true,
            });
            assert!(matches!(
                dialogue.address_input(&mut d, "s", "はいと思うけど", "voice"),
                Input::Consumed(_)
            ));
            assert_eq!(
                d.rows.back().unwrap()["resolution"],
                "confirmation_before_repair_completed"
            );
            assert!(d.sessions["s"].address.is_some());
            let s = d.sessions.get_mut("s").unwrap();
            s.address_checked = Some(Checked {
                generation: 0,
                original: "original".into(),
                repair: Some("repair".into()),
                action: Action::Wait("ambiguous_confirmation"),
                general: false,
            });
            assert!(matches!(
                dialogue.address_input(&mut d, "s", "枕詞って何？", "voice"),
                Input::Pass
            ));
            assert!(d.sessions["s"].address.is_some());
        }
        dialogue.shutdown().await;
    }
    #[tokio::test]
    async fn native_address_routing_finishes_without_helper_and_shutdown_clears_pending() {
        let mut config = DialogueConfig {
            python: "/missing/address-python".into(),
            audio_enabled: false,
            ..DialogueConfig::default()
        };
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "試験", true);
        {
            let mut d = dialogue.data.lock().unwrap();
            d.sessions.get_mut("s").unwrap().address = Some(Pending::new(
                Request {
                    turn: "held".into(),
                    text: "家の話".into(),
                    source: "voice".into(),
                    input_at: 0,
                },
                300000,
            ));
            d.sessions
                .get_mut("s")
                .unwrap()
                .address
                .as_mut()
                .unwrap()
                .repair = Some(("repair".into(), true));
        }
        let response = dialogue.submit(Some("s"), "枕詞って何？", "voice");
        assert_eq!(response["reason"], "address_input_routing");
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                let finished = {
                    let d = dialogue.data.lock().unwrap();
                    d.rows.iter().any(|row| {
                        row["turn_id"] == response["turn_id"]
                            && row["playback_status"] == "not_selected"
                            && row["forwarded_input"].is_object()
                    })
                };
                if finished {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        tokio::time::timeout(Duration::from_secs(2), dialogue.shutdown())
            .await
            .unwrap();
        assert!(
            dialogue.data.lock().unwrap().sessions["s"]
                .address
                .is_none()
        );
    }
}
