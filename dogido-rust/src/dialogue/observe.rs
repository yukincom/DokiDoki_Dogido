//! ゲームイベントから観測・戦況・環境を更新し、保留処理と必要なplayer入力を順に進める。
//! jobs → dataの順でロックし、新規イベントだけを判断へ渡す。操作の実行結果は重複受信時も照合する。
//! ロック解放後に入力配送・発句再開・評価記録を進め、受理結果と配送予定の命令を返す。
use super::{
    Dialogue, chat_context, complete_observation, environment_runtime, episode_runtime, id,
    recent_observation, warnings, workshop_focus, workshop_record,
};
use crate::{
    events::{EventName, GameEvent, SourceKind},
    ingress::Admission,
};
use serde_json::{Value, json};
use std::{sync::Arc, time::Instant};

impl Dialogue {
    /// 一件の観測を鮮度・重複と照合し、戦闘・環境の判断から入力配送までを処理する。
    /// privateは入口で確定した記録制限で、同じイベントから受理する入力にも引き継ぐ。
    pub(super) fn observe_inner(
        self: &Arc<Self>,
        session_id: &str,
        event: GameEvent,
        key: Option<&str>,
        private: bool,
    ) -> Value {
        let sequence = event.sequence;
        let recent = recent_observation(&event);
        let text = event.meta.user_text.clone().unwrap_or_default();
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
        let results = s.assist.observe_results(
            &event.command_results,
            chrono::Utc::now(),
            s.warning.is_some() || s.pending_warning.is_some() || s.cancel.is_some(),
        );
        for observed in &results.observed {
            tracing::info!(event="assist_result",session_id=session_id,result=%json!(observed));
        }
        let episode_before = (!duplicate && self.episodes.is_some())
            .then(|| episode_runtime::Before::capture(&d, session_id));
        let mut input_handled = false;
        let mut input_generation = None;
        if !duplicate {
            let now = self.clock.elapsed().as_millis() as u64;
            let complete = complete_observation(&event);
            let s = d.sessions.get_mut(session_id).unwrap();
            if complete {
                s.latest = Some(event.clone());
                s.received = recent.then(Instant::now);
            }
            if complete && recent {
                s.foreground
                    .set_game_paused(now, event.world.game_paused.unwrap_or(false));
            }
            if complete || recent && event.event.source_kind == SourceKind::Auditory {
                s.audio_latest = Some(event.clone());
                s.audio_received = recent.then(Instant::now);
            }
            if complete {
                if recent {
                    s.stable_threat.observe(
                        &event,
                        now,
                        self.config.warnings.recent_damage_window_ms,
                    );
                } else {
                    s.stable_threat.reset();
                }
            } else if recent && warnings::interruption_reason(&event).is_some() {
                s.stable_threat.reset();
            }
            if recent {
                s.environment_latest = Some(environment_runtime::context(s, &event, complete));
                let (boss, ominous) = s.combat.environmental_presence(now, &self.config.combat);
                s.danger.set_presence(boss, ominous);
                s.danger.update(&event, now, complete, &self.config.combat);
                s.ambient.update(&event, now, complete, &self.config.combat);
                if let Some(current) = &s.environment_latest {
                    match crate::conversation_observation::project(
                        current,
                        s.ambient.current_structure(),
                        self.config.combat.number("home_bed_prompt_distance"),
                        self.config.combat.number("darkness_advice_light_threshold"),
                        self.config.combat.ms("weather_sound_recent_ms"),
                    ) {
                        Ok(context) => s.conversation_observation.observe(context),
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
                Self::cancel_address(&mut d, session_id, "attention_interrupted");
            }
            if recent && !text.trim().is_empty() {
                Self::cancel_combat_input(&mut d, session_id);
                Self::cancel_haiku(&mut d, session_id, "new_player_input");
                Self::cancel_assist(&mut d, session_id);
                Self::cancel_light(&mut d, session_id);
                let s = d.sessions.get_mut(session_id).unwrap();
                s.input_generation = s.input_generation.wrapping_add(1);
                input_generation = Some(s.input_generation);
                s.record_private_generation = Some((s.input_generation, private));
                s.last_player_input = Some(now);
                s.ambient.note_player_input(now);
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
                let workshop_event = (!text.trim().is_empty() && workshop_focus::quiet(s))
                    .then(|| event.without_player_input());
                let decision = s.combat.observe(
                    workshop_event.as_ref().unwrap_or(&event),
                    now,
                    complete,
                    busy,
                    &self.config.combat,
                    &self.config.warnings,
                );
                let names = s.combat.take_name_updates();
                if let Err(error) =
                    s.chat_observation
                        .observe(&event, &names, &chat_context::CatalogLabels)
                {
                    tracing::warn!(event="chat_observation_rejected", session_id, %error);
                }
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
                    s.foreground.start_combat(
                        now,
                        self.config.combat.ms("conversation_suspended_player_turns"),
                    );
                }
                input_handled = decision.input_handled;
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
                    d.sessions
                        .get_mut(session_id)
                        .unwrap()
                        .stable_threat
                        .reset();
                    Self::cancel_assist(&mut d, session_id);
                    let s = d.sessions.get_mut(session_id).unwrap();
                    s.input_generation = s.input_generation.wrapping_add(1);
                    s.deferred_input = None;
                }
                if event.event.name == EventName::PlayerDied || decision.dimension_changed {
                    self.cancel_knowledge_queue(&mut d, session_id, "world_context_changed");
                    Self::cancel_address(&mut d, session_id, "attention_interrupted");
                }
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    session_id,
                    decision,
                    input_handled.then_some(text.as_str()),
                    warnings::interruption_reason(&event),
                );
                self.process_environment(
                    &mut d,
                    &mut jobs,
                    session_id,
                    &event,
                    now,
                    combat_priority,
                );
                let s = d.sessions.get_mut(session_id).unwrap();
                s.danger.finish_frame(s.mode);
            } else {
                let s = d.sessions.get_mut(session_id).unwrap();
                if let Err(error) = s.chat_observation.observe(
                    &event,
                    &Default::default(),
                    &chat_context::CatalogLabels,
                ) {
                    tracing::warn!(event="chat_observation_rejected", session_id, %error);
                }
            }
            self.resolve_vocalization(d.sessions.get_mut(session_id).unwrap(), Some(&event));
            self.start_pending(&mut d, &mut jobs, session_id);
            self.tick_workshop(&mut d, session_id);
            if recent && complete && text.trim().is_empty() {
                self.start_workshop_recovery(&mut d, &mut jobs, session_id);
            }
            d.revision += 1;
        }
        if let Some(feedback) = results.feedback.clone() {
            self.queue_fixed_reply(&mut d, &mut jobs, session_id, feedback, None);
        }
        let mut episode_snapshot = episode_before.map(|mut before| {
            let actions = before.actions(&d, session_id);
            let s = &d.sessions[session_id];
            (before, json!(s.mode), s.foreground.combat_active, actions)
        });
        drop(d);
        drop(jobs);
        if !duplicate && recent && complete_observation(&event) && text.trim().is_empty() {
            self.resume_knowledge_input(session_id);
        }
        if !duplicate
            && recent
            && event.event.name == EventName::StatusSnapshot
            && text.trim().is_empty()
        {
            self.try_start_haiku(session_id, false);
        }
        let input = if input_handled {
            json!({"accepted":true,"reason":"combat_input"})
        } else if !duplicate && !text.trim().is_empty() {
            self.submit_recorded(
                Some(session_id),
                &text,
                "text",
                false,
                input_generation.map(|g| (g, None)),
                false,
                workshop_record::Admission {
                    forwarded: false,
                    private,
                },
            )
        } else {
            Value::Null
        };
        let commands = {
            let d = self.data.lock().unwrap();
            if let Some((before, _, _, actions)) = episode_snapshot.as_mut() {
                actions.extend(before.input_actions(&d, session_id, &text));
            }
            d.sessions
                .get(session_id)
                .map(|s| s.assist.pending_commands(chrono::Utc::now()))
                .unwrap_or_default()
        };
        let event_id = id("evt");
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
            "state":null,"outputs":null,"commands":commands,"acknowledged_command_ids":results.acknowledged_ids,"server_time":recorded_at,"phase":"dialogue","player_input":input,"_workshop_direct_input":input_handled})
    }
}
