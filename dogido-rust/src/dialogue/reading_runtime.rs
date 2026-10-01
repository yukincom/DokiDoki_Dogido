//! Pronunciation edits are explicit typed catalogue operations, not dialogue turns.
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
        edit.validate()?;
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
        catalog_editor::validate_surface(&edit.surface)?;
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
