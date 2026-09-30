//! Explicit, isolated text workshop sessions. A displayed reply is not audio playback.
use super::*;
use crate::haiku_record::{Emission, Workshop};
use anyhow::ensure;

fn refresh_line_sources(w: &mut Workshop) {
    w.materials.remove("line_sources");
    let sources: Vec<_> = w.current_lines.iter().filter(|l| !l.source_atom_ids.is_empty()).map(|l| json!({"line_index":l.line_index,"text":l.reading_text,"atom_ids":l.source_atom_ids,"sources":l.source_atoms})).collect();
    if !sources.is_empty() {
        w.materials.insert("line_sources".into(), json!(sources));
    }
}

impl Dialogue {
    /// The source binding comes only from the server's poem-book reader.
    pub fn open_saved_text_workshop(
        &self,
        sid: &str,
        poem: crate::poem_book::SavedPoem,
    ) -> Result<()> {
        self.open_text_workshop(sid, poem.original.clone(), poem.original.entry_id())?;
        let mut d = self.data.lock().unwrap();
        let s = d.sessions.get_mut(sid).unwrap();
        let w = s.haiku.workshop.as_mut().unwrap();
        w.current_lines = poem.lines.clone();
        w.revision_id = poem.revision_id.clone();
        refresh_line_sources(w);
        s.text_poem = Some(poem);
        Ok(())
    }
    /// Full canonical records for an explicit restart handoff, even after close.
    pub fn text_workshop_state(&self, sid: &str) -> Value {
        let d = self.data.lock().unwrap();
        let Some(s) = d.sessions.get(sid).filter(|s| s.text_workshop) else {
            return Value::Null;
        };
        let Some(w) = &s.haiku.workshop else {
            return Value::Null;
        };
        json!({"key":s.text_poem.as_ref().map(|p| &p.key),"lines":w.current_lines,
            "revision_id":w.revision_id,"pending":w.pending,"version":w.version,
            "workshop_id":w.hud_id,"open":w.open,"candidate":w.conversation_candidate})
    }

    pub fn text_workshop_prompts(&self) -> Value {
        let d = self.data.lock().unwrap();
        json!({"version":d.text_prompt_version,
            "settings":d.text_prompt_settings.clone().unwrap_or_else(workshop_runtime::prompt::editable_defaults),
            "defaults":workshop_runtime::prompt::editable_defaults()})
    }

    pub fn set_text_workshop_prompts(&self, settings: Value, expected: u64) -> Result<u64> {
        ensure!(
            !self.config.audio_enabled
                && !self.config.haiku.memory_enabled
                && !self.config.haiku.enabled,
            "text-only configuration required"
        );
        let assets = workshop_runtime::prompt::editable_assets(&settings)?;
        let mut d = self.data.lock().unwrap();
        ensure!(
            d.text_prompt_version == expected,
            "別の画面で変更されています。最新の設定を読み直してください"
        );
        d.text_prompts = Some(assets);
        d.text_prompt_settings = Some(settings);
        d.text_prompt_version += 1;
        Ok(d.text_prompt_version)
    }

    pub fn text_workshop_last_request(&self, sid: &str) -> Value {
        self.data
            .lock()
            .unwrap()
            .sessions
            .get(sid)
            .filter(|s| s.text_workshop)
            .and_then(|s| s.text_last_request.clone())
            .unwrap_or(Value::Null)
    }

    pub(super) fn record_text_workshop_request(
        &self,
        input: &Value,
        request: &crate::types::GenerationRequest,
        phase: &str,
    ) {
        let mut d = self.data.lock().unwrap();
        if let Some(s) = d
            .sessions
            .values_mut()
            .find(|s| s.text_workshop && s.current_turn == input["operation_id"])
        {
            s.text_last_request = Some(json!({"turn_id":input["operation_id"],"phase":phase,
                "prompt_version":input["text_prompt_version"],"request":request}));
        }
    }

