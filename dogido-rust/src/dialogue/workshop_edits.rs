//! 検証済みの句編集を保存し、返答の再生前に現在句と表示へ反映する。
//! 状態lockで元句と版を照合した後、lockを離して保存する。開始済みの保存は音声取消でも完了を待つ。
use super::*;
use crate::{haiku_record::MemoryStore, workshop_edit::Pending};
use anyhow::{Context, ensure};

impl Dialogue {
    /// 編集結果を、現在のworkshop ID・版・元句へ再照合して適用する（CAS）。
    /// 未採用案がない本人の編集は保存へ進む。生成案や既存の未採用案への編集は案として保持する。
    /// 音声取消だけで開始済みの保存を取り消さない。保存失敗時は現在句を更新せず、
    /// 同じworkshop ID・版・既存の案が保たれていれば、検査済み案を再試行用に保持する。
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
        let (
            original,
            pending,
            previous_pending,
            parent,
            version,
            player_name,
            text_workshop,
            text_poem,
        ) = {
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
            let player_edit = matches!(
                action.as_str(),
                "stage_player_edit" | "stage_conversation_candidate"
            );
            let pending = if player_edit || generated_proposal {
                let Some(proposed) = result.get("workshop_proposed").filter(|p| !p.is_null())
                else {
                    result["workshop_outcome"] = "player_edit_rejected".into();
                    return Ok(());
                };
                let proposed: Pending = serde_json::from_value(proposed.clone())?;
                proposed.validate(&w.current_lines)?;
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
                // A generated proposal still awaits adoption. Editing such a
                // proposal alone does not adopt its other, unapproved changes.
                if generated_proposal || w.pending.is_some() {
                    w.pending = Some(proposed);
                    w.conversation_candidate = None;
                    w.version += 1;
                    result["workshop_outcome"] = if generated_proposal {
                        "revision_proposed"
                    } else {
                        "player_edit_staged"
                    }
                    .into();
                    d.revision += 1;
                    return Ok(());
                }
                proposed
            } else {
                w.pending.clone().context("pending_required")?
            };
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
                w.pending.clone(),
                w.revision_id.clone(),
                w.version,
                s.name.clone(),
                s.text_workshop,
                s.text_poem.clone(),
            )
        };
        tracing::info!(
            event = "workshop_revision_save_started",
            session_id = sid,
            revision_id = pending.id
        );
        let persistent_text = text_poem.is_some();
        let saved = if let Some(poem) = text_poem {
            let write_pending = pending.clone();
            tokio::task::spawn_blocking(move || {
                MemoryStore::new(poem.root).save_current_revision(
                    &original,
                    &write_pending,
                    parent.as_deref(),
                )
            })
            .await?
        } else if text_workshop {
            Ok(format!("text_{}", pending.id))
        } else if self.config.haiku.memory_enabled {
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
                            && w.pending == previous_pending
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
                result["workshop_outcome"] = if text_workshop && !persistent_text {
                    "text_edit_applied"
                } else if action == "accept_pending" {
                    "pending_saved"
                } else {
                    "player_edit_saved"
                }
                .into();
                result["workshop_revision_id"] = revision_id.into();
            }
            Err(error) => {
                tracing::error!(event="workshop_revision_save_failed",session_id=sid,%error);
                // Preserve the validated proposal for an explicit retry; a
                // failed save must never claim that the canonical verse changed.
                let mut d = self.data.lock().unwrap();
                if let Some(w) = d
                    .sessions
                    .get_mut(sid)
                    .and_then(|s| s.haiku.workshop.as_mut())
                    .filter(|w| {
                        result["workshop_id"] == w.hud_id
                            && w.version == version
                            && w.pending == previous_pending
                    })
                {
                    w.pending = Some(pending);
                    w.conversation_candidate = None;
                    w.version += 1;
                    d.revision += 1;
                }
                result["workshop_outcome"] = "pending_save_failed".into();
                result["workshop_reason"] = error.to_string().into();
                result["text"] = if error.to_string() == "saved_poem_changed" {
                    "別の相談で句が更新されてるわ。案は残したで。句集の最新版を開き直してな。"
                } else {
                    "保存できんかったわ。元の句と案は残してあるで。"
                }
                .into();
                result["spoken_text"] = result["text"].clone();
            }
        }
        Ok(())
    }
}
