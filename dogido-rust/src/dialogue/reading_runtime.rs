//! Pronunciation is saved before acknowledgement; an interrupted voice reply
//! cannot undo an authorized write. The canonical poem and pending edit stay put.
use super::*;
use crate::{haiku_record::MemoryStore, reading_correction::Correction};
use anyhow::{Context, ensure};

pub(super) async fn load_overlay(config: &DialogueConfig) -> Result<Vec<Value>> {
    if !config.haiku.memory_enabled {
        return Ok(vec![]);
    }
    let root = config.haiku.memory_dir.clone();
    match tokio::task::spawn_blocking(move || MemoryStore::new(root).reading_corrections()).await? {
        Ok(rows) => Ok(rows),
        Err(error) => {
            tracing::warn!(event="reading_corrections_load_failed",%error);
            Ok(vec![])
        }
    }
}

impl Dialogue {
    pub(super) async fn reading_overlay(&self) -> Result<Vec<Value>> {
        load_overlay(&self.config).await
    }

    pub(super) async fn correct_reading(
        &self,
        sid: &str,
        epoch: u64,
        input: &Value,
        mut c: Correction,
    ) -> Result<Value> {
        {
            let d = self.data.lock().unwrap();
            let s = d.sessions.get(sid).context("session_closed")?;
            ensure!(
                !d.stopped
                    && s.epoch == epoch
                    && workshop_combat_input::allowed(s)
                    && s.cancel.as_ref().is_some_and(|c| !*c.borrow()),
                "cancelled_reading_correction"
            );
            if input["workshop"].is_object() {
                ensure!(
                    s.haiku.workshop.as_ref().is_some_and(|w| w.open
                        && !w.combat_paused()
                        && input["workshop"]["workshop_id"] == w.hud_id
                        && input["workshop"]["version"] == w.version),
                    "stale_workshop"
                );
            }
        }
        let source = c.resolve_biome(input["event"]["world"]["biome"].as_str());
        let (text, outcome) = if !self.config.haiku.memory_enabled {
            ("記憶機能は今止まっとるで。".to_owned(), "memory_disabled")
        } else if c.needs_surface() {
            (
                "どの言葉の読みか、言葉と読みを一緒に教えてくれへん？".to_owned(),
                "surface_required",
            )
        } else {
            let root = self.config.haiku.memory_dir.clone();
            let write_c = c.clone();
            let write_sid = sid.to_owned();
            tracing::info!(event = "reading_correction_save_started", session_id = sid);
            // Await the write even if cancellation arrives while I/O is in flight.
            // The existing serial permit keeps later turns behind this transaction.
            let saved = tokio::task::spawn_blocking(move || {
                MemoryStore::new(root).save_reading_correction(
                    &write_c,
                    source.as_deref(),
                    chrono::Utc::now(),
                    &write_sid,
                )
            })
            .await?;
            match saved {
                Ok(_) => (
                    format!("{}は「{}」やね。覚え直したで。", c.surface, c.reading),
                    "saved",
                ),
                Err(error) => {
                    tracing::error!(event="reading_correction_save_failed",session_id=sid,%error);
                    (
                        "読みの保存ができんかったわ。もう一度教えてな。".into(),
                        "save_failed",
                    )
                }
            }
        };
        tracing::info!(
            event = "reading_correction",
            session_id = sid,
            surface = c.surface,
            reading = c.reading,
            outcome
        );
        // Use the supplied pronunciation in this confirmation without asking the
        // dictionary to read the very surface the player just corrected.
        let spoken = if outcome == "saved" {
            format!("{}は「{}」やね。覚え直したで。", c.reading, c.reading)
        } else {
            text.clone()
        };
        let mut result = json!({"op":"result","text":text,"spoken_text":spoken,"llm_reports":[],
            "memory_action":"reading_correction","memory_outcome":outcome});
        if input["workshop"].is_object() {
            result["workshop_id"] = input["workshop"]["workshop_id"].clone();
            result["workshop_version"] = input["workshop"]["version"].clone();
            result["workshop_action"] = "reading_correction".into();
            result["workshop_steps"] = json!([]);
        }
        Ok(result)
    }
}