    /// Restore a handoff by resolving its revision in the bound source poem.
    /// Legacy handoffs can restore only an unchanged original.
    pub fn restore_text_conversation(&self, sid: &str, snapshot: &Value) -> Result<()> {
        let original = snapshot["dialogue"]["sessions"]
            .as_array()
            .and_then(|a| a.iter().find(|s| s["session_id"] == sid))
            .ok_or_else(|| anyhow::anyhow!("missing saved text session"))?;
        let mut d = self.data.lock().unwrap();
        let s = d
            .sessions
            .get_mut(sid)
            .ok_or_else(|| anyhow::anyhow!("missing text session"))?;
        ensure!(
            s.text_workshop && s.current_turn.is_empty(),
            "new text session required"
        );
        let saved = &snapshot["text_state"];
        let w = s.haiku.workshop.as_mut().unwrap();
        if saved.is_object() {
            let poem = s
                .text_poem
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("missing source binding"))?;
            ensure!(saved["key"] == poem.key, "recovery poem mismatch");
            let lines = poem.lines_at(saved["revision_id"].as_str())?;
            ensure!(saved["lines"] == json!(lines), "recovery lines mismatch");
            w.current_lines = lines;
            w.revision_id = saved["revision_id"].as_str().map(str::to_owned);
            w.pending = serde_json::from_value(saved["pending"].clone())?;
            if let Some(pending) = &w.pending {
                pending.validate(&w.current_lines)?;
            }
            w.version = saved["version"].as_u64().unwrap_or(0);
            w.hud_id = saved["workshop_id"]
                .as_str()
                .ok_or_else(|| anyhow::anyhow!("missing recovery workshop"))?
                .into();
            w.conversation_candidate =
                serde_json::from_value(saved["candidate"].clone()).unwrap_or_default();
        }
        refresh_line_sources(w);
        let canonical = w
            .current_lines
            .iter()
            .map(|l| l.surface_text.clone())
            .collect::<Vec<_>>();
        ensure!(
            saved.is_object()
                || (snapshot["workshop"]["canonical_lines"] == json!(canonical)
                    && snapshot["workshop"]["pending_lines"]
                        .as_array()
                        .is_some_and(Vec::is_empty)),
            "edited verse requires its original line records; cannot restore from HUD"
        );
        if let Some(wid) = snapshot["workshop"]["workshop_id"].as_str() {
            w.hud_id = wid.into();
        }
        w.dialogue =
            serde_json::from_value(original["workshop_history"].clone()).unwrap_or_default();
        while w.dialogue.len() > 4 {
            w.dialogue.pop_front();
        }
        w.followup =
            serde_json::from_value(original["workshop_followup"].clone()).unwrap_or_default();
        w.discussion_target = crate::workshop_target::Target::from_view(
            &json!({"discussion_target":original["workshop_discussion_target"]}),
            &w.current_lines,
        );
        if snapshot["workshop"]["state"] == "closed" || saved["open"] == false {
            w.close("restored_closed");
        }
        if let Some(history) = original["history"].as_array() {
            for row in history {
                if let (Some(t), Some(r), Some(c)) = (
                    row["turn_id"].as_str(),
                    row["role"].as_str(),
                    row["text"].as_str(),
                ) {
                    s.history.push(
                        if r == "assistant" {
                            t.strip_suffix(":reply").unwrap_or(t)
                        } else {
                            t
                        },
                        r,
                        c,
                    );
                }
            }
        }
        let rows = snapshot["dialogue"]["utterances"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("missing transcript"))?;
        ensure!(
            rows.iter().all(|r| r["session_id"] == sid
                && !matches!(
                    r["playback_status"].as_str(),
                    Some("generating" | "queued" | "started")
                )),
            "wait for current reply before upgrade"
        );
        let restore_target_from_rows = w.discussion_target.is_none();
        for row in rows {
            // Recover a player-grounded idea from an unchanged verse's recent
            // transcript, including older versions that dropped a question's
            // rejected edit step. Reconstruct discussion only, never execute it.
            if saved.is_null()
                && w.open
                && w.dialogue.iter().any(|t| row["turn_id"] == t["turn_id"])
                && row["workshop_id"] == w.hud_id
                && row["workshop_state_before"]["canonical"] == canonical.join("\n")
                && let Some(player) = row["player_input_text"].as_str()
                && let Some(reports) = row["llm_reports"].as_array()
            {
                for report in reports {
                    if report["kind"] == "haiku_workshop_agent_step"
                        && let Some(payload) = report["generated"]["text"]
                            .as_str()
                            .and_then(crate::planner::extract_object)
                        && let Some(candidate) = crate::workshop_candidate::Candidate::from_model(
                            &payload,
                            &w.current_lines,
                            w.version,
                            player,
                            w.discussion_target.as_ref(),
                        )
                    {
                        w.conversation_candidate = Some(candidate);
                    }
                }
            }
            if restore_target_from_rows
                && w.open
                && let Some(text) = row["player_input_text"].as_str()
            {
                crate::workshop_target::Target::observe(
                    &mut w.discussion_target,
                    text,
                    &w.current_lines,
                );
            }
            if let Some(steps) = row["workshop_steps"].as_array() {
                w.agent_steps.extend(steps.iter().cloned());
            }
        }
        while w.agent_steps.len() > 12 {
            w.agent_steps.pop_front();
        }
        if let Some(last) = rows.last() {
            s.current_turn = last["turn_id"].as_str().unwrap_or("").into();
            s.epoch = last["epoch"].as_u64().unwrap_or(0);
            s.status = serde_json::from_value(last["playback_status"].clone())
                .unwrap_or(PlaybackStatus::Ready);
        }
        d.rows.extend(rows.iter().cloned());
        while d.rows.len() > 200 {
            d.rows.pop_front();
        }
        d.revision = d
            .revision
            .max(snapshot["dialogue"]["revision"].as_u64().unwrap_or(0))
            + 1;
        Ok(())
    }

    /// Only used by the standalone text entry point, never a Minecraft session.
    pub fn open_text_workshop(
        &self,
        sid: &str,
        emission: Emission,
        entry_id: String,
    ) -> Result<()> {
        ensure!(
            !self.config.audio_enabled
                && !self.config.haiku.memory_enabled
                && !self.config.haiku.enabled
                && !self.config.web.enabled,
            "text workshop requires isolated, silent configuration"
        );
        let mut d = self.data.lock().unwrap();
        let s = d
            .sessions
            .get_mut(sid)
            .ok_or_else(|| anyhow::anyhow!("unknown session"))?;
        ensure!(
            s.preview && s.current_turn.is_empty(),
            "text workshop needs a new preview session"
        );
        s.text_workshop = true;
        s.haiku.workshop = Some(Workshop::open(emission, Some(entry_id), Instant::now()));
        s.foreground.activate(
            crate::foreground::Route::HaikuWorkshop,
            self.clock.elapsed().as_millis() as u64,
            None,
        );
        d.revision += 1;
        Ok(())
    }

    /// The browser calls this after inserting the returned text into its visible log.
    /// Stale/cancelled turns and repeated acknowledgements cannot enter history.
    pub fn acknowledge_workshop_text(&self, sid: &str, turn: &str) -> bool {
        let reply = {
            let mut d = self.data.lock().unwrap();
            let Some(row) = d
                .rows
                .iter()
                .find(|r| r["session_id"] == sid && r["turn_id"] == turn)
            else {
                return false;
            };
            if row["text_displayed"] == true {
                return true;
            }
            if row["playback_status"] != "audio_disabled" {
                return false;
            }
            let Some(s) = d.sessions.get_mut(sid) else {
                return false;
            };
            if !s.text_workshop || s.current_turn != turn {
                return false;
            }
            if !s
                .text_reply
                .as_ref()
                .is_some_and(|(id, epoch, _)| id == turn && *epoch == s.epoch)
            {
                return false;
            }
            s.text_reply.take()
        };
        let Some((_, epoch, mut result)) = reply else {
            return false;
        };
        result["text_displayed"] = true.into();
        self.update(
            sid,
            turn,
            epoch,
            PlaybackStatus::AudioDisabled,
            Some(&result),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::haiku_record::PreparedEmission;

    fn emission() -> Emission {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../scripts/fixtures/workshop_meaning_cases.json"
        ))
        .unwrap();
        let view = &fixture["contexts"]["grass"];
        let e = &view["emission"];
        serde_json::from_value::<PreparedEmission>(json!({
            "text":e["reading_text"],"surface_text":null,"reading_text":e["reading_text"],
            "lines":e["lines"],"materials":view["materials"],"interpretation":null,"preface":null,
            "biome":null,"structure":null,"time_phase":null,"dimension":null,"event_sequence":null,"route":"haiku"
        })).unwrap().complete(chrono::Utc::now()).unwrap()
    }

    #[tokio::test]
    async fn displayed_text_is_explicit_current_and_once_without_audio_completion() {
        let dialogue = Dialogue::new(DialogueConfig {
            audio_enabled: false,
            llm_enabled: false,
            haiku: HaikuSettings {
                enabled: false,
                memory_enabled: false,
                ..Default::default()
            },
            web: WebSettings {
                enabled: false,
                ..Default::default()
            },
            ..Default::default()
        })
        .unwrap();
        dialogue.register("text", "プレイヤー", true);
        dialogue
            .open_text_workshop("text", emission(), "saved".into())
            .unwrap();
        let prompts = dialogue.text_workshop_prompts();
        assert_eq!(prompts["version"], 0);
        assert_eq!(
            dialogue
                .set_text_workshop_prompts(prompts["settings"].clone(), 0)
                .unwrap(),
            1
        );
        assert!(
            dialogue
                .set_text_workshop_prompts(prompts["settings"].clone(), 0)
                .is_err()
        );
        let result = {
            let mut d = dialogue.data.lock().unwrap();
            let s = d.sessions.get_mut("text").unwrap();
            s.current_turn = "turn".into();
            let w = s.haiku.workshop.as_ref().unwrap();
            let wid = w.hud_id.clone();
            d.rows
                .push_back(json!({"session_id":"text","turn_id":"turn","epoch":0,
                "workshop_id":wid,"player_input_text":"どういう意味？"}));
            json!({"text":"草が眠るように感じられるわ。","workshop_action":"explain","workshop_version":0})
        };
        dialogue.update(
            "text",
            "turn",
            0,
            PlaybackStatus::AudioDisabled,
            Some(&result),
        );
        assert!(
            dialogue.snapshot(None)["sessions"][0]["workshop_history"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(!dialogue.acknowledge_workshop_text("other", "turn"));
        assert!(dialogue.acknowledge_workshop_text("text", "turn"));
        assert!(dialogue.acknowledge_workshop_text("text", "turn"));
        let snap = dialogue.snapshot(None);
        assert_eq!(
            snap["sessions"][0]["workshop_history"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(snap["utterances"][0]["playback_status"], "audio_disabled");
        assert_eq!(snap["utterances"][0]["text_displayed"], true);
        let recovery = json!({"dialogue":snap,"workshop":dialogue.workshop_snapshot("text",0)});
        let restored = Dialogue::new(dialogue.config.clone()).unwrap();
        restored.register("text", "プレイヤー", true);
        restored
            .open_text_workshop("text", emission(), "saved".into())
            .unwrap();
        restored
            .restore_text_conversation("text", &recovery)
            .unwrap();
        assert_eq!(
            restored.snapshot(None)["sessions"][0]["workshop_history"],
            recovery["dialogue"]["sessions"][0]["workshop_history"]
        );
        assert_eq!(
            restored.snapshot(None)["utterances"],
            recovery["dialogue"]["utterances"]
        );
        restored.shutdown().await;
        dialogue.update(
            "text",
            "turn",
            0,
            PlaybackStatus::AudioDisabled,
            Some(&result),
        );
        {
            let mut d = dialogue.data.lock().unwrap();
            d.rows[0]["text_displayed"] = false.into();
            d.sessions.get_mut("text").unwrap().epoch += 1;
        }
        assert!(!dialogue.acknowledge_workshop_text("text", "turn"));
        dialogue.shutdown().await;
    }
}
