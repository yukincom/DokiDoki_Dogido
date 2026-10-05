//! 明示入力と内部再送を受け付け、叫声・限定操作・知識質問・句相談・通常会話へ振り分ける。
//! 入力世代・観測鮮度・重複を照合し、jobs → dataの順でロックして保留状態と会話jobを更新する。
//! 記録制限を内部再送へ引き継ぎ、受付前後のworkshop状態を記録する。受理は実再生の成功を意味しない。
use super::{
    Dialogue, address_runtime, bridge, chat_context, empty_event, environment_runtime, fresh, id,
    language_runtime, observation_fresh, workshop_combat_input, workshop_focus, workshop_record,
};
use crate::{events::GameEvent, playback::Status as PlaybackStatus};
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::sync::watch;

impl Dialogue {
    pub(super) fn submit_inner(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
    ) -> Value {
        self.submit_recorded(
            selected,
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            workshop_record::Admission {
                forwarded: true,
                private: false,
            },
        )
    }
    #[allow(clippy::too_many_arguments)] // The last value is recording metadata, not routing authority.
    pub(super) fn submit_recorded(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        origin: workshop_record::Admission,
    ) -> Value {
        let sid = selected.map(str::to_owned).or_else(|| self.only_session());
        let (before, private) = {
            let d = self.data.lock().unwrap();
            let s = sid.as_deref().and_then(|sid| d.sessions.get(sid));
            (
                workshop_record::state(s.and_then(|s| s.haiku.workshop.as_ref())),
                origin.private
                    || s.is_some_and(|s| {
                        s.web.state.research.is_some()
                            || origin.forwarded
                                && s.record_private_generation.is_some_and(|(g, private)| {
                                    private
                                        && expected_generation
                                            .as_ref()
                                            .is_some_and(|(expected, _)| *expected == g)
                                })
                    }),
            )
        };
        let result = self.submit_impl(
            sid.as_deref(),
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            private,
        );
        if let Some(sid) = sid {
            let (after, mut recorded_input) = {
                let d = self.data.lock().unwrap();
                let row = d
                    .rows
                    .iter()
                    .find(|r| r["session_id"] == sid && r["turn_id"] == result["turn_id"]);
                let mut input = workshop_record::Input {
                    raw: text.into(),
                    private,
                    ..Default::default()
                };
                if let Some(row) = row {
                    input.private |= row["workshop_record_private"] == true;
                    input.epoch = row["epoch"].as_u64();
                    if row["player_input_text"] == text {
                        input.semantic = row["interpreted_player_input_text"]
                            .as_str()
                            .map(str::to_owned);
                    }
                }
                (
                    workshop_record::state(
                        d.sessions.get(&sid).and_then(|s| s.haiku.workshop.as_ref()),
                    ),
                    input,
                )
            };
            // Early rejection/classification has no semantic interpretation yet.
            if result["deduplicated"] == true {
                recorded_input.private = true;
            }
            self.record_workshop(
                &sid,
                if origin.forwarded {
                    "forwarded_admission"
                } else {
                    "input_admission"
                },
                &before,
                &after,
                &recorded_input,
                &result,
            );
        }
        result
    }
    #[allow(clippy::too_many_arguments)]
    fn submit_impl(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        private: bool,
    ) -> Value {
        if text.trim().is_empty() {
            return json!({"accepted":false,"reason":"empty_text"});
        }
        if text.chars().count() > 1000 {
            return json!({"accepted":false,"reason":"text_too_long"});
        }
        let session_id = selected.map(str::to_owned).or_else(|| self.only_session());
        let Some(session_id) = session_id else {
            return json!({"accepted":false,"reason":"select_one_session"});
        };
        let mut jobs = self.jobs.lock().unwrap();
        jobs.retain(|j| !j.is_finished());
        let vocalization =
            source.trim().eq_ignore_ascii_case("voice") && crate::vocalization::is_pure(text);
        if jobs.len() >= 16 && !vocalization {
            let mut d = self.data.lock().unwrap();
            self.tick_address(&mut d, &session_id);
            if d.sessions
                .get(&session_id)
                .and_then(|s| s.address.as_ref())
                .is_some_and(|p| p.input(text) == crate::address::Action::Accept)
            {
                Self::cancel_address(&mut d, &session_id, "host_chat_queue_full");
            }
            return json!({"accepted":false,"reason":"input_queue_full"});
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        let Some(s) = d.sessions.get_mut(&session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        if expected_generation
            .as_ref()
            .is_some_and(|(g, _)| *g != s.input_generation)
        {
            return json!({"accepted":false,"reason":"superseded_input"});
        }
        if vocalization {
            return self.accept_vocalization(&mut d, &session_id, text);
        }
        self.resolve_vocalization(s, None);
        if !observation_fresh(s) {
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        if let Some(p) = s.knowledge_queue.iter().find(|p| p.request.text == text) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,
                "turn_id":p.request.turn,"session_id":session_id});
        }
        let queued_knowledge = s
            .knowledge_checked
            .as_ref()
            .filter(|c| {
                expected_generation
                    .as_ref()
                    .is_some_and(|(g, _)| c.generation == *g)
            })
            .and_then(|c| c.original.clone());
        if let Some(p) = s.combat_input.as_ref().filter(|p| p.text == text) {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if let Some(p) = s
            .assist_pending
            .as_ref()
            .filter(|p| p.input.raw_text == text)
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if s.deferred_input.as_ref().is_some_and(|p| p.text == text) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        s.deferred_input = None;
        if let Some(w) = s
            .warning
            .as_ref()
            .filter(|w| w.input.as_deref() == Some(text))
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":w.turn,"session_id":session_id});
        }
        if s.pending_input.as_deref() == Some(text) && s.pending_warning.is_some() {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        if expected_generation.is_none() {
            s.input_generation = s.input_generation.wrapping_add(1);
        }
        s.record_private_generation = Some((s.input_generation, private));
        Self::cancel_combat_input(&mut d, &session_id);
        Self::cancel_haiku(&mut d, &session_id, "new_player_input");
        Self::cancel_assist(&mut d, &session_id);
        Self::cancel_light(&mut d, &session_id);
        let now = self.clock.elapsed().as_millis() as u64;
        let s = d.sessions.get_mut(&session_id).unwrap();
        s.last_player_input = Some(now);
        s.ambient.note_player_input(now);
        if s.warning.as_ref().is_some_and(|w| {
            w.actions
                .iter()
                .all(|a| a.delivery == crate::combat::model::Delivery::Ambient)
        }) {
            Self::cancel_warning(&mut d, &session_id, "new_player_input");
        }
        if !skip_assist
            && !workshop_focus::quiet(&d.sessions[&session_id])
            && let Some(result) = self.assist_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if let Some(event) = s.environment_latest.as_ref().or(s.latest.as_ref())
            && s.chat_allowed
            && !workshop_focus::active(s)
            && let Some(mut speech) = s.ambient.smell_query(event, text, now)
        {
            speech.delivery = crate::combat::model::Delivery::PlayerReply;
            s.pending_warning = Some(vec![speech]);
            s.pending_input = Some(text.to_owned());
            Self::cancel_chat(&mut d, &session_id, "smell_query");
            self.start_pending(&mut d, &mut jobs, &session_id);
            return json!({"accepted":true,"session_id":session_id,"reason":"smell_query"});
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !workshop_focus::quiet(s)
            && let Some(event) = s.latest.clone()
        {
            let decision = s.combat.input(
                &event,
                text,
                self.clock.elapsed().as_millis() as u64,
                &self.config.combat,
                &self.config.warnings,
            );
            if let Some(decision) = decision {
                Self::cancel_chat(&mut d, &session_id, "combat_input");
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    &session_id,
                    decision,
                    Some(text),
                    None,
                );
                self.start_pending(&mut d, &mut jobs, &session_id);
                let s = &d.sessions[&session_id];
                return json!({"accepted":true,"session_id":session_id,"reason":"combat_input",
                    "turn_id":if s.pending_warning.is_some(){None}else{s.warning.as_ref().map(|w|w.turn.clone())},"queued":s.pending_warning.is_some(),"state":s.mode});
            }
        }
        if let Some(result) =
            self.queue_knowledge_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if s.warning
            .as_ref()
            .is_some_and(|w| w.actions.iter().any(|a| a.kind == "workshop_resume"))
        {
            Self::cancel_warning(&mut d, &session_id, "new_player_input");
        }
        if !combat_classified
            && let Some(result) =
                self.classify_paused_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !workshop_combat_input::allowed(s) || s.warning.is_some() {
            if fresh(s)
                && s.warning.as_ref().is_some_and(|w| {
                    w.actions
                        .iter()
                        .all(|a| a.delivery == crate::combat::model::Delivery::UrgentEnvironment)
                })
            {
                s.deferred_input = Some(environment_runtime::DeferredInput {
                    text: text.to_owned(),
                    source: source.to_owned(),
                    previous_turn: None,
                });
                return json!({"accepted":true,"queued":true,"session_id":session_id,"reason":"after_environment_warning"});
            }
            return json!({"accepted":false,"reason":"fresh_safe_snapshot_required"});
        }
        self.tick_workshop(&mut d, &session_id);
        if let Some(result) = self.check_address_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let (address_reply, replay) = match self.address_input(&mut d, &session_id, text, source) {
            address_runtime::Input::Pass => (None, None),
            address_runtime::Input::Repair(reply) => (Some(reply), None),
            address_runtime::Input::Replay(original) => (None, Some(original)),
            address_runtime::Input::Consumed(result) => return result,
        };
        let host_chat_confirmed = replay.is_some();
        let replay = replay.or(queued_knowledge);
        let text = replay.as_ref().map_or(text, |p| p.text.as_str());
        let source = replay.as_ref().map_or(source, |p| p.source.as_str());
        let private = private || replay.as_ref().is_some_and(|p| p.record_private);
        let s = d.sessions.get_mut(&session_id).unwrap();
        let active_turn = s.current_turn.clone();
        // 同じ進行中入力の二重配送を、割り込みやLLM再呼出しにしない。
        if let Some(last) = d
            .rows
            .iter()
            .rev()
            .find(|r| r["turn_id"] == active_turn && r["player_input_text"] == text)
            && matches!(
                last["playback_status"].as_str(),
                Some("generating" | "queued" | "started")
            )
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":last["turn_id"]});
        }
        Self::cancel_chat(&mut d, &session_id, "new_player_input");
        self.tick_foreground(&mut d, &session_id);
        let text_prompt = d.text_prompts.clone();
        let text_prompt_version = d.text_prompt_version;
        let s = d.sessions.get_mut(&session_id).unwrap();
        let workshop = Self::workshop_view(s, text);
        let (interpreted_text, asr_corrections) =
            crate::contextual_asr::for_workshop(text, source, &json!(workshop));
        if !asr_corrections.is_empty() {
            tracing::info!(event="asr_fix_conversation",session_id,original=text,
                interpreted=interpreted_text,applied=?asr_corrections);
        }
        let poem_input = crate::poem_input::parse(text, workshop.is_some());
        let poem_reference = s
            .haiku
            .workshop
            .as_ref()
            .map(|w| json!({"id":w.hud_id,"version":w.version,"open":w.open}));
        let event = s
            .latest
            .as_ref()
            .cloned()
            .unwrap_or_else(|| GameEvent::parse(empty_event(&s.name)).expect("preview event"));
        if let Some((_, Some(previous))) = expected_generation {
            s.history.replace_unanswered(&previous);
        }
        let resume_context = s.foreground.resume_prompt(text);
        let event_digest = s
            .history
            .situation_lines()
            .iter()
            .map(|n| format!("- {n}"))
            .chain(s.combat_digest.iter().map(|n| format!("- {n}")))
            .chain((!resume_context.is_empty()).then_some(resume_context))
            .collect::<Vec<_>>()
            .join("\n");
        let history = s.history.rows();
        let conversation_history = s.history.lines();
        let chat_native = match chat_context::capture(
            s,
            &event,
            &self.config.combat,
            crate::chat_materials::CompletedHistory {
                conversation_history: conversation_history.clone(),
                conversation_turns: history.clone(),
                event_digest: event_digest.clone(),
            },
            workshop.as_ref(),
        ) {
            Ok(native) => native,
            Err(error) => {
                tracing::warn!(event="chat_snapshot_rejected", session_id, %error);
                return json!({"accepted":false,"reason":"chat_context_unavailable"});
            }
        };
        if (poem_input.is_some() || crate::haiku_memory::clear_requested(text))
            && let Some(w) = s.haiku.workshop.as_mut()
        {
            w.followup = crate::workshop_followup::Stage::Discussion;
        }
        let audit_before = workshop_record::state(s.haiku.workshop.as_ref());
        s.epoch += 1;
        let epoch = s.epoch;
        let turn = replay
            .as_ref()
            .map_or_else(|| id("turn"), |p| p.turn.clone());
        if address_reply.is_some()
            && let Some(p) = s.address.as_mut()
        {
            p.repair = Some((turn.clone(), false));
        }
        if let Some(checked) = s.knowledge_checked.as_ref()
            && checked.original.as_ref().is_some_and(|r| r.turn == turn)
            && let Some(audit) = &checked.audit
        {
            audit.transferred();
        }
        s.current_turn = turn.clone();
        s.status = PlaybackStatus::Generating;
        let (language_active, language_state) = language_runtime::context(s, text);
        let input = json!({"audit_before":audit_before,"audit_private":private,"model":self.config.model,"max_tokens":self.config.max_tokens,"reading_engine":self.config.reading_engine,"workshop":workshop,
            "poem_input":poem_input,"poem_reference":poem_reference,"operation_id":turn,
            "source":source,"language_active":language_active,"language_state":language_state,
            "address_reply":address_reply,"host_chat_confirmed":host_chat_confirmed,
            "input_at_ms":now,"previous_activity_ms":s.foreground.last_player_at.max(s.last_completed_conversation),
            "text":text,"interpreted_text":interpreted_text,"history":history,"conversation_history":conversation_history,
            "event_digest":event_digest,"event":event});
        let mut input = input;
        if s.text_workshop {
            input["text_workshop_prompt"] = text_prompt.unwrap_or(Value::Null);
            input["text_prompt_version"] = text_prompt_version.into();
        }
        let mut input = bridge::Input::native(input, event, chat_native);
        input.retained_history_count = s.history.retained_count();
        if workshop.is_none()
            && s.web.state.research.is_none()
            && poem_input.is_none()
            && !crate::haiku_memory::memory_candidate(text)
        {
            s.history.push(&turn, "user", text);
        }
        let (cancel, rx) = watch::channel(false);
        s.cancel = Some(cancel);
        if replay.is_none() && d.rows.len() == 200 {
            d.rows.pop_front();
        }
        let mut new_row = json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":session_id,"category":"speech","text":"",
            "created_at":chrono::Utc::now(),"input_at_ms":now,"epoch":epoch,"reference_ids":[],"output_mode":"both","player_input_text":text,"source":source,"playback_status":"generating",
            "interpreted_player_input_text":interpreted_text,"asr_corrections":asr_corrections,
            "workshop_id":workshop.as_ref().map(|w| &w["workshop_id"]),"workshop_fixed_close":false,"workshop_state_before":audit_before,"workshop_record_private":private});
        if let Some(original) = &replay {
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                row["playback_status"] = "generating".into();
                row["epoch"] = epoch.into();
                row["interpreted_player_input_text"] =
                    new_row["interpreted_player_input_text"].clone();
                row["asr_corrections"] = new_row["asr_corrections"].clone();
                row["workshop_state_before"] = new_row["workshop_state_before"].clone();
                row["workshop_record_private"] = new_row["workshop_record_private"].clone();
                if host_chat_confirmed {
                    row["language_status"] = "host_chat".into();
                    row["conversation_route"] = "casual".into();
                } else {
                    row["category"] = "speech".into();
                    row["workshop_id"] = new_row["workshop_id"].clone();
                    row["workshop_fixed_close"] = new_row["workshop_fixed_close"].clone();
                    row["resolution"] = "knowledge_resumed".into();
                }
                row["input_at_ms"] = original.input_at.into();
                row["resumed_at"] = chrono::Utc::now().to_rfc3339().into();
            } else {
                // 長い戦闘で表示履歴200件から落ちても、保留質問のIDを復元する。
                if d.rows.len() == 200 {
                    d.rows.pop_front();
                }
                new_row["input_at_ms"] = original.input_at.into();
                new_row["resumed_at"] = chrono::Utc::now().to_rfc3339().into();
                d.rows.push_back(new_row);
            }
        } else {
            d.rows.push_back(new_row);
        }
        d.revision += 1;
        drop(d);
        tracing::info!(
            event = "dialogue_input",
            session_id,
            turn_id = turn,
            text,
            source
        );
        let this = self.clone();
        let sid = session_id.clone();
        let tid = turn.clone();
        let job = tokio::spawn(async move {
            this.run_turn(sid, tid, epoch, input, rx).await;
        });
        jobs.push(job);
        json!({"accepted":true,"session_id":session_id,"turn_id":turn})
    }
}
