//! 明示入力と内部再送を受け付け、叫声・限定操作・知識質問・句相談・通常会話へ振り分ける。
//! 入力世代・観測鮮度・重複を照合し、jobs → dataの順でロックして保留状態と会話jobを更新する。
//! 記録制限を内部再送へ引き継ぎ、受付前後のworkshop状態を記録する。受理は実再生の成功を意味しない。
use super::{
    Dialogue, bridge, chat_context, empty_event, environment_runtime, fresh, id, observation_fresh,
    workshop_combat_input, workshop_focus, workshop_record,
};
use crate::{events::GameEvent, playback::Status as PlaybackStatus};
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::sync::watch;

impl Dialogue {
    #[cfg(test)]
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
        self.submit_recorded_guarded(
            selected,
            text,
            source,
            skip_assist,
            expected_generation,
            combat_classified,
            origin,
            None,
            None,
        )
    }
    /// 非同期分類の再送は世代に加え接続個体を、同じ受付ロック内で照合する。
    #[allow(clippy::too_many_arguments)]
    pub(super) fn submit_recorded_guarded(
        self: &Arc<Self>,
        selected: Option<&str>,
        text: &str,
        source: &str,
        skip_assist: bool,
        expected_generation: Option<(u64, Option<String>)>,
        combat_classified: bool,
        origin: workshop_record::Admission,
        expected_instance: Option<&str>,
        expected_voice: Option<Option<&str>>,
    ) -> Value {
        let sid = selected.map(str::to_owned).or_else(|| self.only_session());
        let (before, private) = {
            let d = self.data.lock().unwrap();
            let s = sid.as_deref().and_then(|sid| d.sessions.get(sid));
            (
                workshop_record::state(s.and_then(|s| s.haiku.workshop.as_ref())),
                origin.private
                    || s.is_some_and(|s| {
                        origin.forwarded
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
            expected_instance,
            expected_voice,
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
        expected_instance: Option<&str>,
        expected_voice: Option<Option<&str>>,
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
            let d = self.data.lock().unwrap();
            if expected_instance.is_some_and(|instance| {
                d.sessions
                    .get(&session_id)
                    .is_none_or(|s| s.instance_id != instance)
            }) {
                return json!({"accepted":false,"reason":"superseded_session"});
            }
            return json!({"accepted":false,"reason":"input_queue_full"});
        }
        let mut d = self.data.lock().unwrap();
        if d.stopped {
            return json!({"accepted":false,"reason":"server_stopping"});
        }
        self.tick_workshop(&mut d, &session_id);
        self.prune_classifier_inputs(&mut d, &session_id);
        if let Some(generation) = expected_voice {
            let Some(s) = d.sessions.get(&session_id) else {
                return json!({"accepted":false,"reason":"unknown_session_id"});
            };
            if let Some(rejection) = super::text_delivery::voice_rejection(s, generation) {
                return rejection;
            }
            Self::record_voice_input_locked(&mut d, Some(&session_id), text, source);
        }
        let Some(s) = d.sessions.get_mut(&session_id) else {
            return json!({"accepted":false,"reason":"unknown_session_id"});
        };
        if expected_instance.is_some_and(|instance| instance != s.instance_id) {
            return json!({"accepted":false,"reason":"superseded_session"});
        }
        // 内部再送の結果失効は終端取消。結果なしへ戻して別BERTで再分類しない。
        if expected_generation.is_some()
            && s.request_classification
                .binding(s.input_generation, text, source)
                .is_some_and(|binding| !binding.current(s))
        {
            return json!({"accepted":false,"reason":"superseded_classifier_mode"});
        }
        if source.trim().eq_ignore_ascii_case("voice")
            && s.delivery.enabled
            && !s.delivery.voice_enabled
        {
            return json!({"accepted":false,"reason":"voice_disabled"});
        }
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
        let quiet = crate::combat::core::wants_quiet(text);
        // 明示停止は分類待ちと観測の鮮度より先に処理する。待機入力も再開しない。
        if quiet {
            self.cancel_reply_queue(&mut d, &session_id, "player_requested_quiet");
            Self::cancel_chat(&mut d, &session_id, "player_requested_quiet");
            Self::cancel_haiku(&mut d, &session_id, "player_requested_quiet");
            Self::cancel_warning(&mut d, &session_id, "player_requested_quiet");
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if !observation_fresh(s) {
            return if quiet {
                json!({"accepted":true,"reason":"player_requested_quiet"})
            } else {
                json!({"accepted":false,"reason":"fresh_safe_snapshot_required"})
            };
        }
        if !quiet
            && self.config.request_classifier.enabled()
            && let Some(binding) = super::request_runtime::Binding::capture(s)
            && self
                .config
                .request_classifier
                .model_root(binding.mode)
                .is_none()
        {
            return json!({"accepted":false,"reason":"classifier_model_unavailable","classifier_mode":binding.mode});
        }
        let current_binding = super::request_runtime::Binding::capture(s);
        let current_classified_input = !self.config.request_classifier.enabled()
            || s.request_classification
                .binding(s.input_generation, text, source)
                .is_some_and(|b| b.current(s));
        let active_turn = s.current_turn.clone();
        // 非緊急警告が回答の後ろへ待機していても、再送を別の保留質問にしない。
        if s.cancel.is_some()
            && let Some(row) = d.rows.iter().find(|r| {
                r["session_id"] == session_id
                    && r["turn_id"] == active_turn
                    && r["player_input_text"] == text
                    && r["source"] == source
                    && (!self.config.request_classifier.enabled()
                        || r["classifier_binding"] == json!(current_binding))
                    && matches!(
                        r["playback_status"].as_str(),
                        Some("generating" | "queued" | "started")
                    )
            })
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":row["turn_id"]});
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if let Some(p) = s.reply_queue.iter().find(|p| {
            p.request.text == text
                && p.request.source == source
                && p.request.binding.as_ref().is_none_or(|b| b.current(s))
        }) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,
                "turn_id":p.request.turn,"session_id":session_id});
        }
        let queued_reply = s
            .reply_checked
            .as_ref()
            .filter(|c| {
                expected_generation
                    .as_ref()
                    .is_some_and(|(g, _)| c.generation == *g)
            })
            .and_then(|c| c.original.clone());
        let queued_dispatch =
            s.reply_checked.as_ref().is_some_and(|c| c.dispatch_input) && queued_reply.is_some();
        if let Some(p) = s.combat_input.as_ref().filter(|p| {
            p.text == text
                && p.request.source == source
                && p.request.binding.as_ref().is_none_or(|b| b.current(s))
        }) {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if let Some(p) = s
            .assist_pending
            .as_ref()
            .filter(|p| p.input.raw_text == text && current_classified_input)
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if let Some(p) = s
            .request_classification
            .pending
            .as_ref()
            .filter(|p| p.text == text && p.source == source && p.binding.current(s))
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":p.turn,"session_id":session_id});
        }
        if s.deferred_input.as_ref().is_some_and(|p| {
            p.text == text && p.source == source && p.binding.as_ref().is_none_or(|b| b.current(s))
        }) {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        if let Some(w) = s
            .warning
            .as_ref()
            .filter(|w| w.input.as_deref() == Some(text) && current_classified_input)
        {
            return json!({"accepted":true,"deduplicated":true,"turn_id":w.turn,"session_id":session_id});
        }
        if current_classified_input
            && s.pending_input.as_deref() == Some(text)
            && s.pending_warning.is_some()
        {
            return json!({"accepted":true,"deduplicated":true,"queued":true,"session_id":session_id});
        }
        // 相槌・くしゃみを含む新入力は、実再生が始まった台詞を切らず既存FIFOへ保持する。
        // 「静かにして」等の明示停止は従来の停止処理へ渡す。再生前の入力優先は維持する。
        if super::reply_queue::playback_started(&d, &session_id)
            && !crate::combat::core::wants_quiet(text)
            && let Some(result) =
                self.queue_reply_input(&mut d, &mut jobs, &session_id, text, source, true)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        s.deferred_input = None;
        if expected_generation.is_none() {
            s.input_generation = s.input_generation.wrapping_add(1);
        }
        s.record_private_generation = Some((s.input_generation, private));
        super::body_interest_runtime::cancel(s);
        s.body_requests.cancel();
        Self::cancel_request_classification(&mut d, &session_id);
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
        // 危険中の分類待ちは既存FIFOに予約し、新入力で先の知識質問を失わない。
        // 有効な同一入力の結果がない間は、剣・敵・馬の分岐へ進まない。
        if !quiet
            && self.config.request_classifier.enabled()
            && super::request_runtime::classification(&d.sessions[&session_id], text, source)
                .is_none()
        {
            if let Some(result) =
                self.queue_reply_input(&mut d, &mut jobs, &session_id, text, source, true)
            {
                return result;
            }
            return self.start_request_classification(&mut d, &mut jobs, &session_id, text, source);
        }
        if !quiet
            && let Some(result) =
                self.body_request_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        if !quiet
            && !skip_assist
            && (!workshop_focus::quiet(&d.sessions[&session_id])
                || super::request_runtime::classification(&d.sessions[&session_id], text, source)
                    .is_some_and(|r| {
                        r.prediction.label == crate::request_classifier::Label::EquipSword
                    }))
            && let Some(result) = self.assist_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        if let Some(event) = s
            .environment_latest
            .as_ref()
            .or(s.latest.as_ref())
            .map(|raw| super::body_runtime::event(s, raw))
            && s.chat_allowed
            && !workshop_focus::active(s)
            && let Some(mut speech) = s.ambient.smell_query(&event, text, now)
        {
            speech.delivery = crate::combat::model::Delivery::PlayerReply;
            s.pending_warning = Some(vec![speech]);
            s.pending_input = Some(text.to_owned());
            Self::cancel_chat(&mut d, &session_id, "smell_query");
            self.start_pending(&mut d, &mut jobs, &session_id);
            return json!({"accepted":true,"session_id":session_id,"reason":"smell_query"});
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        let classified = super::request_runtime::classification(s, text, source).is_some_and(|r| {
            r.prediction.label == crate::request_classifier::Label::EnemyInformation
        });
        if (quiet || classified || !workshop_focus::quiet(s))
            && let Some(raw) = s.latest.clone()
        {
            // 分類済みの現在モブ質問はドギドの身体観測を使う。戦況・静粛は従来の観測を保つ。
            let event = if classified && !quiet {
                super::body_runtime::event(s, &raw)
            } else {
                raw
            };
            let decision = s.combat.input_with_enemy_request(
                &event,
                text,
                self.clock.elapsed().as_millis() as u64,
                &self.config.combat,
                &self.config.warnings,
                classified,
            );
            if let Some(decision) = decision {
                if crate::combat::core::wants_quiet(text) {
                    self.cancel_reply_queue(&mut d, &session_id, "player_requested_quiet");
                }
                Self::cancel_chat(&mut d, &session_id, "combat_input");
                self.apply_combat_decision(
                    &mut d,
                    &mut jobs,
                    &session_id,
                    decision,
                    super::combat_runtime::DecisionContext {
                        input: Some(text),
                        reason: None,
                        combat_priority: false,
                    },
                );
                self.start_pending(&mut d, &mut jobs, &session_id);
                let s = &d.sessions[&session_id];
                return json!({"accepted":true,"session_id":session_id,"reason":"combat_input",
                    "turn_id":if s.pending_warning.is_some(){None}else{s.warning.as_ref().map(|w|w.turn.clone())},"queued":s.pending_warning.is_some(),"state":s.mode});
            }
        }
        // 分類器未設定時の呼び寄せ判定も、通常会話の保留より先に既存の所有者へ渡す。
        // 正本DBの明示質問は従来どおりこの操作判定へ入れない。
        let classified =
            super::request_runtime::classification(&d.sessions[&session_id], text, source)
                .is_some();
        let knowledge = !classified
            && crate::knowledge::query::from_normalized(
                &crate::player_text::prepare(text).normalized_text,
            )
            .is_some();
        if (queued_reply.is_none() || queued_dispatch)
            && !knowledge
            && let Some(result) =
                self.horse_assist_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        if let Some(result) =
            self.queue_reply_input(&mut d, &mut jobs, &session_id, text, source, true)
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
            && super::request_runtime::classification(&d.sessions[&session_id], text, source)
                .is_some_and(|c| c.mode == crate::request_classifier::Mode::Senryu)
            && let Some(result) =
                self.hold_paused_input(&mut d, &mut jobs, &session_id, text, source)
        {
            return result;
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        let ordinary_reply = super::reply_queue::ordinary(
            s,
            text,
            super::request_runtime::classification(s, text, source),
        );
        let input_allowed = if ordinary_reply {
            observation_fresh(s)
        } else {
            workshop_combat_input::allowed(s)
        };
        if !input_allowed || s.warning.is_some() || s.pending_warning.is_some() {
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
                    generation: s.input_generation,
                    instance: s.instance_id.clone(),
                    binding: s
                        .request_classification
                        .binding(s.input_generation, text, source)
                        .cloned()
                        .or_else(|| super::request_runtime::Binding::capture(s)),
                    classification: super::request_runtime::classification(s, text, source)
                        .cloned(),
                });
                return json!({"accepted":true,"queued":true,"session_id":session_id,"reason":"after_environment_warning"});
            }
            return json!({"accepted":false,"reason":if input_allowed {
                "warning_in_progress"
            } else { "fresh_safe_snapshot_required" }});
        }
        // 環境警告による再送も、同じ接続・原文の元入力へ戻す。
        // 振り分け後に復元することで操作や警告の判断を変えず、下の共通再開処理で
        // 元turn・受付時刻を保ち、新しいepochだけを割り当てて旧通知の取り違えを防ぐ。
        let replay = queued_reply.or_else(|| {
            let previous = expected_generation.as_ref()?.1.as_deref()?;
            let s = &d.sessions[&session_id];
            let row = d.rows.iter().find(|row| {
                row["turn_id"] == previous
                    && row["session_id"] == session_id
                    && row["session_instance"] == s.instance_id
                    && row["player_input_text"] == text
                    && row["source"] == source
            })?;
            Some(super::reply_queue::Request {
                turn: previous.to_owned(),
                text: text.to_owned(),
                source: source.to_owned(),
                input_at: row["input_at_ms"].as_u64()?,
                record_private: row["workshop_record_private"] == true,
                classification: super::request_runtime::classification(s, text, source).cloned(),
                binding: s
                    .request_classification
                    .binding(s.input_generation, text, source)
                    .cloned()
                    .or_else(|| super::request_runtime::Binding::capture(s)),
            })
        });
        let text = replay.as_ref().map_or(text, |p| p.text.as_str());
        let source = replay.as_ref().map_or(source, |p| p.source.as_str());
        let private = private || replay.as_ref().is_some_and(|p| p.record_private);
        // 警告後に元本文を再送するときも、元入力の分類を引き継ぐ。
        if let Some(original) = &replay {
            let s = d.sessions.get_mut(&session_id).unwrap();
            s.request_classification.restore(
                s.input_generation,
                original.binding.clone(),
                original.classification.clone(),
            );
        }
        let s = d.sessions.get_mut(&session_id).unwrap();
        let active_turn = s.current_turn.clone();
        // 同じ進行中入力の二重配送を、割り込みやLLM再呼出しにしない。
        if let Some(last) = d.rows.iter().rev().find(|r| {
            r["turn_id"] == active_turn
                && r["player_input_text"] == text
                && r["source"] == source
                && (!self.config.request_classifier.enabled()
                    || r["classifier_binding"]
                        == json!(super::request_runtime::Binding::capture(
                            &d.sessions[&session_id]
                        )))
        }) && matches!(
            last["playback_status"].as_str(),
            Some("generating" | "queued" | "started")
        ) {
            return json!({"accepted":true,"deduplicated":true,"turn_id":last["turn_id"]});
        }
        Self::cancel_chat(&mut d, &session_id, "new_player_input");
        self.tick_foreground(&mut d, &session_id);
        let text_prompt = d.text_prompts.clone();
        let text_prompt_version = d.text_prompt_version;
        let input_occurred_at = replay.as_ref().map_or_else(
            || json!(chrono::Utc::now()),
            |original| {
                d.rows.iter().find(|row| {
                    row["session_id"] == session_id && row["turn_id"] == original.turn
                }).map_or(Value::Null, |row| row["created_at"].clone())
            },
        );
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
        if s.haiku.foreground() { s.history.defer_for_haiku(); }
        let history = s.model_history();
        let conversation_history = super::history::History::lines_for(&history);
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
        if let Some(checked) = s.reply_checked.as_ref()
            && checked.original.as_ref().is_some_and(|r| r.turn == turn)
            && let Some(audit) = &checked.audit
        {
            audit.transferred();
        }
        s.current_turn = turn.clone();
        s.status = PlaybackStatus::Generating;
        let input = json!({"audit_before":audit_before,"audit_private":private,"model":self.config.model,"max_tokens":self.config.max_tokens,"reading_engine":self.config.reading_engine,"workshop":workshop,
            "poem_input":poem_input,"poem_reference":poem_reference,"operation_id":turn,
            "session_instance":s.instance_id,"feedback_workshop":workshop,
            "source":source,
            "input_at_ms":now,"previous_activity_ms":s.foreground.last_player_at.max(s.last_completed_conversation),
            "text":text,"interpreted_text":interpreted_text,"history":history,"conversation_history":conversation_history,
            "event_digest":event_digest,"event":event});
        let mut input = input;
        input["resumed_reply"] = replay.is_some().into();
        if s.text_workshop {
            input["text_workshop_prompt"] = text_prompt.unwrap_or(Value::Null);
            input["text_prompt_version"] = text_prompt_version.into();
        }
        let mut input = bridge::Input::native(input, event, chat_native);
        input.retained_history_count = s.history.retained_count();
        input.request_classification =
            super::request_runtime::classification(s, text, source).cloned();
        if workshop.is_none()
            && poem_input.is_none()
            && !crate::haiku_memory::memory_candidate(text)
        {
            s.history.push_input(&turn, text, input_occurred_at.clone());
        }
        let (cancel, rx) = watch::channel(false);
        s.cancel = Some(cancel);
        if replay.is_none() && d.rows.len() == 200 {
            d.rows.pop_front();
        }
        let mut new_row = json!({"utterance_id":id("utt"),"turn_id":turn,"session_id":session_id,"category":"speech","text":"",
            "created_at":input_occurred_at,"input_at_ms":now,"epoch":epoch,"session_instance":input["session_instance"],"reference_ids":[],"output_mode":"both","player_input_text":text,"source":source,"playback_status":"generating",
            "interpreted_player_input_text":interpreted_text,"asr_corrections":asr_corrections,
            "workshop_id":workshop.as_ref().map(|w| &w["workshop_id"]),"workshop_fixed_close":false,"workshop_state_before":audit_before,"workshop_record_private":private});
        new_row["combat_resumable"] = ordinary_reply.into();
        new_row["request_classification"] = json!(input.request_classification);
        new_row["classifier_binding"] = json!(super::request_runtime::Binding::capture(
            &d.sessions[&session_id]
        ));
        if let Some(original) = &replay {
            d.sessions
                .get_mut(&session_id)
                .unwrap()
                .delivery
                .forget_reply(&turn);
            if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
                // 警告前の本文・display ID・配送結果を新しい試行へ流用しない。
                // 元turnと受付時刻だけを保持し、古いepochの通知はupdate側で拒否する。
                new_row["created_at"] = row["created_at"].clone();
                *row = new_row.clone();
                row["playback_status"] = "generating".into();
                row["epoch"] = epoch.into();
                row["interpreted_player_input_text"] =
                    new_row["interpreted_player_input_text"].clone();
                row["asr_corrections"] = new_row["asr_corrections"].clone();
                row["workshop_state_before"] = new_row["workshop_state_before"].clone();
                row["workshop_record_private"] = new_row["workshop_record_private"].clone();
                row["category"] = "speech".into();
                row["workshop_id"] = new_row["workshop_id"].clone();
                row["workshop_fixed_close"] = new_row["workshop_fixed_close"].clone();
                row["resolution"] = "reply_resumed".into();
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
        if let Some(checked) = d
            .sessions
            .get_mut(&session_id)
            .unwrap()
            .reply_checked
            .as_mut()
            && checked.original.as_ref().is_some_and(|r| r.turn == turn)
        {
            // 予約から進行中turnへの引継ぎをspawn前に完了する。
            // 高速に終わったturnの次質問再開が、残った予約に阻まれないようにする。
            checked.original = None;
            checked.audit = None;
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
