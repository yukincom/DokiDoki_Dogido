//! 返答の生成・配送・再生結果を、同じturnとepochの表示台帳・sessionへ反映する。
//! dataのmutex下で現在性とworkshopの対象を照合し、再生完了またはテキスト表示確認後に履歴を確定する。
//! 状態反映後にロックを解放してworkshop記録を渡し、現在のturnへ反映できたかを返す。
use super::{Dialogue, knowledge_display, language_runtime, web_runtime, workshop_record};
use crate::{planner::repair::Repair, playback::Status as PlaybackStatus};
use serde_json::{Value, json};
use std::time::Instant;

impl Dialogue {
    /// 処理開始時のepoch（世代）を持つturnへ配送結果を反映する。
    /// 同じturn IDが再開されても、新しい世代の表示行を古い処理から上書きしない。
    /// 現在のsession世代でない結果は取消とし、会話・workshopの状態へ適用しない。
    pub(super) fn update(
        &self,
        sid: &str,
        turn: &str,
        epoch: u64,
        status: PlaybackStatus,
        result: Option<&Value>,
    ) -> bool {
        let mut d = self.data.lock().unwrap();
        let current = !d.stopped && d.sessions.get(sid).is_some_and(|s| s.epoch == epoch);
        let row = d.rows.iter().find(|r| r["turn_id"] == turn);
        if row.is_some_and(|r| r["epoch"].as_u64().is_some_and(|owner| owner != epoch)) {
            return false;
        }
        let workshop_id = row
            .and_then(|r| r["workshop_id"].as_str())
            .map(str::to_owned);
        let fixed_close = row.is_some_and(|r| r["workshop_fixed_close"] == true);
        let closed_by_turn = row.is_some_and(|r| r["workshop_closed_by_turn"] == true);
        let text_displayed = status == PlaybackStatus::AudioDisabled
            && result.is_some_and(|r| r["text_displayed"] == true)
            && d.sessions.get(sid).is_some_and(|s| s.text_workshop);
        let player_text = row
            .and_then(|r| r["player_input_text"].as_str())
            .unwrap_or("")
            .to_owned();
        if current
            && (status == PlaybackStatus::Queued || text_displayed)
            && let Some(wid) = workshop_id.as_ref()
        {
            let valid = d
                .sessions
                .get(sid)
                .and_then(|s| s.haiku.workshop.as_ref())
                .is_some_and(|w| {
                    w.hud_id == *wid
                        && !w.combat_paused()
                        && result.is_none_or(|r| {
                            r["workshop_action"] != "knowledge"
                                || r["workshop_version"] == w.version
                        })
                        && (result.is_none_or(|r| {
                            !matches!(
                                r["workshop_action"].as_str(),
                                Some("confirm_close" | "decline_resume")
                            )
                        }) || (w.pending.is_none()
                            && result.is_some_and(|r| r["workshop_version"] == w.version)))
                        && (w.is_open()
                            || ((fixed_close
                                || closed_by_turn
                                || result.is_some_and(|r| r["workshop_closed_by_turn"] == true))
                                && w.close_reason.as_deref() == Some("explicit_close")))
                });
            if !valid {
                Self::cancel_chat(&mut d, sid, "workshop_changed");
                return false;
            }
        }
        let status = if !current {
            PlaybackStatus::Cancelled
        } else {
            status
        };
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["playback_status"] = status.into();
            row[format!("{status}_at")] = chrono::Utc::now().to_rfc3339().into();
            if let Some(result) = result {
                if status == PlaybackStatus::Queued
                    && workshop_id.is_some()
                    && matches!(
                        result["workshop_action"].as_str(),
                        Some("close_workshop" | "confirm_close" | "decline_resume")
                    )
                {
                    // This validated queued transition closes the pin below.
                    // Preserve ownership for its later text-display acknowledgement.
                    row["workshop_closed_by_turn"] = true.into();
                }
                if text_displayed {
                    row["text_displayed"] = true.into();
                    row["output_mode"] = "text".into();
                }
                knowledge_display::attach(row, result);
                if status == PlaybackStatus::Queued
                    && row["conversation_route"].is_null()
                    && result["memory_action"].is_null()
                    && (workshop_id.is_none()
                        || matches!(
                            result["workshop_action"].as_str(),
                            Some("unrelated" | "knowledge")
                        ))
                {
                    row["conversation_route"] = if result["knowledge_status"].is_null() {
                        "casual"
                    } else {
                        "learning"
                    }
                    .into();
                }
                if let Some(text) = result.get("text") {
                    row["text"] = text.clone();
                }
                if let Some(error) = result.get("error") {
                    row["error"] = error.clone();
                }
                if let Some(reports) = result.get("llm_reports") {
                    row["llm_reports"] = reports.clone();
                }
                for key in [
                    "memory_action",
                    "memory_outcome",
                    "workshop_action",
                    "workshop_steps",
                    "workshop_reason",
                    "workshop_outcome",
                    "workshop_revision_id",
                    "workshop_followup",
                    "workshop_feedback_outcome",
                    "dialogue_action",
                    "observation_revision",
                ] {
                    if let Some(value) = result.get(key) {
                        row[key] = value.clone();
                    }
                }
            }
        }
        if current {
            let row = d.rows.iter().find(|r| r["turn_id"] == turn);
            let route = row
                .and_then(|r| {
                    serde_json::from_value::<crate::foreground::Route>(
                        r["conversation_route"].clone(),
                    )
                    .ok()
                })
                .unwrap_or_default();
            let now = self.clock.elapsed().as_millis() as u64;
            let player_at = row.and_then(|r| r["input_at_ms"].as_u64()).unwrap_or(now);
            let s = d.sessions.get_mut(sid).unwrap();
            s.status = status;
            s.haiku.last_activity = Instant::now();
            if status.is_terminal() {
                s.cancel = None;
            }
            if let Some(p) = s.address.as_mut() {
                p.playback(turn, status);
            }
            if let Some(result) = result {
                if s.text_workshop && status == PlaybackStatus::AudioDisabled && !text_displayed {
                    s.text_reply = Some((turn.into(), epoch, result.clone()));
                }
                let knowledge_detour =
                    workshop_id.is_some() && result["workshop_action"] == "knowledge";
                let workshop_reply = workshop_id.is_some()
                    && result["workshop_action"] != "unrelated"
                    && !knowledge_detour;
                if status == PlaybackStatus::Quiet
                    && result["dialogue_action"] == "silent"
                    && !workshop_reply
                {
                    s.history.push(turn, "user", &player_text);
                    s.history.silent(turn);
                }
                if (status == PlaybackStatus::Completed
                    || status == PlaybackStatus::Quiet && result["dialogue_action"] == "silent")
                    && let Some(revision) = result["observation_revision"].as_u64()
                {
                    s.conversation_observation.considered(revision);
                }
                if status == PlaybackStatus::Queued
                    && !workshop_reply
                    && result["memory_action"].is_null()
                    && result["web_private"] != true
                {
                    s.history.push(turn, "user", &player_text);
                    if !knowledge_detour {
                        s.foreground
                            .select(turn, &player_text, route, now, player_at);
                    }
                }
                if status == PlaybackStatus::Queued && workshop_id.is_some() && !knowledge_detour {
                    if !workshop_reply {
                        s.history.push(turn, "user", &player_text);
                    }
                    if let Some(w) = s
                        .haiku
                        .workshop
                        .as_mut()
                        .filter(|w| Some(&w.hud_id) == workshop_id.as_ref())
                    {
                        w.record_activity(Instant::now());
                        if w.open
                            && !w.combat_paused()
                            && w.pending.is_none()
                            && result["workshop_version"] == w.version
                            && let Ok(draft) = serde_json::from_value(
                                result["workshop_conversation_candidate"].clone(),
                            )
                            && let Some(candidate) =
                                crate::workshop_candidate::Candidate::from_player(
                                    draft,
                                    &w.current_lines,
                                    w.version,
                                    &player_text,
                                )
                        {
                            // Only the player's idea: independent of Dogido's reply playback.
                            w.conversation_candidate = Some(candidate);
                        }
                        if let Some(steps) = result["workshop_steps"].as_array() {
                            w.agent_steps.extend(steps.iter().cloned());
                            while w.agent_steps.len() > 12 {
                                w.agent_steps.pop_front();
                            }
                        }
                        if result["workshop_action"] == "close_workshop"
                            || result["workshop_action"] == "confirm_close"
                            || result["workshop_action"] == "decline_resume"
                        {
                            w.close("explicit_close");
                        }
                    }
                }
                if (status == PlaybackStatus::Completed || text_displayed)
                    && !knowledge_detour
                    && let Some(w) = s
                        .haiku
                        .workshop
                        .as_mut()
                        .filter(|w| Some(&w.hud_id) == workshop_id.as_ref())
                {
                    if workshop_reply {
                        let lines = w
                            .pending
                            .as_ref()
                            .map_or(w.current_lines.as_slice(), |p| &p.lines);
                        if w.open
                            && !w.combat_paused()
                            && let Some(target) = crate::workshop_target::Target::from_view(
                                &json!({"discussion_target":result["workshop_discussion_target"]}),
                                lines,
                            )
                        {
                            w.discussion_target = Some(target);
                        }
                        if w.open
                            && !w.combat_paused()
                            && result["workshop_version"] == w.version
                            && w.pending.is_none()
                        {
                            w.followup =
                                serde_json::from_value(result["workshop_followup"].clone())
                                    .unwrap_or_default();
                        }
                        w.drift_count = 0;
                        w.record_activity(Instant::now());
                        if !w.dialogue.iter().any(|p| p["turn_id"] == turn) {
                            w.dialogue.push_back(json!({"turn_id":turn,"player_text":player_text,"dogido_text":result["text"]}));
                            while w.dialogue.len() > 4 {
                                w.dialogue.pop_front();
                            }
                        }
                    } else {
                        w.drift_count += 1;
                        if w.drift_count >= 2 {
                            w.close("drift");
                        }
                    }
                }
                if status == PlaybackStatus::Queued
                    && !workshop_reply
                    && let Ok(repair) = serde_json::from_value::<Repair>(result["repair"].clone())
                {
                    s.history.annotate(turn, &repair);
                }
                if status == PlaybackStatus::Completed {
                    web_runtime::completed(s, turn, &player_text, result);
                }
                // 生成や再生開始だけでは「話した履歴」にしない。音声完了、または
                // テキスト相談室での表示確認を受けてから確定する。ここで成立した
                // 通常雑談の対だけを、後の川柳の会話材料へ渡す。
                if (status == PlaybackStatus::Completed || text_displayed)
                    && !workshop_reply
                    && result["memory_action"].is_null()
                    && result["web_private"] != true
                {
                    s.last_completed_conversation = Some(now);
                    language_runtime::completed(s, result);
                    s.history
                        .push(turn, "assistant", result["text"].as_str().unwrap_or(""));
                    s.foreground.completed(
                        turn,
                        &player_text,
                        result["text"].as_str().unwrap_or(""),
                        route,
                    );
                    if let Some(pair) = s
                        .history
                        .completed_pairs()
                        .into_iter()
                        .find(|p| p["turn_id"] == turn)
                        && result["knowledge_status"].is_null()
                        && route == crate::foreground::Route::Casual
                        && !s.haiku.material_turns.iter().any(|p| p["turn_id"] == turn)
                    {
                        if s.haiku.material_turns.len() == 3 {
                            s.haiku.material_turns.pop_front();
                        }
                        s.haiku.material_turns.push_back(pair);
                    }
                }
            }
        }
        d.revision += 1;
        let record = if status == PlaybackStatus::Queued {
            d.rows
                .iter()
                .find(|r| r["turn_id"] == turn)
                .cloned()
                .map(|row| {
                    let after = workshop_record::state(
                        d.sessions.get(sid).and_then(|s| s.haiku.workshop.as_ref()),
                    );
                    (row, after)
                })
        } else {
            None
        };
        drop(d);
        if let Some((row, after)) = record {
            self.record_workshop(
                sid,
                "decision",
                &row["workshop_state_before"],
                &after,
                &workshop_record::Input::from_row(&row),
                &row,
            );
        }
        tracing::info!(
            event = "dialogue_status",
            session_id = sid,
            turn_id = turn,
            playback_status = status.as_str()
        );
        current
    }
}
