//! 一つの戦闘判断から選ばれた音声を、同じ取消所有権で順に配送する。
use super::{Data, Dialogue, Session, bridge, id, observation_fresh};
use super::{PlaybackStatus, workshop_combat_runtime::NoticeEnd};
use crate::{
    combat::model::{Delivery, Scope, Speech},
    events::GameEvent,
    threats::Warning,
};
use serde_json::{Value, json};
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::watch;

pub(super) struct Active {
    pub turn: String,
    pub actions: Vec<Speech>,
    pub cancel: watch::Sender<bool>,
    pub started: bool,
    pub input: Option<String>,
    pub protected_until: Option<Instant>,
}
impl Active {
    pub fn finishing(&self) -> bool {
        self.started && self.actions.iter().all(can_finish)
    }
    pub fn protected(&self) -> bool {
        self.finishing() || self.protected_until.is_some_and(|t| Instant::now() < t)
    }
}

// 通常の観測変化では、悲鳴・断片・本文を含む一つの台詞を最後まで配送する。
// 呼吸ループと、句の版・戦闘pauseに結び付く再開確認は途中でも失効させる。
fn can_finish(action: &Speech) -> bool {
    action.cue_id != Some("suppressed_breath")
        && action.kind != "dark_push_stop"
        && !matches!(
            action.scope,
            Scope::Workshop { .. } | Scope::WorkshopReply { .. }
        )
}

pub(super) fn active_applicable(
    active: &Active,
    session: &Session,
    settings: &crate::threats::Settings,
    environment_settings: &crate::combat::model::Settings,
) -> bool {
    active.actions.iter().all(|action| {
        // 実際の敵に対して開始した警告は、その後の対象消失だけでは切らない。
        if workshop_blocked(action, session)
            && !(active.started && action.delivery == Delivery::Combat)
        {
            return false;
        }
        if !active.started || !can_finish(action) {
            return applicable(action, session, settings, environment_settings);
        }
        // 再生開始後の対象消失・方向・個数・明るさ等は、発話の途中停止にしない。
        // 観測自体の失効、平時の台詞中の敵・被弾は従来どおり停止する。
        let fresh = if matches!(action.scope, Scope::Auditory(_)) {
            session
                .audio_received
                .is_some_and(|at| at.elapsed() <= Duration::from_secs(10))
        } else {
            observation_fresh(session)
        };
        fresh
            && (applicable(action, session, settings, environment_settings)
                || !(matches!(
                    action.delivery,
                    Delivery::Ambient | Delivery::UrgentEnvironment
                ) || matches!(action.scope, Scope::Safe))
                || session
                    .latest
                    .as_ref()
                    .is_some_and(|e| interruption_reason(e).is_none())
                    && session
                        .audio_latest
                        .as_ref()
                        .is_none_or(|e| interruption_reason(e).is_none()))
    })
}

fn workshop_blocked(action: &Speech, session: &Session) -> bool {
    super::workshop_focus::active(session)
        && (matches!(
            action.delivery,
            Delivery::Ambient | Delivery::UrgentEnvironment
        ) || action.kind == "smell"
            || super::workshop_focus::quiet(session)
                && action.delivery == Delivery::Combat
                && !super::workshop_focus::combat_completion(action.kind))
}

