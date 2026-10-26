//! ゲームイベントから観測・戦況・環境を更新し、保留中の配送と発句を進める。
//! jobs → dataの順でロックし、新規イベントだけを判断へ渡す。操作の実行結果は重複受信時も照合する。
//! ロック解放後に入力配送・発句再開・評価記録を進め、受理結果と配送予定の命令を返す。
use super::{
    Dialogue, chat_context, complete_observation, environment_runtime, episode_runtime, id,
    recent_observation, warnings, workshop_focus,
};
use crate::{
    events::{EventName, GameEvent, SourceKind},
    ingress::Admission,
};
use serde_json::{Value, json};
use std::{sync::Arc, time::Instant};

impl Dialogue {
    /// 一件の観測を鮮度・重複と照合し、戦闘・環境の判断と保留中の配送を進める。
    pub(super) fn observe_inner(
        self: &Arc<Self>,
        session_id: &str,
        mut event: GameEvent,
        key: Option<&str>,
    ) -> Value {
        let sequence = event.sequence;
        let recent = recent_observation(&event);
        // submitと同じjobs → dataの順で、停止判定からjob登録までを保護する。
        // shutdownはdataで停止を確定して解放した後、jobsを回収するため、
        // 受理されたjobは回収対象に入り、停止後の新規受付はここで断られる。
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
        let mut screenshot_acks = Vec::new();
        let mut assist_results = Vec::new();
        for result in &event.command_results {
            if result.command_type
                == crate::events::AdapterCommandResultCommandType::CaptureBodyView
                || s.visual.owns(&result.command_id)
            {
                if s.visual.receive(result, chrono::Utc::now()) {
                    screenshot_acks.push(result.command_id.clone());
                }
            } else if result.command_type
                == crate::events::AdapterCommandResultCommandType::CaptureScreenshot
                || s.screenshots.owns(&result.command_id)
            {
                let (ack, receipt) = s.screenshots.receive(result, chrono::Utc::now());
                if ack {
                    screenshot_acks.push(result.command_id.clone());
                }
                if let Some(receipt) = receipt {
                    let owner = self.clone();
                    let sid = session_id.to_owned();
                    let instance = s.instance_id.clone();
                    let command_id = result.command_id.clone();
                    let store = crate::bookshelf::store::Store {
                        memory: self.config.haiku.memory_dir.clone(),
                        capture_dir: self.config.haiku.capture_dir.clone(),
                        screenshots: self
                            .config
                            .haiku
                            .screenshot_dir
                            .clone()
                            .unwrap_or_else(crate::screenshot::default_directory),
                        photos: std::path::PathBuf::new(),
                    };
                    jobs.push(tokio::spawn(async move {
                        let write_id = command_id.clone();
                        let saved = tokio::task::spawn_blocking(move || {
                            store.register_capture(&write_id, &receipt)
                        })
                        .await;
                        let terminal = match &saved {
                            Ok(Ok(())) => true,
                            Ok(Err(error)) => crate::bookshelf::captures::terminal_error(error),
                            Err(_) => false,
                        };
                        match &saved {
                            Ok(Ok(())) => tracing::info!(
                                event = "haiku_screenshot_registered",
                                session_id = sid,
                                command_id
                            ),
                            error => tracing::warn!(
                                event = "haiku_screenshot_registration_failed",
                                session_id = sid,
                                command_id,
                                terminal,
                                ?error
                            ),
                        }
                        if let Some(s) = owner.data.lock().unwrap().sessions.get_mut(&sid)
                            && s.instance_id == instance
                        {
                            s.screenshots
                                .registered(&command_id, terminal, chrono::Utc::now());
                        }
                    }));
                }
            } else {
                assist_results.push(result.clone());
            }
        }
        // 画像は専用の最新一枚へ受理済み。以後の観測コピー・履歴・episodeへ保持しない。
        event.discard_command_images();
        let mut results = s.assist.observe_results(
            &assist_results,
            chrono::Utc::now(),
            s.warning.is_some() || s.pending_warning.is_some() || s.cancel.is_some(),
        );
        results.acknowledged_ids.extend(screenshot_acks);
        for observed in &results.observed {
            tracing::info!(event="assist_result",session_id=session_id,result=%json!(observed));
        }
        let episode_before = (!duplicate && self.episodes.is_some())
            .then(|| episode_runtime::Before::capture(&d, session_id));
        if !duplicate {
            let now = self.clock.elapsed().as_millis() as u64;
            let complete = complete_observation(&event);
            let s = d.sessions.get_mut(session_id).unwrap();
            super::body_runtime::observe(s, &event);
            s.horse_recall.observe(&event, recent);
            if complete {
                s.latest = Some(event.clone());
                s.received = recent.then(Instant::now);
            }
            if complete && recent {
                s.foreground
                    .set_game_paused(now, event.world.game_paused.unwrap_or(false));
            }
            super::body_interest_runtime::observe_boundary(s, Instant::now());
            if complete || recent && event.event.source_kind == SourceKind::Auditory {
                s.audio_latest = Some(event.clone());
                s.audio_received = recent.then(Instant::now);
            }
            // 同じ受信・同じロック内の判断では、受理済み身体の時点を一度固定する。
            // environment_latestは部分通知を補った別入力なので、下で独立に投影する。
            let senses = super::body_runtime::event(s, &event);
            if recent {
                s.environment_latest = Some(environment_runtime::context(s, &event, complete));
                let (boss, ominous) = s.combat.environmental_presence(now, &self.config.combat);
                s.danger.set_presence(boss, ominous);
                s.danger.update(&senses, now, complete, &self.config.combat);
                s.ambient
                    .update(&senses, now, complete, &self.config.combat);
                s.conversation_observation.observe_origin(&senses);
                if let Some(current) = &s.environment_latest {
                    let current = super::body_runtime::event(s, current);
                    match crate::conversation_observation::project(
                        &current,
                        s.ambient.current_structure(),
                        self.config.combat.number("home_bed_prompt_distance"),
                        self.config.combat.number("darkness_advice_light_threshold"),
                        self.config.combat.ms("weather_sound_recent_ms"),
                    ) {
                        Ok(context) => {
                            let context = super::body_runtime::with_context(context, &current);
                            s.conversation_observation.observe(context);
                            s.conversation_observation.sample_notice(
                                now,
                                self.config.combat.ms("surroundings_notice_interval_ms"),
                                event.event.name == EventName::StatusSnapshot,
                            );
                        }
                        Err(error) => {
                            tracing::warn!(event="conversation_observation_rejected",session_id,%error)
                        }
                    }
                }
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
            }
            self.tick_workshop(&mut d, session_id);
            self.tick_foreground(&mut d, session_id);
            if recent {
                // Clear a superseded deep-dark reaction before busy is computed below.
                self.preempt_ominous_reaction(&mut d, session_id, &event);
            }
            self.refresh_combat_audio(&mut d, session_id);
            if recent {
                let s = d.sessions.get_mut(session_id).unwrap();
                let busy = s.warning.is_some() || s.pending_warning.is_some() || jobs.len() >= 16;
                let decision = s.combat.observe(
                    &event,
                    now,
                    complete,
                    busy,
                    &self.config.combat,
                    &self.config.warnings,
                );
                let names = s.combat.take_name_updates();
                s.chat_observation.observe_body(
                    &senses,
                    &names,
                    &chat_context::CatalogLabels,
                );
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
                    s.foreground.start_combat();
                }
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
                    Self::cancel_assist(&mut d, session_id);
                    let s = d.sessions.get_mut(session_id).unwrap();
                    s.input_generation = s.input_generation.wrapping_add(1);
                    s.deferred_input = None;
                }
                if event.event.name == EventName::PlayerDied || decision.dimension_changed {
                    self.cancel_reply_queue(&mut d, session_id, "world_context_changed");
                }
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    session_id,
                    decision,
                    super::combat_runtime::DecisionContext {
                        input: None,
                        reason: warnings::interruption_reason(&event),
                        combat_priority: recent
                            && (workshop_focus::hostile(&event)
                                || matches!(
                                    event.event.name,
                                    EventName::HostileDefeated
                                        | EventName::CreeperDetonated
                                        | EventName::CombatEnded
                                )),
                    },
                );
                self.process_environment(&mut d, &mut jobs, session_id, now, combat_priority);
                let s = d.sessions.get_mut(session_id).unwrap();
                s.conversation_observation.finish_notice_opportunity();
                s.danger.finish_frame(s.mode);
            } else {
                let s = d.sessions.get_mut(session_id).unwrap();
                s.chat_observation.observe_body(
                    &senses,
                    &Default::default(),
                    &chat_context::CatalogLabels,
                );
            }
            self.resolve_vocalization(d.sessions.get_mut(session_id).unwrap(), Some(&event));
            self.start_pending(&mut d, &mut jobs, session_id);
            self.tick_workshop(&mut d, session_id);
            if recent && complete {
                self.start_workshop_recovery(&mut d, &mut jobs, session_id);
            }
            d.revision += 1;
        }
        let s = &d.sessions[session_id];
        let stale_look = d.rows.iter().find(|r| r["turn_id"] == s.current_turn)
            .is_some_and(|r| !super::visual_runtime::look_input_current(s,r));
        if stale_look { Self::cancel_chat(&mut d,session_id,"look_input_changed"); }
        let body_reports =
            super::body_request_runtime::observe(d.sessions.get_mut(session_id).unwrap(), &event);
        for report in body_reports {
            if let Some(row) = d.rows.iter_mut().find(|row| {
                row["session_id"] == session_id
                    && row["body_request_command_id"] == report.command_id
            }) {
                row["body_request_status"] = report.value["status"].clone();
                row["body_request_result"] = report.value.clone();
            }
            tracing::info!(event="body_request_result",session_id,result=%report.value);
            let continued = self.continue_look_reply(&mut d, &mut jobs, session_id, &report);
            if !continued && let Some(feedback) = report.feedback {
                self.queue_fixed_reply(
                    &mut d,
                    &mut jobs,
                    session_id,
                    feedback.into(),
                    Some(&report.original),
                );
            }
            d.revision += 1;
        }
        let horse_feedback = super::horse_recall_runtime::observe_results(
            d.sessions.get_mut(session_id).unwrap(),
            &results,
        );
        if let Some(feedback) = results.feedback.clone().or(horse_feedback) {
            self.queue_fixed_reply(&mut d, &mut jobs, session_id, feedback, None);
        }
        let episode_snapshot = episode_before.map(|mut before| {
            let actions = before.actions(&d, session_id);
            let s = &d.sessions[session_id];
            (before, json!(s.mode), s.foreground.combat_active, actions)
        });
        drop(d);
        drop(jobs);
        if !duplicate && recent && complete_observation(&event) {
            self.resume_reply_input(session_id);
        }
        if !duplicate && recent && event.event.name == EventName::StatusSnapshot {
            self.try_start_haiku(session_id, false);
        }
        let commands = {
            let mut d = self.data.lock().unwrap();
            d.sessions
                .get_mut(session_id)
                .map(|s| {
                    super::body_interest_runtime::refresh(s,self.clock.elapsed().as_millis() as u64);
                    let utc = chrono::Utc::now();
                    // ACKは共有台帳。到着の確定は今回の姿勢・休止・入力による取消を反映してから行う。
                    // 同一イベント内の古いarrivedで、取消済み対象の再訪待ち時間を作らない。
                    for result in s.body_autonomy.observe_results(&event.command_results,Instant::now(),utc) {
                        tracing::info!(event="body_interest_result",session_id=session_id,result=%result);
                    }
                    let mut commands: Vec<Value> = s.assist.commands(utc).iter().map(|command| json!(command)).collect();
                    commands.extend(s.body_autonomy.commands(Instant::now(),utc));
                    commands.extend(super::body_request_runtime::commands(s));
                    commands.extend(s.screenshots.commands(utc));
                    let capture_context = (self.config.chat_image.enabled && !s.preview && !s.foreground.game_paused)
                        .then(|| s.body_observation.accepted(Instant::now()).map(|v| v.context.clone())).flatten();
                    s.visual.validate_generation(s.input_generation);
                    s.visual.arm(session_id,capture_context,utc);
                    commands.extend(s.visual.commands(utc));
                    commands
                })
                .unwrap_or_default()
        };
        let event_id = id("evt");
        let body_request_generation = self
            .data
            .lock()
            .unwrap()
            .sessions
            .get(session_id)
            .map(|s| s.input_generation);
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
            "state":null,"outputs":null,"commands":commands,"body_request_generation":body_request_generation,"acknowledged_command_ids":results.acknowledged_ids,"server_time":recorded_at,"phase":"dialogue"})
    }
}
