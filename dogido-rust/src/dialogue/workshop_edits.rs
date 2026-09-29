//! Commit validated player edits before speech. Persistence is owned by the turn
//! job and awaited even if playback is cancelled; no disk I/O holds the state lock.
use super::*;
use crate::{haiku_record::MemoryStore, workshop_edit::Pending};
use anyhow::{Context, ensure};

impl Dialogue {
    pub(super) async fn apply_workshop_edit(
        &self,
        sid: &str,
        epoch: u64,
        result: &mut Value,
    ) -> Result<()> {
        let action = result["workshop_action"].as_str().unwrap_or("").to_owned();
        let generated_proposal = result["workshop_proposed"]["generated_basis"].is_object();
        if !generated_proposal
            && !matches!(
                action.as_str(),
                "stage_player_edit"
                    | "stage_conversation_candidate"
                    | "accept_pending"
                    | "reject_pending"
            )
        {
            return Ok(());
        }
        let close_after = result["workshop_close_after"] == true;
        let (original, pending, parent, version, player_name) = {
            let mut d = self.data.lock().unwrap();
            ensure!(!d.stopped, "shutdown");
            let s = d.sessions.get_mut(sid).context("session_closed")?;
            ensure!(
                s.epoch == epoch
                    && workshop_combat_input::allowed(s)
                    && s.cancel.as_ref().is_some_and(|c| !*c.borrow()),
                "cancelled_edit"
            );
            let w = s.haiku.workshop.as_mut().context("workshop_closed")?;
            ensure!(
                result["workshop_id"] == w.hud_id
                    && w.open
                    && !w.combat_paused()
                    && result["workshop_version"] == w.version,
                "stale_workshop"
            );
            if matches!(
                action.as_str(),
                "stage_player_edit" | "stage_conversation_candidate"
            ) || generated_proposal
            {
                if let Some(proposed) = result.get("workshop_proposed").filter(|p| !p.is_null()) {
                    let proposed: Pending = serde_json::from_value(proposed.clone())?;
                    proposed.validate(&w.current_lines)?;
                    // Recheck the working version as well as the canonical base.
                    let working = w.pending.as_ref().map_or(&w.current_lines, |p| &p.lines);
                    if generated_proposal {
                        ensure!(w.pending.is_none(), "pending_exists");
                    } else {
                        Pending::stage(
                            &w.current_lines,
                            working,
                            proposed.lines.clone(),
                            proposed.selected_line,
                        )?;
                    }
                    w.pending = Some(proposed);
                    w.conversation_candidate = None;
                    w.version += 1;
                    result["workshop_outcome"] = if generated_proposal {
                        "revision_proposed"
                    } else {
                        "player_edit_staged"
                    }
                    .into();
                } else {
                    result["workshop_outcome"] = "player_edit_rejected".into();
                }
                d.revision += 1;
                return Ok(());
            }
            let pending = w.pending.clone().context("pending_required")?;
            if action == "reject_pending" {
                w.pending = None;
                w.conversation_candidate = None;
                w.version += 1;
                if close_after {
                    w.close("explicit_close");
                    result["workshop_closed_by_turn"] = true.into();
                }
                result["workshop_outcome"] = "pending_rejected".into();
                d.revision += 1;
                return Ok(());
            }
            pending.validate(&w.current_lines)?;
            (
                w.emission.clone(),
                pending,
                w.revision_id.clone(),
                w.version,
                s.name.clone(),
            )
        };
        tracing::info!(
            event = "workshop_revision_save_started",
            session_id = sid,
            revision_id = pending.id
        );
        let saved = if self.config.haiku.memory_enabled {
            let root = self.config.haiku.memory_dir.join("sessions").join(sid);
            let write_sid = sid.to_owned();
            let write_pending = pending.clone();
            // Do not select! cancellation around this join. Once acceptance has
            // passed the live CAS boundary, its save must be resolved and reaped.
            tokio::task::spawn_blocking(move || {
                let store = MemoryStore::new(root);
                store.save_agent_haiku(&write_sid, &player_name, &original)?;
                store.save_player_revision(&original, &write_pending, parent.as_deref())
            })
            .await?
        } else {
            Err(anyhow::anyhow!("memory_disabled"))
        };
        match saved {
            Ok(revision_id) => {
                let mut d = self.data.lock().unwrap();
                // Cancellation can stop speech, but cannot undo an already
                // authorized save. The serial turn permit prevents other edits
                // until this transaction resolves. Never reopen a closed pin.
                if let Some(w) = d
                    .sessions
                    .get_mut(sid)
                    .and_then(|s| s.haiku.workshop.as_mut())
                    .filter(|w| {
                        result["workshop_id"] == w.hud_id
                            && w.version == version
                            && w.pending.as_ref() == Some(&pending)
                    })
                {
                    w.current_lines = pending.lines;
                    let sources:Vec<_>=w.current_lines.iter().filter(|l| !l.source_atom_ids.is_empty()).map(|l| json!({"line_index":l.line_index,"text":l.reading_text,"atom_ids":l.source_atom_ids,"sources":l.source_atoms})).collect();
                    if sources.is_empty() {
                        w.materials.remove("line_sources");
                    } else {
                        w.materials.insert("line_sources".into(), json!(sources));
                    }
                    w.revision_id = Some(revision_id.clone());
                    w.pending = None;
                    w.conversation_candidate = None;
                    w.version += 1;
                    if close_after && w.open {
                        w.close("explicit_close");
                        result["workshop_closed_by_turn"] = true.into();
                    }
                    d.revision += 1;
                }
                result["workshop_outcome"] = "pending_saved".into();
                result["workshop_revision_id"] = revision_id.into();
            }
            Err(error) => {
                tracing::error!(event="workshop_revision_save_failed",session_id=sid,%error);
                result["workshop_outcome"] = "pending_save_failed".into();
                result["workshop_reason"] = error.to_string().into();
                result["text"] = "保存できんかったわ。元の句と案は残してあるで。".into();
                result["spoken_text"] = result["text"].clone();
            }
        }
        Ok(())
    }
}