pub(super) fn interruption_reason(e: &GameEvent) -> Option<&'static str> {
    if !e.visual_threats.is_empty() {
        Some("visual_hostile")
    } else if !e.auditory_threats.is_empty() {
        Some("auditory_hostile")
    } else if e.combat.recent_damage_ms.is_some_and(|ms| ms < 3000) {
        Some("recent_damage")
    } else if e.combat.combat_active_hint == Some(true) {
        Some("combat_active")
    } else {
        None
    }
}
#[cfg(test)]
pub(super) fn from_warning(plan: Warning) -> Speech {
    let mut s = Speech::visual(
        plan.kind,
        plan.text.clone(),
        if plan.group_counts.is_empty() {
            vec![plan.target.clone()]
        } else {
            plan.group_support.clone()
        },
    );
    s.visual_plan = Some(plan);
    s
}
fn audio_key(a: &crate::events::AuditoryThreat) -> String {
    a.source_id
        .clone()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            serde_json::to_value(a.direction.horizontal)
                .unwrap()
                .as_str()
                .unwrap_or("nearby")
                .to_owned()
        })
}
pub(super) fn applicable(
    action: &Speech,
    session: &Session,
    settings: &crate::threats::Settings,
    environment_settings: &crate::combat::model::Settings,
) -> bool {
    if workshop_blocked(action, session) {
        return false;
    }
    if matches!(
        action.delivery,
        Delivery::Ambient | Delivery::UrgentEnvironment
    ) || action.kind == "smell"
    {
        if !observation_fresh(session) {
            return false;
        }
        let Some(event) = session
            .environment_latest
            .as_ref()
            .or(session.latest.as_ref())
        else {
            return false;
        };
        if !session
            .danger
            .still_applicable(action, event, environment_settings)
            || !crate::environment::ambient::still_applicable(action, event)
        {
            return false;
        }
        if action.kind == "light_source_gain" {
            let light = session.danger.light_context(event, environment_settings);
            if light.severe_darkness {
                return false;
            }
        }
    }
    if let Some(plan) = &action.visual_plan {
        return observation_fresh(session)
            && session
                .latest
                .as_ref()
                .is_some_and(|e| plan.applicable(e, settings));
    }
    match &action.scope {
        Scope::WorkshopReply { id, version } => {
            super::workshop_combat_input::allowed(session)
                && session.haiku.workshop.as_ref().is_some_and(|w| {
                    w.open && !w.combat_paused() && w.hud_id == *id && w.version == *version
                })
        }
        Scope::Workshop { id, version } => {
            super::workshop_combat_runtime::applicable(session, id, *version)
        }
        Scope::Event => true,
        Scope::Safe => {
            observation_fresh(session)
                && (matches!(
                    session.mode,
                    crate::combat::model::Mode::Normal | crate::combat::model::Mode::Aftermath
                ) || session.mode == crate::combat::model::Mode::Alert
                    && (action.delivery == Delivery::Ambient || action.kind == "smell"))
                && session
                    .latest
                    .as_ref()
                    .is_some_and(|e| e.visual_threats.is_empty() && e.auditory_threats.is_empty())
                && session
                    .audio_latest
                    .as_ref()
                    .is_none_or(|e| e.auditory_threats.is_empty())
        }
        Scope::Visual(ids) => {
            observation_fresh(session)
                && session.latest.as_ref().is_some_and(|e| {
                    ids.iter().all(|id| {
                        e.visual_threats
                            .iter()
                            .any(|t| crate::threats::identity(t) == *id)
                    })
                })
        }
        Scope::Auditory(ids) => {
            session
                .audio_received
                .is_some_and(|at| at.elapsed() <= Duration::from_secs(10))
                && session.audio_latest.as_ref().is_some_and(|e| {
                    ids.iter().all(|id| {
                        e.auditory_threats
                            .iter()
                            .any(|a| audio_key(a) == *id || a.source_id.as_ref() == Some(id))
                    })
                })
        }
    }
}
pub(super) fn refresh(
    actions: &[Speech],
    session: &Session,
    settings: &crate::threats::Settings,
    environment_settings: &crate::combat::model::Settings,
    started: bool,
) -> Option<Vec<Speech>> {
    actions
        .iter()
        .map(|action| {
            if let Some(plan) = &action.visual_plan {
                if !observation_fresh(session) {
                    return None;
                }
                let next = plan.relocated(session.latest.as_ref()?, settings, started)?;
                let mut speech = action.clone();
                speech.text = next.text.clone();
                speech.visual_plan = Some(next);
                Some(speech)
            } else if applicable(action, session, settings, environment_settings) {
                Some(action.clone())
            } else {
                None
            }
        })
        .collect()
}
fn display_text(actions: &[Speech]) -> String {
    actions
        .iter()
        .map(|a| {
            a.visual_plan
                .as_ref()
                .map(Warning::display_text)
                .unwrap_or_else(|| a.text.clone())
        })
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}
fn cue_path(directory: &Path, cue: &str) -> Option<PathBuf> {
    let mapped = match cue {
        "spot_hostile_gasp" | "boss_reveal_scream" => {
            "panic/freesound_community-male-gasp-1-7183.mp3"
        }
        "panic_scream_start"
        | "front_spawn_scream"
        | "ushiro_scream"
        | "panic_multi"
        | "panic_generic"
        | "panic_creeper"
        | "panic_zombie"
        | "panic_skeleton"
        | "panic_spider"
        | "panic_witch"
        | "panic_enderman"
        | "warden_sonic_boom_scream" => "panic/universfield-man-scream-08-352438.mp3",
        "suppressed_gasp" => "panic/universfield-funny-dramatic-gasp-320975.mp3",
        "suppressed_breath" => "panic/freesound_community-heavy-breath-male-63980.mp3",
        "aftermath_relief" => "aftermath.mp3",
        _ => "",
    };
    if !mapped.is_empty() {
        let path = directory.join(mapped);
        if path.is_file() {
            return Some(path);
        }
    }
    // IDs come from the closed catalog; reject traversal even for a bad manifest.
    if Path::new(cue)
        .components()
        .any(|p| !matches!(p, std::path::Component::Normal(_)))
    {
        return None;
    }
    ["mp3", "wav", "m4a"]
        .iter()
        .map(|ext| directory.join(format!("{cue}.{ext}")))
        .find(|p| p.is_file())
}
fn named_paths(directory: &Path, text: &str) -> Option<Vec<PathBuf>> {
    let name = text.strip_suffix("うしろ！うしろ〜！")?.trim();
    let names = directory.join("player_names");
    let manifest: Value = std::fs::read(names.join("manifest.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(Value::Null);
    let mapped = manifest
        .get("call_name_to_file")
        .or_else(|| manifest.get("names"))
        .and_then(|v| v.get(name))
        .and_then(Value::as_str)
        .and_then(|v| Path::new(v).file_name())
        .map(|v| names.join(v));
    let safe = name
        .chars()
        .map(|c| {
            if c.is_whitespace() || "\\/:*?\"<>|".contains(c) {
                '_'
            } else {
                c
            }
        })
        .collect::<String>();
    let safe = safe
        .trim_matches(['.', '_'])
        .chars()
        .take(64)
        .collect::<String>();
    let direct = (!name.contains(['/', '\\'])).then(|| names.join(format!("{name}.mp3")));
    let path = [mapped, direct, Some(names.join(format!("{safe}.mp3")))]
        .into_iter()
        .flatten()
        .find(|p| p.is_file())?;
    let tail = names.join("ushiro_tail.mp3");
    tail.is_file().then_some(vec![path, tail])
}
impl Dialogue {
    fn remember_environment_outcome(d: &mut Data, turn: &str, result: Option<&Value>) {
        let Some(result) = result else {
            return;
        };
        let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) else {
            return;
        };
        for key in ["environment_reaction_outcome", "reaction_leaf_outcome"] {
            if let Some(outcome) = result.get(key) {
                row[key] = outcome.clone();
                if !row["model_reaction_outcomes"].is_array() {
                    row["model_reaction_outcomes"] = json!([]);
                }
                row["model_reaction_outcomes"]
                    .as_array_mut()
                    .unwrap()
                    .push(outcome.clone());
            }
        }
        if let Some(revision) = result.get("observation_revision") {
            row["observation_revision"] = revision.clone();
        }
    }

    pub(super) fn cancel_chat(d: &mut Data, sid: &str, reason: &str) {
        Self::cancel_web(d, sid, reason);
        if reason != "new_player_input" {
            Self::cancel_address(d, sid, "attention_interrupted");
        }
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        if let Some(c) = s.cancel.take() {
            s.epoch += 1;
            s.status = PlaybackStatus::Cancelled;
            let _ = c.send(true);
            let turn = s.current_turn.clone();
            if let Some(p) = s.address.as_mut() {
                p.playback(&turn, PlaybackStatus::Cancelled);
            }
            Self::cancel_row(d, sid, &turn, reason);
        }
    }
    pub(super) fn cancel_warning(d: &mut Data, sid: &str, reason: &str) {
        let Some(s) = d.sessions.get_mut(sid) else {
            return;
        };
        s.pending_warning = None;
        s.pending_input = None;
        if let Some(w) = s.warning.take() {
            if !w.started
                && matches!(
                    reason,
                    "stale_observation" | "stale_environment_reaction" | "target_changed_or_gone"
                )
                && observation_fresh(s)
                && !d.rows.iter().any(|r| {
                    r["turn_id"] == w.turn && r["environment_reaction_outcome"] == "silent"
                })
                && let Some(event) = s.environment_latest.as_ref().or(s.latest.as_ref())
                && interruption_reason(event).is_none()
                && w.actions
                    .iter()
                    .any(|a| crate::environment::reaction::only_smell_spatial_changed(a, event))
            {
                s.ambient.reconsider_smell_spatial(event);
            }
            for a in &w.actions {
                if let Scope::Workshop { id, version } = &a.scope {
                    let end = if matches!(reason, "new_player_input" | "manual_interrupt") {
                        NoticeEnd::Consumed
                    } else {
                        NoticeEnd::Retry
                    };
                    super::workshop_combat_runtime::finish_notice(s, id, *version, end);
                }
            }
            let _ = w.cancel.send(true);
            s.status = PlaybackStatus::Cancelled;
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
    pub(super) fn queue_actions(
        d: &mut Data,
        sid: &str,
        actions: &[Speech],
        input: Option<&str>,
    ) -> (String, watch::Receiver<bool>) {
        let turn = id("warning");
        let (cancel, rx) = watch::channel(false);
        let s = d.sessions.get_mut(sid).unwrap();
        if !actions.iter().all(crate::reaction_leaf::is_model_reaction) {
            s.haiku.last_activity = Instant::now();
        }
        s.warning = Some(Active {
            turn: turn.clone(),
            actions: actions.to_vec(),
            cancel,
            started: false,
            input: input.map(str::to_owned),
            protected_until: None,
        });
        s.status = PlaybackStatus::Queued;
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        let plan = actions.iter().find_map(|a| a.visual_plan.as_ref());
        let category = if actions.iter().any(|a| a.delivery == Delivery::Combat) {
            "callout"
        } else {
            "speech"
        };
        let mut pending_display = actions.to_vec();
        for action in &mut pending_display {
            if crate::reaction_leaf::is_model_reaction(action) {
                action.text.clear();
            }
        }
        d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,"category":category,
            "source":if input.is_some(){"player_input"}else{"game_observation"},"player_input_text":input,
            "text":display_text(&pending_display),"created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"both",
            "playback_status":"queued","warning":plan,"combat_actions":actions}));
        d.revision += 1;
        tracing::info!(event="warning_queued",session_id=sid,turn_id=turn,actions=%json!(actions));
        (turn, rx)
    }
    fn warning_update(
        &self,
        sid: &str,
        turn: &str,
        status: PlaybackStatus,
        error: Option<String>,
    ) -> bool {
        let mut d = self.data.lock().unwrap();
        let current = !d.stopped
            && d.sessions
                .get(sid)
                .and_then(|s| s.warning.as_ref())
                .is_some_and(|w| w.turn == turn);
        let notice = d
            .sessions
            .get(sid)
            .and_then(|s| s.warning.as_ref())
            .and_then(|w| {
                w.actions.iter().find_map(|a| {
                    if let Scope::Workshop { id, version } = &a.scope {
                        Some((id.clone(), *version))
                    } else {
                        None
                    }
                })
            });
        let notice_valid = notice.as_ref().is_none_or(|(id, version)| {
            d.sessions
                .get(sid)
                .is_some_and(|s| super::workshop_combat_runtime::applicable(s, id, *version))
        });
        let status = if current && (status != PlaybackStatus::Completed || notice_valid) {
            status
        } else {
            PlaybackStatus::Cancelled
        };
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
            if let Some(error) = error {
                row["error"] = error.into();
            }
        }
        if current {
            let reaction = d.rows.iter().find(|r| r["turn_id"] == turn).cloned();
            let s = d.sessions.get_mut(sid).unwrap();
            if let Some(row) = reaction {
                let outcomes = row["model_reaction_outcomes"].as_array();
                let has_model = outcomes.is_some_and(|items| !items.is_empty());
                let all_silent = has_model && outcomes.unwrap().iter().all(|v| v == "silent");
                if matches!(
                    status,
                    PlaybackStatus::NotSelected | PlaybackStatus::Completed
                ) && all_silent
                {
                    s.history.silent(turn);
                } else if status == PlaybackStatus::Completed && has_model {
                    s.history
                        .push(turn, "assistant", row["text"].as_str().unwrap_or(""));
                }
                if (status == PlaybackStatus::NotSelected && all_silent
                    || status == PlaybackStatus::Completed)
                    && let Some(revision) = row["observation_revision"].as_u64()
                {
                    s.conversation_observation.considered(revision);
                }
            }
            s.status = status;
            let model_only = s.warning.as_ref().is_some_and(|w| {
                w.actions
                    .iter()
                    .all(crate::reaction_leaf::is_model_reaction)
            });
            if !model_only || matches!(status, PlaybackStatus::Started | PlaybackStatus::Completed)
            {
                s.haiku.last_activity = Instant::now();
            }
            if status.ends_playback() || status == PlaybackStatus::NotSelected {
                if let Some((id, version)) = notice {
                    let end = if status == PlaybackStatus::Completed {
                        NoticeEnd::Completed
                    } else {
                        NoticeEnd::Retry
                    };
                    super::workshop_combat_runtime::finish_notice(s, &id, version, end);
                }
                s.warning = None;
            }
        }
        d.revision += 1;
        tracing::info!(
            event = "warning_status",
            session_id = sid,
            turn_id = turn,
            playback_status = status.as_str()
        );
        current
    }
    fn protect_action(&self, sid: &str, turn: &str, protect: u64) {
        let mut d = self.data.lock().unwrap();
        if let Some(w) = d
            .sessions
            .get_mut(sid)
            .and_then(|s| s.warning.as_mut())
            .filter(|w| w.turn == turn)
        {
            w.started = true;
            w.protected_until = Some(Instant::now() + Duration::from_millis(protect));
        }
    }
    pub(super) async fn run_warning(
        self: Arc<Self>,
        sid: String,
        turn: String,
        actions: Vec<Speech>,
        mut cancel: watch::Receiver<bool>,
    ) {
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let monitor_turn = turn.clone();
        let mut monitor_cancel = cancel.clone();
        let monitor = super::monitor::Monitor::spawn(async move {
            loop {
                tokio::select! {
                    _=bridge::cancelled(&mut monitor_cancel)=>break,
                    _=tokio::time::sleep(Duration::from_millis(100))=>{
                        let mut d=owner.data.lock().unwrap();
                        let stale=d.sessions.get(&monitor_sid).is_some_and(|s|s.warning.as_ref().is_some_and(|w|w.turn==monitor_turn
                            && !active_applicable(w,s,&owner.config.warnings,&owner.config.combat)));
                        if stale{Self::cancel_warning(&mut d,&monitor_sid,"stale_observation");break;}
                    }
                }
            }
        });
        let result=async{
            let _permit=tokio::select!{biased;_=bridge::cancelled(&mut cancel)=>anyhow::bail!("cancelled"),p=self.serial.acquire()=>p.unwrap()};
            let actions = {
                let mut d=self.data.lock().unwrap();
                let s=d.sessions.get_mut(&sid).ok_or_else(||anyhow::anyhow!("session closed"))?;
                let active=s.warning.as_mut().filter(|w|w.turn==turn).ok_or_else(||anyhow::anyhow!("cancelled"))?;
                let input=active.input.clone();
                let mut resolved=actions;
                if let Some(text)=input.as_deref().filter(|_|is_query(&resolved)) {
                    anyhow::ensure!(observation_fresh(s),"stale observation");
                    resolved=vec![answer_fixed_query(s,text,resolved[0].kind,self.clock.elapsed().as_millis() as u64,&self.config.combat)];
                    s.warning.as_mut().unwrap().actions=resolved.clone();
                    if let Some(row)=d.rows.iter_mut().find(|r|r["turn_id"]==turn){row["text"]=display_text(&resolved).into();row["combat_actions"]=json!(resolved);}
                }
                let s=&d.sessions[&sid];
                super::environment_runtime::refresh_dialogue_context(s,&mut resolved);
                d.sessions.get_mut(&sid).unwrap().warning.as_mut().unwrap().actions=resolved.clone();
                resolved
            };
            let mut config=self.config.clone();
            if actions.iter().any(|a| matches!(a.delivery, Delivery::Combat | Delivery::UrgentEnvironment)) {
                config.speed=config.warnings.battle_speed;
            }
            let began=AtomicBool::new(false);
            let mut rendered=actions.clone();
            for action in &mut rendered {
                if crate::reaction_leaf::is_model_reaction(action) { action.text.clear(); }
            }
            let mut silent_only=true;
            let mut thunder_scream_completed=false;
            for (index,action) in actions.iter().enumerate(){
                anyhow::ensure!(!*cancel.borrow(),"cancelled");
                let action_began=AtomicBool::new(false);
                let started=||{
                    if !action_began.swap(true,Ordering::SeqCst){self.protect_action(&sid,&turn,action.protect_ms);}
                    if !began.swap(true,Ordering::SeqCst){self.warning_update(&sid,&turn,PlaybackStatus::Started,None);}
                };
                let mut text=action.text.clone();
                if let Some(leaf)=&action.leaf{
                    let details=leaf_details_after_cue(action, thunder_scream_completed);
                    let input=json!({"op":"combat_leaf","model":config.model,"max_tokens":config.max_tokens,
                        "reading_engine":config.reading_engine,"kind":leaf.kind,"details":details,
                        "temperature":leaf.temperature,"fallback_text":text});
                    let result=bridge::render(&config,&self.llm,input,&mut cancel).await;
                    if crate::reaction_leaf::is_model_reaction(action) {
                        anyhow::ensure!(!*cancel.borrow(),"cancelled");
                        let mut d=self.data.lock().unwrap();
                        // An already returned silent choice consumes this opportunity
                        // even if its old bearing fails the check below.
                        Self::remember_environment_outcome(&mut d,&turn,result.as_ref().ok());
                        let valid=d.sessions.get(&sid).is_some_and(|s| s.warning.as_ref().is_some_and(|w|w.turn==turn)
                            && applicable(action,s,&config.warnings,&config.combat));
                        if !valid {
                            Self::cancel_warning(&mut d,&sid,"stale_environment_reaction");
                            anyhow::bail!("stale environment reaction");
                        }
                    }
                    match result {
                        Ok(result)=>{
                            anyhow::ensure!(!*cancel.borrow(),"cancelled");
                            text=result["spoken_text"].as_str().unwrap_or(&text).to_owned();
                            rendered[index].text=result["text"].as_str().unwrap_or(&action.text).to_owned();
                            let mut d=self.data.lock().unwrap();
                            if let Some(row)=d.rows.iter_mut().find(|r|r["turn_id"]==turn){
                                row["text"]=display_text(&rendered).into();
                                if !row["llm_reports"].is_array(){row["llm_reports"]=json!([]);}
                                if let Some(reports)=result["llm_reports"].as_array(){row["llm_reports"].as_array_mut().unwrap().extend(reports.iter().cloned());}
                            }
                            d.revision+=1;
                            if result["environment_reaction_outcome"] == "silent" || result["reaction_leaf_outcome"] == "silent" { continue; }
                        }
                        Err(e) if *cancel.borrow()=>return Err(e),
                        Err(e)=>{
                            tracing::warn!(event="combat_leaf_fallback",kind=leaf.kind,error=%e);
                            if crate::reaction_leaf::is_model_reaction(action) {
                                rendered[index].text=action.text.clone();
                                let mut d=self.data.lock().unwrap();
                                let key=if leaf.kind == crate::environment::reaction::KIND { "environment_reaction_outcome" } else { "reaction_leaf_outcome" };
                                Self::remember_environment_outcome(&mut d,&turn,Some(&json!({key:"generation_error"})));
                                if let Some(row)=d.rows.iter_mut().find(|r|r["turn_id"]==turn) {
                                    row["text"]=display_text(&rendered).into();
                                }
                                d.revision+=1;
                            }
                        }
                    }
                }
                silent_only=false;
                if let Some(plan)=&action.visual_plan{
                    if let Some(cue)=&plan.cue{
                        if let Some(path)=config.warnings.cue_dir.as_deref().and_then(|dir|cue_path(dir,cue.id)){self.audio.play_file(&config,&path,&mut cancel,0,&started).await?;}
                        else{tracing::warn!(event="warning_cue_fallback",cue_id=cue.id,reason="file_missing");self.audio.speak(&config,cue.text,&mut cancel,&started).await?;}
                    }
                    if let Some(paths)=config.warnings.cue_dir.as_deref().and_then(|dir|plan.fragment_paths(dir)){
                        tracing::info!(event="warning_fragments",fragments=paths.len());
                        for (i,path) in paths.iter().enumerate(){self.audio.play_file(&config,path,&mut cancel,i,&started).await?;}
                    }else if !text.is_empty(){
                        if !plan.cue_sequence.is_empty(){tracing::warn!(event="warning_fragments_fallback",reason="file_missing");}
                        self.audio.speak(&config,&text,&mut cancel,&started).await?;
                    }
                    continue;
                }
                let paths=if action.kind=="ushiro_named"{config.warnings.cue_dir.as_deref().and_then(|dir|named_paths(dir,&text))}
                    else if !action.cue_sequence.is_empty(){action.cue_sequence.iter().map(|id|config.warnings.cue_dir.as_deref().and_then(|dir|cue_path(dir,id))).collect::<Option<Vec<_>>>()}
                    else{None};
                if let Some(paths)=paths{
                    for (i,path) in paths.iter().enumerate(){self.audio.play_file(&config,path,&mut cancel,i,&started).await?;}
                }else if let Some(path)=action.cue_id.and_then(|id|config.warnings.cue_dir.as_deref().and_then(|dir|cue_path(dir,id))){
                    self.audio.play_file(&config,&path,&mut cancel,0,&started).await?;
                }else if !text.is_empty(){self.audio.speak(&config,&text,&mut cancel,&started).await?;}
                if action.kind=="thunder_cue" && action_began.load(Ordering::SeqCst) { thunder_scream_completed=true; }
            }
            Ok::<PlaybackStatus,anyhow::Error>(if silent_only { PlaybackStatus::NotSelected } else { PlaybackStatus::Completed })
        }.await;
        monitor.finish().await;
        match result {
            Ok(status) => {
                self.warning_update(&sid, &turn, status, None);
            }
            Err(e) => {
                self.warning_update(
                    &sid,
                    &turn,
                    if *cancel.borrow() {
                        PlaybackStatus::Cancelled
                    } else {
                        PlaybackStatus::Failed
                    },
                    Some(e.to_string()),
                );
            }
        }
        {
            let mut jobs = self.jobs.lock().unwrap();
            jobs.retain(|job| !job.is_finished());
            let mut d = self.data.lock().unwrap();
            if !d.stopped && d.sessions.contains_key(&sid) {
                self.refresh_combat_audio(&mut d, &sid);
                self.start_pending(&mut d, &mut jobs, &sid);
            }
        }
        self.resume_deferred(&sid);
    }
}

