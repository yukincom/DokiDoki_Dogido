//! Explicit archive requests never consult a model. The original pin is captured
//! at input receipt, rechecked before I/O, and closed only after a successful save.
use super::*;
use crate::{
    haiku_record::{HaikuLine, MemoryStore},
    poem_input::{self, Input, WholeRevision},
    python_worker::Helper,
};
use anyhow::{Context, ensure};

impl Dialogue {
    pub(super) async fn save_poem_input(
        &self,
        sid: &str,
        epoch: u64,
        input: &Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Value> {
        let request: Input = serde_json::from_value(input["poem_input"].clone())?;
        self.memory_live(sid, epoch, input, false)?;
        let action = match request {
            Input::Revision { .. } => "haiku_revision",
            Input::SaveLast => "haiku_save_last",
            _ => "player_haiku",
        };
        let reply =
            |outcome: &str, text: &str| Self::memory_result(input, action, outcome, text.into());
        if request == Input::Invalid {
            return Ok(reply(
                "invalid_input",
                "句を省略せずに保存したいから、本文を一行から三行で書いてな。",
            ));
        }
        if !self.config.haiku.memory_enabled {
            return Ok(reply("memory_disabled", "記憶機能は今止まっとるで。"));
        }
        if matches!(request, Input::Revision { .. } | Input::SaveLast)
            && input["poem_reference"].is_null()
        {
            return Ok(reply("no_original", "まだ保存する元の句がないわ。"));
        }
        // Only the dictionary bridge supplies canonical readings. An unresolved
        // legacy archive payload remains text; it never invents three new lines.
        let lines: Vec<HaikuLine> = if let Input::Revision { text, source } = &request {
            let mut helper = Helper::start(
                &self.config.python,
                &self.config.helper.with_file_name("workshop_helper.py"),
            )?;
            let mut editing = crate::workshop_editing::Engine::default();
            let frame = json!({"op":"whole_verse","text":text,"source":source});
            let result = tokio::select! {_=bridge::cancelled(cancel)=>Err(anyhow::anyhow!("cancelled")),
            r=tokio::time::timeout(Duration::from_secs(15), editing.run(&mut helper, &frame))=>
                r.unwrap_or_else(|_|Err(anyhow::anyhow!("whole verse timed out")))};
            helper.finish(result.is_err()).await?;
            let crate::workshop_editing::Output::WholeVerse { lines } = result? else {
                anyhow::bail!("unexpected whole verse output");
            };
            poem_input::validate_lines(text, source, &lines)?;
            lines
        } else {
            vec![]
        };
        // Recheck after the helper wait, before authorizing any persistent write.
        self.memory_live(sid, epoch, input, false)?;
        let (name, original) = {
            let d = self.data.lock().unwrap();
            let s = d.sessions.get(sid).context("session_closed")?;
            ensure!(
                !d.stopped
                    && s.epoch == epoch
                    && workshop_combat_input::allowed(s)
                    && s.cancel.as_ref().is_some_and(|c| !*c.borrow()),
                "cancelled_poem_input"
            );
            let w = s.haiku.workshop.as_ref();
            if matches!(request, Input::Revision { .. } | Input::SaveLast)
                && !w.is_some_and(|w| {
                    input["poem_reference"]["id"] == w.hud_id
                        && input["poem_reference"]["version"] == w.version
                        && input["poem_reference"]["open"] == w.open
                        && !w.combat_paused()
                })
            {
                return Ok(reply(
                    "stale_original",
                    "元の句が変わったから、もう一度確認してな。",
                ));
            }
            (
                s.name.clone(),
                w.map(|w| {
                    (
                        w.emission.clone(),
                        w.current_lines.clone(),
                        w.revision_id.clone(),
                    )
                }),
            )
        };
        let root = self.config.haiku.memory_dir.join("sessions").join(sid);
        let write_sid = sid.to_owned();
        let operation = input["operation_id"]
            .as_str()
            .context("operation_id_required")?
            .to_owned();
        let event: GameEvent = serde_json::from_value(input["event"].clone())?;
        let revision = if let Input::Revision { text, source } = &request {
            let (emission, base, parent) = original.as_ref().context("original_required")?;
            Some(WholeRevision {
                id: format!("rev_{operation}"),
                at: chrono::Utc::now(),
                original: emission.clone(),
                base: base.clone(),
                parent: parent.clone(),
                text: text.clone(),
                source: source.clone(),
                lines,
            })
        } else {
            None
        };
        let write_revision = revision.clone();
        tracing::info!(event = "poem_input_save_started", session_id = sid, action);
        // Like pending adoption, an authorized write is awaited through speech
        // cancellation. The serial permit remains held until it has been reaped.
        let saved = tokio::task::spawn_blocking(move || -> Result<bool> {
            let store = MemoryStore::new(root);
            match request {
                Input::Player { text } => store.save_authored_poem(&operation, &event, &text),
                Input::SaveLast => Ok(store
                    .save_agent_haiku(&write_sid, &name, &original.context("original_required")?.0)?
                    .inserted),
                Input::Revision { .. } => {
                    let revision = write_revision.context("revision_required")?;
                    store.save_agent_haiku(&write_sid, &name, &revision.original)?;
                    store.save_whole_revision(&revision)?;
                    Ok(true)
                }
                Input::Invalid => unreachable!(),
            }
        })
        .await?;
        let inserted = match saved {
            Ok(inserted) => inserted,
            Err(error) => {
                tracing::warn!(event="poem_input_save_failed",session_id=sid,%error);
                return Ok(reply(
                    "save_failed",
                    "保存できんかったわ。元の句と案は残してあるで。",
                ));
            }
        };
        let mut result = reply(
            if inserted { "saved" } else { "already_saved" },
            if revision.is_some() {
                "元の句と直し、覚えといたで。"
            } else if inserted {
                "句を覚えといたで。"
            } else {
                "その句はもう覚えとるで。"
            },
        );
        if let Some(revision) = revision {
            let mut d = self.data.lock().unwrap();
            if let Some(w) = d
                .sessions
                .get_mut(sid)
                .and_then(|s| s.haiku.workshop.as_mut())
                .filter(|w| {
                    input["poem_reference"]["id"] == w.hud_id
                        && input["poem_reference"]["version"] == w.version
                })
            {
                if !revision.lines.is_empty() {
                    w.current_lines = revision.lines;
                    w.revision_id = Some(revision.id.clone());
                    w.materials.remove("line_sources");
                }
                w.pending = None;
                w.version += 1;
                w.close("explicit_close");
                result["workshop_closed_by_turn"] = true.into();
                result["workshop_revision_id"] = revision.id.into();
                d.revision += 1;
            }
        }
        Ok(result)
    }
}
