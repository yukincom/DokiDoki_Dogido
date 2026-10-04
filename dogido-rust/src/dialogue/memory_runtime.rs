//! 句の想起・指摘・soft lessonを、現在の会話から記憶の読込・保存へ結ぶ。
//! turnと保存処理の順序を揃え、ディスクI/Oの待機中はリアルタイム判断の状態lockを保持しない。
use super::*;
use crate::{haiku_memory::RecallQuery, haiku_record::MemoryStore, workshop_edit};
use anyhow::{Context, ensure};

impl Dialogue {
    pub async fn memory_view(&self, view: crate::memory_api::View) -> Result<Value> {
        if !self.config.haiku.memory_enabled {
            return Ok(view.disabled());
        }
        let root = self.config.haiku.memory_dir.clone();
        tokio::task::spawn_blocking(move || crate::memory_api::read(&root, view)).await?
    }

    /// 記憶処理を始める入力が、現在のsessionとworkshopにまだ属するか照合する。
    /// 生成後の取消・戦闘中断・句の更新を保存前に確認する。この検査自体はI/Oを行わない。
    /// 書込みを開始した後の完了待ちは、各保存処理が担当する。
    pub(super) fn memory_live(
        &self,
        sid: &str,
        epoch: u64,
        input: &Value,
        praise: bool,
    ) -> Result<()> {
        let d = self.data.lock().unwrap();
        let s = d.sessions.get(sid).context("session_closed")?;
        ensure!(
            !d.stopped
                && s.epoch == epoch
                && workshop_combat_input::allowed(s)
                && s.cancel.as_ref().is_some_and(|c| !*c.borrow()),
            "cancelled_memory_input"
        );
        if input["workshop"].is_object() {
            ensure!(
                s.haiku
                    .workshop
                    .as_ref()
                    .is_some_and(|w| input["workshop"]["workshop_id"] == w.hud_id
                        && input["workshop"]["version"] == w.version
                        && !w.combat_paused()
                        && (w.open
                            || (praise && w.close_reason.as_deref() == Some("explicit_close")))),
                "stale_workshop"
            );
        }
        Ok(())
    }

    pub(super) async fn active_lessons(&self) -> Result<Vec<Value>> {
        if !self.config.haiku.memory_enabled {
            return Ok(vec![]);
        }
        let root = self.config.haiku.memory_dir.clone();
        match tokio::task::spawn_blocking(move || {
            MemoryStore::new(root).active_lessons(chrono::Utc::now())
        })
        .await?
        {
            Ok(rows) => Ok(rows),
            Err(error) => {
                tracing::warn!(event="haiku_lessons_load_failed",%error);
                Ok(vec![])
            }
        }
    }

    pub(super) fn memory_result(input: &Value, action: &str, outcome: &str, text: String) -> Value {
        let mut r = json!({"text":text,"spoken_text":text,"llm_reports":[],"memory_action":action,"memory_outcome":outcome});
        if input["workshop"].is_object() {
            r["workshop_id"] = input["workshop"]["workshop_id"].clone();
            r["workshop_version"] = input["workshop"]["version"].clone();
            r["workshop_action"] = action.into();
            r["workshop_steps"] = json!([]);
        }
        r
    }

    pub(super) async fn clear_lessons(
        &self,
        sid: &str,
        epoch: u64,
        input: &Value,
    ) -> Result<Value> {
        ensure!(
            crate::haiku_memory::clear_requested(input["text"].as_str().unwrap_or("")),
            "clear_request_required"
        );
        self.memory_live(sid, epoch, input, false)?;
        let (text, outcome) = if !self.config.haiku.memory_enabled {
            ("記憶機能は今止まっとるで。", "memory_disabled")
        } else {
            let root = self.config.haiku.memory_dir.clone();
            tracing::info!(event = "haiku_lessons_clear_started", session_id = sid);
            // Like revision/reading saves, finish and reap an authorized write
            // even if a new input or combat cancels its spoken acknowledgement.
            match tokio::task::spawn_blocking(move || {
                MemoryStore::new(root).loosen_lessons(chrono::Utc::now())
            })
            .await?
            {
                Ok(()) => ("おけ、前の注意は気にせんでええわ。", "saved"),
                Err(error) => {
                    tracing::warn!(event="haiku_lessons_clear_failed",%error);
                    ("ちょっと保存に失敗したわ。", "save_failed")
                }
            }
        };
        Ok(Self::memory_result(
            input,
            "clear_lessons",
            outcome,
            text.into(),
        ))
    }

