//! 一つの戦闘判断から選ばれた音声を、同じ取消所有権で順に配送する。
use super::{Data, Dialogue, Session, bridge, id, observation_fresh};
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
            s.status = "cancelled".into();
            let _ = c.send(true);
            let turn = s.current_turn.clone();
            if let Some(p) = s.address.as_mut() {
                p.playback(&turn, "cancelled");
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
            for a in &w.actions {
                if let Scope::Workshop { id, version } = &a.scope {
                    super::workshop_combat_runtime::finish_notice(s, id, *version, reason);
                }
            }
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
    pub(super) fn queue_actions(
        d: &mut Data,
        sid: &str,
        actions: &[Speech],
        input: Option<&str>,
    ) -> (String, watch::Receiver<bool>) {
        let turn = id("warning");
        let (cancel, rx) = watch::channel(false);
        let s = d.sessions.get_mut(sid).unwrap();
        s.haiku.last_activity = Instant::now();
        s.warning = Some(Active {
            turn: turn.clone(),
            actions: actions.to_vec(),
            cancel,
            started: false,
            input: input.map(str::to_owned),
            protected_until: None,
        });
        s.status = "queued".into();
        if d.rows.len() == 200 {
            d.rows.pop_front();
        }
        let plan = actions.iter().find_map(|a| a.visual_plan.as_ref());
        let category = if actions.iter().any(|a| a.delivery == Delivery::Combat) {
            "callout"
        } else {
            "speech"
        };
        d.rows.push_back(json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":sid,"category":category,
            "source":if input.is_some(){"player_input"}else{"game_observation"},"player_input_text":input,
            "text":display_text(actions),"created_at":chrono::Utc::now(),"reference_ids":[],"output_mode":"both",
            "playback_status":"queued","warning":plan,"combat_actions":actions}));
        d.revision += 1;
        tracing::info!(event="warning_queued",session_id=sid,turn_id=turn,actions=%json!(actions));
        (turn, rx)
    }
    fn warning_update(&self, sid: &str, turn: &str, status: &str, error: Option<String>) -> bool {
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
        let status = if current && (status != "completed" || notice_valid) {
            status
        } else {
            "cancelled"
        };
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
            if let Some(error) = error {
                row["error"] = error.into();
            }
        }
        if current {
            let s = d.sessions.get_mut(sid).unwrap();
            s.status = status.into();
            s.haiku.last_activity = Instant::now();
            if matches!(status, "completed" | "failed" | "cancelled") {
                if let Some((id, version)) = notice {
                    super::workshop_combat_runtime::finish_notice(s, &id, version, status);
                }
                s.warning = None;
            }
        }
        d.revision += 1;
        tracing::info!(
            event = "warning_status",
            session_id = sid,
            turn_id = turn,
            playback_status = status
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
        let monitor = tokio::spawn(async move {
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
                resolved
            };
            let mut config=self.config.clone();
            if actions.iter().any(|a| matches!(a.delivery, Delivery::Combat | Delivery::UrgentEnvironment)) {
                config.speed=config.warnings.battle_speed;
            }
            let began=AtomicBool::new(false);
            let mut rendered=actions.clone();
            for (index,action) in actions.iter().enumerate(){
                anyhow::ensure!(!*cancel.borrow(),"cancelled");
                let action_began=AtomicBool::new(false);
                let started=||{
                    if !action_began.swap(true,Ordering::SeqCst){self.protect_action(&sid,&turn,action.protect_ms);}
                    if !began.swap(true,Ordering::SeqCst){self.warning_update(&sid,&turn,"started",None);}
                };
                let mut text=action.text.clone();
                if let Some(leaf)=&action.leaf{
                    let input=json!({"op":"combat_leaf","model":config.model,"max_tokens":config.max_tokens,
                        "reading_engine":config.reading_engine,"kind":leaf.kind,"details":leaf.details,
                        "temperature":leaf.temperature,"fallback_text":text});
                    match bridge::render(&config,&self.llm,input,&mut cancel).await{
                        Ok(result)=>{
                            text=result["spoken_text"].as_str().unwrap_or(&text).to_owned();
                            rendered[index].text=result["text"].as_str().unwrap_or(&action.text).to_owned();
                            let mut d=self.data.lock().unwrap();
                            if let Some(row)=d.rows.iter_mut().find(|r|r["turn_id"]==turn){
                                row["text"]=display_text(&rendered).into();
                                if !row["llm_reports"].is_array(){row["llm_reports"]=json!([]);}
                                if let Some(reports)=result["llm_reports"].as_array(){row["llm_reports"].as_array_mut().unwrap().extend(reports.iter().cloned());}
                            }
                            d.revision+=1;
                        }
                        Err(e) if *cancel.borrow()=>return Err(e),
                        Err(e)=>tracing::warn!(event="combat_leaf_fallback",kind=leaf.kind,error=%e),
                    }
                }
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
            }
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
        let mut reply = crate::environment::ambient::current_smell_reply(
            session
                .environment_latest
                .as_ref()
                .or(session.latest.as_ref())
                .expect("fresh query"),
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
}