fn leaf_details_after_cue(action: &Speech, thunder_scream_completed: bool) -> Value {
    let mut details = action
        .leaf
        .as_ref()
        .map(|leaf| leaf.details.clone())
        .unwrap_or(Value::Null);
    if action.kind == "thunder_reaction" && thunder_scream_completed {
        details["scream_status"] = "completed".into();
    }
    details
}

pub(super) fn is_query(actions: &[Speech]) -> bool {
    actions.len() == 1
        && matches!(
            actions[0].kind,
            "hostile_direction" | "hostile_count" | "dragon_direction" | "smell"
        )
}

pub(super) fn answer_fixed_query(
    session: &Session,
    text: &str,
    kind: &str,
    now: u64,
    settings: &crate::combat::model::Settings,
) -> Speech {
    if kind == "smell" {
        let mut reply = crate::environment::ambient::current_smell_query_reply(
            session
                .environment_latest
                .as_ref()
                .or(session.latest.as_ref())
                .expect("fresh query"),
            text,
        );
        reply.delivery = Delivery::PlayerReply;
        return reply;
    }
    session
        .combat
        .answer_query(
            session.latest.as_ref().expect("fresh query"),
            text,
            now,
            settings,
        )
        .unwrap_or_else(|| Speech::new("hostile_direction", "今は方位と距離を確かめられへんわ。"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn thunder_generation_does_not_promote_a_queued_cue_to_completed() {
        let mut action = Speech::new("thunder_reaction", "雷の一言");
        action.leaf = Some(crate::combat::model::LeafRequest {
            kind: crate::environment::reaction::KIND.into(),
            details: json!({"scream_status":"scheduled"}),
            temperature: 0.65,
        });
        assert_eq!(
            leaf_details_after_cue(&action, false)["scream_status"],
            "scheduled"
        );
        assert_eq!(
            leaf_details_after_cue(&action, true)["scream_status"],
            "completed"
        );
        action.leaf.as_mut().unwrap().details["scream_status"] = "not_scheduled".into();
        assert_eq!(
            leaf_details_after_cue(&action, false)["scream_status"],
            "not_scheduled"
        );
        assert_eq!(
            action.leaf.as_ref().unwrap().details["scream_status"],
            "not_scheduled"
        );
    }

    #[test]
    fn returned_silent_choice_is_consumed_before_spatial_invalidation() {
        use crate::combat::model::Mode;
        fn smell(cardinal: &str) -> GameEvent {
            GameEvent::parse(json!({"schema_version":"2026-05-24","adapter":"fixture","sequence":1,
                "observed_at":chrono::Utc::now(),"event":{"name":"status_snapshot","source_kind":"system","priority_hint":"background","certainty":"high"},
                "smell_observation":{"status":"present","smell_id":"zombie","category":"decay","valence":"unpleasant",
                    "source_kind":"entity","specificity":"source","effective_strength":5,
                    "direction_estimate":{"cardinal":cardinal}}})).unwrap()
        }
        for outcome in ["silent", "speak"] {
            let dialogue = Dialogue::new(crate::dialogue::DialogueConfig::default()).unwrap();
            dialogue.register("s", "試験", false);
            let old = smell("east");
            let current = smell("west");
            let settings = &dialogue.config.combat;
            let mut data = dialogue.data.lock().unwrap();
            let session = data.sessions.get_mut("s").unwrap();
            session.ambient.update(&old, 1000, true, settings);
            session.ambient.update(&old, 1100, true, settings);
            let mut action = session
                .ambient
                .priority_smell(&old, 1100, Mode::Normal, false, settings)
                .unwrap();
            action.delivery = Delivery::Ambient;
            crate::environment::reaction::attach(&mut action, &old, &json!({}), settings);
            session.latest = Some(current.clone());
            session.environment_latest = Some(current.clone());
            session.received = Some(Instant::now());
            session.ambient.update(&current, 1200, true, settings);
            let (turn, _rx) = Dialogue::queue_actions(&mut data, "s", &[action], None);
            Dialogue::remember_environment_outcome(
                &mut data,
                &turn,
                Some(&json!({"environment_reaction_outcome":outcome})),
            );
            Dialogue::cancel_warning(&mut data, "s", "stale_environment_reaction");
            let session = data.sessions.get_mut("s").unwrap();
            session.ambient.update(&current, 1300, true, settings);
            let retry =
                session
                    .ambient
                    .priority_smell(&current, 1300, Mode::Normal, false, settings);
            assert_eq!(retry.is_some(), outcome == "speak", "{outcome}");
        }
    }
    #[test]
    fn silent_model_consideration_preserves_haiku_quiet_time_but_speech_and_cue_do_not() {
        for kind in [crate::environment::reaction::KIND, "newly_burning_visual"] {
            let d = Dialogue::new(crate::dialogue::DialogueConfig::default()).unwrap();
            d.register("s", "試験", false);
            let mut action = Speech::new("ambient", "まだ発話しない");
            action.leaf = Some(crate::combat::model::LeafRequest {
                kind: kind.into(),
                details: json!({}),
                temperature: 0.5,
            });
            let original = Instant::now() - Duration::from_secs(60);
            let (turn, _rx) = {
                let mut data = d.data.lock().unwrap();
                data.sessions.get_mut("s").unwrap().haiku.last_activity = original;
                Dialogue::queue_actions(&mut data, "s", &[action.clone()], None)
            };
            assert_eq!(
                d.data.lock().unwrap().sessions["s"].haiku.last_activity,
                original
            );
            d.warning_update("s", &turn, PlaybackStatus::NotSelected, None);
            {
                let data = d.data.lock().unwrap();
                assert_eq!(data.sessions["s"].haiku.last_activity, original);
                assert!(data.sessions["s"].warning.is_none());
            }
            let (turn, _rx) =
                Dialogue::queue_actions(&mut d.data.lock().unwrap(), "s", &[action.clone()], None);
            d.warning_update("s", &turn, PlaybackStatus::Started, None);
            assert!(d.data.lock().unwrap().sessions["s"].haiku.last_activity > original);
            d.warning_update("s", &turn, PlaybackStatus::Completed, None);
            let mut data = d.data.lock().unwrap();
            data.sessions.get_mut("s").unwrap().haiku.last_activity = original;
            let _ = Dialogue::queue_actions(
                &mut data,
                "s",
                &[Speech::new("thunder_cue", "ひいっ！"), action],
                None,
            );
            assert!(data.sessions["s"].haiku.last_activity > original);
        }
    }
    #[test]
    fn named_ushiro_uses_manifest_and_requires_both_fragments() {
        let directory = std::env::temp_dir().join(format!("dogido-named-{}", uuid::Uuid::new_v4()));
        let names = directory.join("player_names");
        std::fs::create_dir_all(&names).unwrap();
        std::fs::write(
            names.join("manifest.json"),
            r#"{"call_name_to_file":{"ゆきん":"voice.mp3"}}"#,
        )
        .unwrap();
        std::fs::write(names.join("voice.mp3"), b"fixture").unwrap();
        assert!(named_paths(&directory, "ゆきんうしろ！うしろ〜！").is_none());
        std::fs::write(names.join("ushiro_tail.mp3"), b"fixture").unwrap();
        assert_eq!(
            named_paths(&directory, "ゆきんうしろ！うしろ〜！").unwrap(),
            vec![names.join("voice.mp3"), names.join("ushiro_tail.mp3")]
        );
        std::fs::remove_dir_all(directory).unwrap();
    }
    #[test]
    fn environmental_speech_and_silence_share_history_only_after_delivery_decision() {
        for (kind, key) in [
            (
                crate::environment::reaction::KIND,
                "environment_reaction_outcome",
            ),
            ("newly_burning_visual", "reaction_leaf_outcome"),
        ] {
            for (status, outcome, expected) in [
                (PlaybackStatus::NotSelected, "silent", "event"),
                (PlaybackStatus::Completed, "silent", "event"),
                (PlaybackStatus::Completed, "speak", "assistant"),
                (PlaybackStatus::Failed, "silent", ""),
                (PlaybackStatus::Cancelled, "speak", ""),
            ] {
                let d = Dialogue::new(crate::dialogue::DialogueConfig::default()).unwrap();
                d.register("s", "試験", false);
                let mut a = Speech::new("smell", "パンの匂いやな。");
                a.leaf = Some(crate::combat::model::LeafRequest {
                    kind: kind.into(),
                    details: json!({}),
                    temperature: 0.65,
                });
                let (turn, _rx) = {
                    let mut data = d.data.lock().unwrap();
                    let (t, rx) = Dialogue::queue_actions(&mut data, "s", &[a], None);
                    Dialogue::remember_environment_outcome(
                        &mut data,
                        &t,
                        Some(&json!({key:outcome})),
                    );
                    if outcome == "speak" {
                        data.rows.iter_mut().find(|r| r["turn_id"] == t).unwrap()["text"] =
                            "パンの匂いやな。".into();
                    }
                    (t, rx)
                };
                d.warning_update("s", &turn, status, None);
                let data = d.data.lock().unwrap();
                let h = &data.sessions["s"].history;
                assert_eq!(
                    h.rows()
                        .first()
                        .and_then(|r| r["role"].as_str())
                        .unwrap_or(""),
                    expected
                );
                assert!(h.completed_pairs().is_empty());
            }
        }
    }
}
