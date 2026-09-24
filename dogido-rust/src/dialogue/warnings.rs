use super::{Data, Dialogue, bridge, id, recent_observation};
use crate::{events::GameEvent, threats::Warning};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::sync::watch;

pub(super) struct Active {
    pub turn: String,
    pub plan: Warning,
    pub cancel: watch::Sender<bool>,
}

pub(super) fn interruption_reason(e: &GameEvent) -> Option<&'static str> {
    if !e.visual_threats.is_empty() {
        Some("visual_hostile")
    } else if !e.auditory_threats.is_empty() {
        Some("auditory_hostile")
    } else if e.combat.recent_damage_ms.is_some_and(|ms| ms < 8000) {
        Some("recent_damage")
    } else if e.combat.combat_active_hint == Some(true) {
        Some("combat_active")
    } else if e.world.danger_darkness_score.is_some_and(|v| v >= 0.72) {
        Some("danger_darkness")
    } else {
        None
    }
}

impl Dialogue {
    pub(super) fn cancel_chat(d: &mut Data, sid: &str, reason: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        if let Some(c) = s.cancel.take() {
            s.epoch += 1;
            s.status = "cancelled".into();
            let _ = c.send(true);
            let turn = s.current_turn.clone();
            Self::cancel_row(d, sid, &turn, reason);
        }
    }
    pub(super) fn cancel_warning(d: &mut Data, sid: &str, reason: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        s.pending_warning = None;
        if let Some(w) = s.warning.take() {
            let _ = w.cancel.send(true);
            s.status = "cancelled".into();
            Self::cancel_row(d, sid, &w.turn, reason);
        }
    }
    fn cancel_row(d: &mut Data, sid: &str, turn: &str, reason: &str) {
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["cancel_reason"] = reason.into();
            row["playback_status"] = "cancelled".into();
            row["cancelled_at"] = chrono::Utc::now().to_rfc3339().into();
        }
        d.revision += 1;
        tracing::info!(
            event = "audio_interrupt",
            session_id = sid,
            turn_id = turn,
            reason
        );
    }
    pub(super) fn queue_warning(
        d: &mut Data,
        sid: &str,
        plan: &Warning,
    ) -> (String, watch::Receiver<bool>) {
        let turn = id("warning");
        let (cancel, rx) = watch::channel(false);
        let s = d.sessions.get_mut(sid).unwrap();
        s.warning = Some(Active {
            turn: turn.clone(),
            plan: plan.clone(),
            cancel,
        });
        s.status = "queued".into();
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        d.rows.push_back(
            json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,
            "category":"callout","source":"game_observation","text":plan.display_text(),
            "created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"both",
            "playback_status":"queued","warning":plan}),
        );
        d.revision += 1;
        tracing::info!(event="warning_queued",session_id=sid,turn_id=turn,warning=%json!(plan));
        (turn, rx)
    }
    fn warning_update(&self, sid: &str, turn: &str, status: &str, error: Option<String>) -> bool {
        let mut d = self.data.lock().unwrap();
        let current = !d.stopped
            && d.sessions
                .get(sid)
                .and_then(|s| s.warning.as_ref())
                .is_some_and(|w| w.turn == turn);
        let status = if current { status } else { "cancelled" };
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
            if let Some(error) = error {
                row["error"] = Value::String(error);
            }
        }
        if current {
            let s = d.sessions.get_mut(sid).unwrap();
            s.status = status.into();
            if matches!(status, "completed" | "failed" | "cancelled") {
                s.warning = None;
            }
        }
        // 警告は普通の雑談assistant履歴へ追加しない。
        d.revision += 1;
        tracing::info!(
            event = "warning_status",
            session_id = sid,
            turn_id = turn,
            playback_status = status
        );
        current
    }
    pub(super) async fn run_warning(
        self: Arc<Self>,
        sid: String,
        turn: String,
        plan: Warning,
        mut cancel: watch::Receiver<bool>,
    ) {
        // 音声待ち中から観測失効を監視する。取消はchatとは別の所有権。
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let monitor_turn = turn.clone();
        let mut monitor_cancel = cancel.clone();
        let monitor = tokio::spawn(async move {
            loop {
                tokio::select! {
                    _=bridge::cancelled(&mut monitor_cancel)=>break,
                    _=tokio::time::sleep(Duration::from_millis(100))=>{
                        let mut d=owner.data.lock().unwrap();
                        let stale=d.sessions.get(&monitor_sid).is_some_and(|s|
                            s.warning.as_ref().is_some_and(|w|w.turn==monitor_turn)
                            && !(s.received.is_some_and(|at|at.elapsed()<=Duration::from_secs(10))
                                && s.latest.as_ref().is_some_and(recent_observation)));
                        if stale {Self::cancel_warning(&mut d,&monitor_sid,"stale_observation");break;}
                    }
                }
            }
        });
        let result=async {
            let _permit=tokio::select! {biased; _=bridge::cancelled(&mut cancel)=>anyhow::bail!("cancelled"),p=self.serial.acquire()=>p.unwrap()};
            let mut config=self.config.clone();
            config.speed=config.warnings.battle_speed;
            let began=AtomicBool::new(false);
            let started=||{if !began.swap(true,Ordering::SeqCst) {self.warning_update(&sid,&turn,"started",None);}};
            if let Some(cue)=&plan.cue {
                let path=config.warnings.cue_dir.join(cue.file);
                if path.is_file() {
                    self.audio.play_file(&config,&path,&mut cancel,0,&started).await?;
                } else {
                    tracing::warn!(event="warning_cue_fallback",cue_id=cue.id,reason="file_missing");
                    self.audio.speak(&config,cue.text,&mut cancel,&started).await?;
                }
            }
            if !plan.text.is_empty() {self.audio.speak(&config,&plan.text,&mut cancel,started).await?;}
            Ok::<(),anyhow::Error>(())
        }.await;
        monitor.abort();
        let _ = monitor.await;
        match result {
            Ok(()) => {
                self.warning_update(&sid, &turn, "completed", None);
            }
            Err(e) => {
                self.warning_update(
                    &sid,
                    &turn,
                    if *cancel.borrow() {
                        "cancelled"
                    } else {
                        "failed"
                    },
                    Some(e.to_string()),
                );
            }
        }
    }
}
