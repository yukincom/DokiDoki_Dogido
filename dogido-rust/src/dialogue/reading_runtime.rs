//! フォームの読み編集を一度検証し、保存可能な状態ならMemoryStoreへ渡す。
//! 会話turnは作らず、状態ロックの外で保存する。保存版の現在性はMemoryStoreが再照合する。
use super::*;
use crate::{catalog_editor, haiku_record::MemoryStore};
use anyhow::ensure;

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

    pub async fn catalog_view(&self) -> Result<Value> {
        let enabled = self.config.haiku.memory_enabled;
        let root = self.config.haiku.memory_dir.clone();
        let rows = if enabled {
            tokio::task::spawn_blocking(move || MemoryStore::new(root).reading_corrections())
                .await??
        } else {
            vec![]
        };
        Ok(json!({"enabled":enabled,"entries":catalog_editor::entries(),"corrections":rows}))
    }

    pub async fn edit_catalog_reading(&self, edit: catalog_editor::Edit) -> Result<Value> {
        let edit = edit.validate()?;
        let _serial = self.serial.acquire().await?;
        ensure!(!self.data.lock().unwrap().stopped, "server_stopping");
        ensure!(self.config.haiku.memory_enabled, "memory_disabled");
        let root = self.config.haiku.memory_dir.clone();
        // An accepted write finishes even if the browser disconnects. No session,
        // model or speech is involved; disk I/O never holds the state mutex.
        let row = tokio::task::spawn_blocking(move || {
            MemoryStore::new(root).edit_catalog_reading(&edit, chrono::Utc::now())
        })
        .await??;
        Ok(json!({"saved":true,"correction":row}))
    }

    pub async fn remove_catalog_reading(&self, edit: catalog_editor::Remove) -> Result<Value> {
        let edit = edit.validate()?;
        let _serial = self.serial.acquire().await?;
        ensure!(!self.data.lock().unwrap().stopped, "server_stopping");
        ensure!(self.config.haiku.memory_enabled, "memory_disabled");
        let root = self.config.haiku.memory_dir.clone();
        tokio::task::spawn_blocking(move || {
            MemoryStore::new(root).remove_catalog_reading(&edit, chrono::Utc::now())
        })
        .await??;
        Ok(json!({"removed":true}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn catalog_input_errors_precede_disabled_memory_and_stopping() {
        let mut config = DialogueConfig {audio_enabled: false, llm_enabled: false, ..Default::default()};
        config.haiku.enabled = false;
        config.haiku.memory_enabled = false;
        let dialogue = Dialogue::new(config).unwrap();
        let edit = || catalog_editor::Edit {surface: "草地".into(), reading: "くさち".into(),
            entry_id: None, wrong_reading: None, expected_id: None};
        for stopped in [false, true] {
            dialogue.data.lock().unwrap().stopped = stopped;
            let invalid = catalog_editor::Edit {reading: "クサチ".into(), ..edit()};
            assert_eq!(dialogue.edit_catalog_reading(invalid).await.unwrap_err().to_string(), "invalid_reading");
            let invalid = catalog_editor::Remove {surface: "".into(), expected_id: "old".into()};
            assert_eq!(dialogue.remove_catalog_reading(invalid).await.unwrap_err().to_string(), "invalid_surface");
            let reason = if stopped { "server_stopping" } else { "memory_disabled" };
            assert_eq!(dialogue.edit_catalog_reading(edit()).await.unwrap_err().to_string(), reason);
            let remove = catalog_editor::Remove {surface: "草地".into(), expected_id: "old".into()};
            assert_eq!(dialogue.remove_catalog_reading(remove).await.unwrap_err().to_string(), reason);
        }
    }
}