    pub(super) async fn recall_poems(
        &self,
        sid: &str,
        epoch: u64,
        input: &Value,
        query: &Value,
        cancel: &mut watch::Receiver<bool>,
    ) -> Result<Value> {
        self.memory_live(sid, epoch, input, false)?;
        let q: RecallQuery = serde_json::from_value(query.clone())?;
        let (text, outcome) = if !self.config.haiku.memory_enabled {
            ("記憶機能は今止まっとるで。".into(), "memory_disabled")
        } else {
            let root = self.config.haiku.memory_dir.clone();
            match tokio::task::spawn_blocking(move || MemoryStore::new(root).recall_reply(&q))
                .await?
            {
                Ok(text) => (text, "read"),
                Err(error) => {
                    tracing::warn!(event="haiku_recall_failed",%error);
                    ("保存した句を読み出せんかったわ。".into(), "read_failed")
                }
            }
        };
        let mut result = Self::memory_result(input, "haiku_recall", outcome, text);
        result["spoken_text"] = super::tts_runtime::read(
            &self.config,
            result["text"].as_str().context("recall text")?,
            cancel,
            tokio::time::Instant::now() + Duration::from_secs(30),
        )
        .await?
        .into();
        self.memory_live(sid, epoch, input, false)?;
        Ok(result)
    }

    pub(super) async fn record_feedback(
        &self,
        sid: &str,
        epoch: u64,
        input: &Value,
        result: &mut Value,
    ) {
        let step = result["workshop_feedback"].clone();
        if !self.config.haiku.memory_enabled || crate::haiku_memory::feedback_kind(&step).is_none()
        {
            return;
        }
        if self
            .memory_live(sid, epoch, input, step["action"] == "praise")
            .is_err()
        {
            return;
        }
        let view = &input["workshop"];
        let lines: Result<Vec<crate::haiku_record::HaikuLine>, _> =
            serde_json::from_value(if view["pending"].is_object() {
                view["pending"]["lines"].clone()
            } else {
                view["current_lines"].clone()
            });
        let Ok(lines) = lines else {
            return;
        };
        let critique = json!({"entry_id":view["entry_id"],"player_text":input["text"],
            "surface_at_time":workshop_edit::reading(&lines),"materials_snapshot":view["materials"],"session_id":sid});
        let root = self.config.haiku.memory_dir.clone();
        tracing::info!(event = "haiku_feedback_save_started", session_id = sid);
        match tokio::task::spawn_blocking(move || {
            MemoryStore::new(root).save_feedback(critique, &step, chrono::Utc::now())
        })
        .await
        {
            Ok(Ok(())) => result["workshop_feedback_outcome"] = "saved".into(),
            error => {
                tracing::warn!(event = "haiku_feedback_save_failed", ?error);
                result["workshop_feedback_outcome"] = "save_failed".into();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn recall_reading_does_not_start_python_change_display_or_rewrite_memory() {
        let root =
            std::env::temp_dir().join(format!("dogido-recall-reading-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join("long_term")).unwrap();
        let entry = root.join("long_term/haiku_entries.jsonl");
        let bytes=json!({"id":"fixture","created_at":"2026-09-29T00:00:00Z","text":"今朝の草地","world":{"biome":"plains"}}).to_string()+"\n";
        std::fs::write(&entry, &bytes).unwrap();
        let helper = root.join("haiku_tokens.py");
        std::fs::write(&helper, "raise AssertionError('must not start')\n").unwrap();
        let mut config = DialogueConfig {
            python: "/missing/recall-python".into(),
            helper,
            reading_engine: "off".into(),
            audio_enabled: false,
            base_url: "http://127.0.0.1:9/v1".into(),
            ..Default::default()
        };
        config.haiku.memory_enabled = true;
        config.haiku.memory_dir = root.clone();
        let dialogue = Dialogue::new(config).unwrap();
        dialogue.register("s", "読みの試験", true);
        let (owner, mut cancel) = watch::channel(false);
        dialogue
            .data
            .lock()
            .unwrap()
            .sessions
            .get_mut("s")
            .unwrap()
            .cancel = Some(owner.clone());
        let query = serde_json::to_value(RecallQuery::default()).unwrap();
        let input = json!({"text":"句を思い出して","reading_corrections":[{"surface":"草地","reading":"catalog-only"}]});
        let result = dialogue
            .recall_poems("s", 0, &input, &query, &mut cancel)
            .await
            .unwrap();
        assert!(result["text"].as_str().unwrap().contains("今朝の草地"));
        assert!(
            result["spoken_text"]
                .as_str()
                .unwrap()
                .contains("今あさのくさち")
        );
        assert!(
            !result["spoken_text"]
                .as_str()
                .unwrap()
                .contains("catalog-only")
        );
        assert_eq!(result["memory_action"], "haiku_recall");
        assert_eq!(std::fs::read_to_string(&entry).unwrap(), bytes);
        owner.send(true).unwrap();
        assert!(
            dialogue
                .recall_poems("s", 0, &input, &query, &mut cancel)
                .await
                .is_err()
        );
        dialogue.shutdown().await;
        std::fs::remove_dir_all(root).unwrap();
    }
}
