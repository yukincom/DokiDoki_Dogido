//! 受理済み入力を直列枠で実行し、観測と句の再取得から応答生成・保存・音声配送へ進める。
//! 待機中と実行中の取消・観測鮮度を監視し、状態反映は同じturnとepochを検証するupdateへ渡す。
//! 実再生結果の反映後に通常会話からの発句再開を判定し、受付時の情報と実行結果をworkshop記録へ結ぶ。
use super::{
    Dialogue, bridge, chat_context, fresh, history, monitor, workshop_combat_input, workshop_focus,
    workshop_record,
};
use crate::playback::Status as PlaybackStatus;
use serde_json::{Value, json};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::watch;

impl Dialogue {
    pub(super) async fn run_turn(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        mut input: bridge::Input,
        cancel: watch::Receiver<bool>,
    ) {
        let before = input
            .as_object_mut()
            .and_then(|o| o.remove("audit_before"))
            .unwrap_or(Value::Null);
        let private = input
            .as_object_mut()
            .and_then(|o| o.remove("audit_private"))
            == Some(Value::Bool(true));
        let attempt = workshop_record::Attempt::new(
            before,
            workshop_record::Input {
                raw: input["text"].as_str().unwrap_or("").into(),
                semantic: input["interpreted_text"].as_str().map(str::to_owned),
                private,
                epoch: Some(epoch),
            },
        );
        self.clone()
            .run_turn_body(sid.clone(), turn.clone(), epoch, input, cancel)
            .await;
        self.record_workshop_turn(&sid, &turn, &attempt);
    }
    /// 通常turnを直列枠内で実行する。待ち時間中の観測・保存済み句を取り込み直し、
    /// routing/生成 → 状態再照合・保存 → 読み上げ → 実再生結果の反映、まで所有する。
    /// dataは必要な照合区間ごとに取り直すため、この間もゲーム観測を受理できる。
    async fn run_turn_body(
        self: Arc<Self>,
        sid: String,
        turn: String,
        epoch: u64,
        mut input: bridge::Input,
        mut cancel: watch::Receiver<bool>,
    ) {
        let started = Instant::now();
        let mut permit = Some(
            tokio::select! { _=bridge::cancelled(&mut cancel)=>{self.update(&sid,&turn,epoch,PlaybackStatus::Cancelled,None);return;}, p=self.serial.acquire()=>p.unwrap() },
        );
        // Pick up rain/smell changes and reactions completed while waiting for
        // the preceding turn. The resulting snapshot is fixed for this decision.
        {
            let d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get(&sid).filter(|s| s.epoch == epoch)
                && let Some((event, previous)) = input.snapshot.as_deref()
            {
                let event = s.latest.as_ref().unwrap_or(event);
                input.horse_recall =
                    super::horse_recall_runtime::capture(s, input["text"].as_str().unwrap_or(""));
                let mut history = previous.context.history.clone();
                history.conversation_turns = s.history.refresh_before_input(
                    &turn,
                    &history.conversation_turns,
                    input.retained_history_count,
                );
                history.conversation_history =
                    history::History::lines_for(&history.conversation_turns);
                let workshop = input["workshop"].is_object().then_some(&input["workshop"]);
                if let Ok(native) =
                    chat_context::capture(s, event, &self.config.combat, history, workshop)
                {
                    let event = event.clone();
                    input["history"] = json!(native.context.history.conversation_turns);
                    input["conversation_turns"] = input["history"].clone();
                    input["conversation_history"] =
                        native.context.history.conversation_history.clone().into();
                    input.snapshot = Some(std::sync::Arc::new((event, native)));
                }
            }
        }
        // An earlier authorized save may have completed while this turn waited.
        // Rebuild the read-only context before planning against that new version.
        if input["workshop"].is_object() && input["poem_input"].is_null() {
            let mut d = self.data.lock().unwrap();
            if let Some(s) = d.sessions.get_mut(&sid)
                && s.epoch == epoch
                && s.haiku.workshop.as_ref().is_some_and(|w| {
                    w.open
                        && input["workshop"]["workshop_id"] == w.hud_id
                        && input["workshop"]["version"] != w.version
                })
            {
                let text = input["text"].as_str().unwrap_or("").to_owned();
                if let Some(view) = Self::workshop_view(s, &text) {
                    input["workshop"] = view;
                }
            }
        }
        // 観測更新が途切れた場合も、古い場所の返答・音声を続けない。
        let owner = self.clone();
        let monitor_sid = sid.clone();
        let mut monitor_cancel = cancel.clone();
        let workshop_id = input["workshop"]["workshop_id"].as_str().map(str::to_owned);
        let provisional_target = input["workshop"]["provisional"].as_str().map(str::to_owned);
        let monitor = monitor::Monitor::spawn(async move {
            loop {
                tokio::select! {
                    _=bridge::cancelled(&mut monitor_cancel)=>break,
                    _=tokio::time::sleep(Duration::from_millis(200))=>{
                        let mut d=owner.data.lock().unwrap();
                        owner.tick_workshop(&mut d, &monitor_sid);
                        let stale = d.sessions.get(&monitor_sid).is_some_and(|s| {
                            // 閉じる返答の配送中も、開始時と同じ安定した敵なら最後まで話せる。
                            let same_threat = provisional_target.as_ref().is_some_and(|key| {
                                workshop_combat_input::ready(s, owner.clock.elapsed().as_millis() as u64, &owner.config).as_ref() == Some(key)
                            });
                            let workshop_allowed = workshop_id.is_some()
                                && (workshop_focus::quiet_observation(s)
                                    || workshop_combat_input::provisional(s) || same_threat);
                            s.epoch == epoch && !(fresh(s) || workshop_allowed)
                        });
                        if stale {Self::cancel_chat(&mut d,&monitor_sid,"stale_observation");break;}
                        let expired = workshop_id.as_ref().is_some_and(|wid| d.sessions.get(&monitor_sid)
                            .filter(|s| s.epoch==epoch).is_some_and(|s| s.haiku.workshop.as_ref()
                                .is_none_or(|w| w.hud_id != *wid || (!w.is_open() && w.close_reason.as_deref()!=Some("explicit_close")))));
                        if expired {Self::cancel_chat(&mut d,&monitor_sid,"workshop_changed");break;}
                    }
                }
            }
        });
        let mut completed = false;
        self.prepare_web(&sid, epoch, &mut input, &cancel).await;
        let web_result = self.web_turn(&sid, epoch, &input, &mut cancel).await;
        let result = if let Err(error) = web_result {
            Err(error)
        } else if let Some(result) = web_result.unwrap() {
            Ok(result)
        } else if input["address_reply"].is_string() {
            match self.select_foreground(&sid, &turn, epoch, crate::foreground::Route::Learning) {
                Ok(()) => bridge::render(&self.config, &self.llm, input.clone(), &mut cancel).await,
                Err(error) => Err(error),
            }
        } else if input["poem_input"].is_object() {
            self.save_poem_input(&sid, epoch, &input, &mut cancel).await
        } else if crate::haiku_memory::clear_requested(input["text"].as_str().unwrap_or("")) {
            self.clear_lessons(&sid, epoch, &input).await
        } else if input["workshop"].is_object() {
            match self.render_workshop(&input, &mut cancel, &mut permit).await {
                Ok(workshop) if workshop["workshop_action"] == "unrelated" => {
                    let safe = self
                        .data
                        .lock()
                        .unwrap()
                        .sessions
                        .get(&sid)
                        .is_some_and(fresh);
                    if !safe {
                        Err(anyhow::anyhow!("combat_workshop_only"))
                    } else {
                        let mut ordinary = input.clone();
                        // 対話plannerには過去の通常会話と現在入力だけを渡す。
                        ordinary["workshop"] = Value::Null;
                        ordinary["workshop_fallback"] = true.into();
                        match bridge::render(&self.config, &self.llm, ordinary, &mut cancel).await {
                            Ok(mut result) => {
                                let mut reports = workshop["llm_reports"]
                                    .as_array()
                                    .cloned()
                                    .unwrap_or_default();
                                reports.extend(
                                    result["llm_reports"]
                                        .as_array()
                                        .cloned()
                                        .unwrap_or_default(),
                                );
                                for key in [
                                    "workshop_id",
                                    "workshop_action",
                                    "workshop_steps",
                                    "workshop_reason",
                                ] {
                                    result[key] = workshop[key].clone();
                                }
                                result["llm_reports"] = json!(reports);
                                Ok(result)
                            }
                            Err(error) => Err(error),
                        }
                    }
                }
                result => result,
            }
        } else {
            bridge::render_with_route(
                &self.config,
                &self.llm,
                input.clone(),
                &mut cancel,
                |route| self.select_foreground(&sid, &turn, epoch, route),
                |outcome| self.hold_language_handoff(&sid, &turn, epoch, &input, outcome),
            )
            .await
        };
        let result = match result {
            Ok(r) if r["memory_query"].is_object() => {
                self.recall_poems(&sid, epoch, &input, &r["memory_query"], &mut cancel)
                    .await
            }
            r => r,
        };
        match result {
            Ok(mut result) => {
                if let Err(error) = self.apply_horse_recall(
                    &sid,
                    &turn,
                    epoch,
                    &input,
                    &mut result,
                    *cancel.borrow(),
                ) {
                    result["text"] = "馬の状態が変わったから、呼び戻しは見送ったで。".into();
                    result["spoken_text"] = result["text"].clone();
                    result["horse_recall_status"] = "rejected".into();
                    result["horse_recall_reason"] = error.to_string().into();
                }
                if input["host_chat_confirmed"] == true {
                    result["language_status"] = "host_chat".into();
                    result["language_state"] = json!(crate::language::State::default());
                }
                if result["output_suppressed"] == true || self.workshop_combat_priority(&input) {
                    // 発言への評価と返答予定は残すが、古い句操作や音声は実行しない。
                    drop(permit.take());
                    if result["workshop_feedback"]["action"] == "praise" {
                        self.record_feedback(&sid, epoch, &input, &mut result).await;
                    }
                    self.update(&sid, &turn, epoch, PlaybackStatus::Cancelled, Some(&result));
                    monitor.finish().await;
                    drop(permit);
                    return;
                }
                self.apply_language_result(&sid, &turn, epoch, &result);
                {
                    // ディスクへの記録待ちも、戦闘音声の直列枠を占有し続けない。
                    let feedback = self.record_feedback(&sid, epoch, &input, &mut result);
                    tokio::pin!(feedback);
                    tokio::select! {
                        _ = &mut feedback => {},
                        _ = bridge::cancelled(&mut cancel) => {
                            drop(permit.take());
                            feedback.await;
                        },
                    }
                }
                if self.workshop_combat_priority(&input) {
                    self.update(&sid, &turn, epoch, PlaybackStatus::Cancelled, Some(&result));
                    monitor.finish().await;
                    drop(permit);
                    return;
                }
                if let Err(error) = self.apply_workshop_edit(&sid, epoch, &mut result).await {
                    if self.workshop_combat_priority(&input) {
                        self.update(&sid, &turn, epoch, PlaybackStatus::Cancelled, Some(&result));
                        monitor.finish().await;
                        drop(permit);
                        return;
                    }
                    result["text"] =
                        "句が変わったか、編集を続けられん状態になったわ。もう一度確認してな。"
                            .into();
                    result["spoken_text"] = result["text"].clone();
                    result["workshop_reason"] = error.to_string().into();
                    result["workshop_action"] = "fallback".into();
                }
                if let Some(outcome) = result.get("workshop_outcome").cloned()
                    && let Some(last) = result
                        .get_mut("workshop_steps")
                        .and_then(Value::as_array_mut)
                        .and_then(|s| s.last_mut())
                {
                    last["outcome"] = outcome;
                }
                if let Some(unsupported) = result.get("unsupported").cloned() {
                    result["error"] = unsupported;
                    self.update(
                        &sid,
                        &turn,
                        epoch,
                        PlaybackStatus::Unsupported,
                        Some(&result),
                    );
                } else if result["language_status"] == "awaiting_address" {
                    self.update(
                        &sid,
                        &turn,
                        epoch,
                        PlaybackStatus::NotSelected,
                        Some(&result),
                    );
                } else if result["text"].as_str().is_none_or(|s| s.is_empty()) {
                    self.update(&sid, &turn, epoch, PlaybackStatus::Quiet, Some(&result));
                } else if self.update(&sid, &turn, epoch, PlaybackStatus::Queued, Some(&result)) {
                    tracing::info!(
                        event = "dialogue_reply",
                        session_id = sid,
                        turn_id = turn,
                        text = result["text"].as_str().unwrap_or(""),
                        generation_ms = started.elapsed().as_millis() as u64
                    );
                    let output_config = self.output_config(&sid);
                    let text_displayed = if !output_config.audio_enabled {
                        self.wait_for_text(&sid, &turn, &mut cancel).await
                    } else {
                        false
                    };
                    let playback = if output_config.audio_enabled {
                        self.audio
                            .speak(
                                &output_config,
                                result["spoken_text"].as_str().unwrap_or(""),
                                &mut cancel,
                                || {
                                    self.update(&sid, &turn, epoch, PlaybackStatus::Started, None);
                                },
                            )
                            .await
                    } else {
                        Err(anyhow::anyhow!("audio_disabled"))
                    };
                    let status = match playback {
                        Ok(()) => PlaybackStatus::Completed,
                        Err(error) => {
                            result["error"] = error.to_string().into();
                            if *cancel.borrow() {
                                PlaybackStatus::Cancelled
                            } else if !output_config.audio_enabled {
                                result.as_object_mut().unwrap().remove("error");
                                PlaybackStatus::AudioDisabled
                            } else {
                                PlaybackStatus::Failed
                            }
                        }
                    };
                    // Web開始は音声プロセスの実終了と同じepochの確認後だけ。
                    self.web_playback(&sid, epoch, &result, status);
                    completed = self.update(&sid, &turn, epoch, status, Some(&result))
                        && (status == PlaybackStatus::Completed || text_displayed)
                        && result["workshop_action"].is_null()
                        && result["knowledge_status"].is_null()
                        && result["memory_action"].is_null();
                }
            }
            Err(error) => {
                self.update(
                    &sid,
                    &turn,
                    epoch,
                    if *cancel.borrow() {
                        PlaybackStatus::Cancelled
                    } else {
                        PlaybackStatus::Failed
                    },
                    Some(&json!({"error":error.to_string()})),
                );
            }
        }
        monitor.finish().await;
        drop(permit);
        if completed {
            self.try_start_haiku(&sid, true);
        }
        tracing::info!(
            event = "dialogue_finished",
            session_id = sid,
            turn_id = turn,
            total_ms = started.elapsed().as_millis() as u64
        );
    }
}
