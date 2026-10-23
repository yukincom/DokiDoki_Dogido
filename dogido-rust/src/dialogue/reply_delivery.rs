//! 会話の返答が届いたと確認できた時点の反映。文字と音声のどちらでも同じ確定処理を一度だけ行う。
use super::{Data, Dialogue};
use serde_json::{Value, json};
use std::time::Instant;

impl Dialogue {
    pub(super) fn confirm_reply_delivery(
        &self,
        d: &mut Data,
        sid: &str,
        turn: &str,
        epoch: u64,
        result: &Value,
        text: bool,
    ) -> bool {
        let Some(row) = d
            .rows
            .iter()
            .find(|r| r["session_id"] == sid && r["turn_id"] == turn)
            .cloned()
        else {
            return false;
        };
        let Some(s) = d.sessions.get(sid) else {
            return false;
        };
        if d.stopped
            || (!row["session_instance"].is_null() && row["session_instance"] != s.instance_id)
        {
            return false;
        }
        if s.epoch != epoch
            || row["epoch"].as_u64().is_some_and(|owner| owner != epoch)
            || text && (s.current_turn != turn || row["playback_status"] == "cancelled")
        {
            return false;
        }
        if row["delivery_confirmed"] == true {
            return true;
        }
        let workshop_id = row["workshop_id"].as_str().map(str::to_owned);
        if text && let Some(wid) = workshop_id.as_ref() {
            let valid = s.haiku.workshop.as_ref().is_some_and(|w| {
                w.hud_id == *wid
                    && !w.combat_paused()
                    && (result["workshop_action"] != "knowledge"
                        || result["workshop_version"] == w.version)
                    && (!matches!(result["workshop_action"].as_str(), Some("confirm_close"))
                        || w.pending.is_none() && result["workshop_version"] == w.version)
                    && (w.is_open()
                        || (row["workshop_fixed_close"] == true
                            || row["workshop_closed_by_turn"] == true
                            || result["workshop_closed_by_turn"] == true)
                            && w.close_reason.as_deref() == Some("explicit_close"))
            });
            if !valid {
                return false;
            }
        }
        let player_text = row["player_input_text"].as_str().unwrap_or("").to_owned();
        let route =
            serde_json::from_value::<crate::foreground::Route>(row["conversation_route"].clone())
                .unwrap_or_default();
        let now = self.clock.elapsed().as_millis() as u64;
        let knowledge_detour = workshop_id.is_some() && result["workshop_action"] == "knowledge";
        let workshop_reply =
            workshop_id.is_some() && result["workshop_action"] != "unrelated" && !knowledge_detour;
        let s = d.sessions.get_mut(sid).unwrap();
        if let Some(revision) = result["observation_revision"].as_u64() {
            s.conversation_observation.considered(revision);
        }
        if !knowledge_detour
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
                    w.followup = serde_json::from_value(result["workshop_followup"].clone())
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
        if !workshop_reply
            && !result["memory_action"].is_null()
            && let Some(text) = result["text"].as_str().filter(|text| !text.is_empty())
        {
            // 想起・保存結果も実発話として共有する。通常雑談の対や新しい句の材料にはしない。
            s.history.story(
                &format!("memory:{turn}"),
                &format!("プレイヤー「{player_text}」\nドギド「{text}」"),
            );
        }
        // 生成や再生開始だけでは「話した履歴」にしない。音声完了、または
        // Minecraftやテキスト相談室の表示確認を受けてから確定する。ここで成立した
        // 通常雑談の対だけを、後の川柳の会話材料へ渡す。
        if !workshop_reply && result["memory_action"].is_null() {
            s.last_completed_conversation = Some(now);
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
        if let Some(wid) = workshop_id.as_deref()
            && let Some(result) = Some(result).filter(|r| {
                !matches!(
                    r["workshop_action"].as_str(),
                    Some("unrelated" | "knowledge")
                )
            })
            && let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn)
            && row["shared_story_recorded"] != true
            && row["workshop_record_private"] != true
        {
            row["shared_story_recorded"] = true.into();
            let text = result["text"].as_str().unwrap_or("");
            let story = format!("川柳の相談。プレイヤー「{player_text}」\nドギド「{text}」");
            if let Some(s) = d.sessions.get_mut(sid) {
                self.share_story(
                    sid,
                    s,
                    super::story::StoryEvent {
                        id: &format!("workshop:{turn}"),
                        workshop: Some(wid),
                        kind: "workshop_dialogue",
                        text: &story,
                        details: json!({"workshop_id":wid,"input_turn_id":turn,"player_text":player_text,"dogido_text":text}),
                    },
                );
            }
        }
        if let Some(row) = d.rows.iter_mut().find(|r| r["turn_id"] == turn) {
            row["delivery_confirmed"] = true.into();
        }
        true
    }
}
