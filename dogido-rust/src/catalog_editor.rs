//! Typed catalogue edits only. A conversation or model cannot submit this operation.
use crate::{haiku::materials::Entries, reading_correction::Correction};
use anyhow::{Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::LazyLock;

static ENTRIES: LazyLock<Vec<Value>> = LazyLock::new(|| {
    let world = crate::world_catalog::catalog();
    let chat = crate::chat_catalog::catalog();
    let mut rows = Vec::new();
    let mut add = |kind: &str, id: &str, label: &str, entry: &Value| {
        if !label.is_empty() {
            rows.push(
                json!({"id":format!("{kind}:{id}"),"kind":kind,"surface":label,
                "reading":entry["reading"].as_str().unwrap_or(""),
                "note":entry["note"].as_str().unwrap_or("")}),
            );
        }
    };
    for (kind, entries) in [
        ("mob", chat.all_mob_entries()),
        ("structure", chat.structure_entries()),
        ("biome", world.biome_entries()),
    ] {
        for (id, entry) in entries {
            add(kind, id, entry["label"].as_str().unwrap_or(id), entry);
        }
    }
    for (id, label) in world.item_labels() {
        add(
            "item",
            id,
            label.as_str().unwrap_or(id),
            world.item_entry(id).unwrap_or(&Value::Null),
        );
    }
    for (id, label) in world.block_labels() {
        add(
            "block",
            id,
            label.as_str().unwrap_or(id),
            world.block_entry(id).unwrap_or(&Value::Null),
        );
    }
    rows.sort_by_key(|r| {
        (
            r["surface"].as_str().unwrap().to_owned(),
            r["id"].as_str().unwrap().to_owned(),
        )
    });
    rows
});

pub fn entries() -> &'static [Value] {
    &ENTRIES
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Edit {
    pub surface: String,
    pub reading: String,
    #[serde(default)]
    pub entry_id: Option<String>,
    #[serde(default)]
    pub wrong_reading: Option<String>,
    pub expected_id: Option<String>,
}
impl Edit {
    pub fn validate(&self) -> Result<Correction> {
        validate_surface(&self.surface)?;
        ensure!(valid_reading(&self.reading), "invalid_reading");
        ensure!(
            self.wrong_reading
                .as_deref()
                .is_none_or(|s| valid_reading(s) && s != self.reading),
            "invalid_wrong_reading"
        );
        if let Some(id) = &self.entry_id {
            ensure!(
                entries()
                    .iter()
                    .any(|r| r["id"] == *id && r["surface"] == self.surface),
                "catalog_entry_mismatch"
            );
        }
        Ok(Correction {
            surface: self.surface.clone(),
            reading: self.reading.clone(),
            wrong_reading: self.wrong_reading.clone(),
            explicit: true,
        })
    }
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Remove {
    pub surface: String,
    pub expected_id: String,
}
pub fn validate_surface(surface: &str) -> Result<()> {
    ensure!(
        !surface.is_empty()
            && surface.trim() == surface
            && surface.chars().count() <= 80
            && !surface.chars().any(char::is_control),
        "invalid_surface"
    );
    Ok(())
}
fn valid_reading(s: &str) -> bool {
    !s.is_empty()
        && s.chars().count() <= 80
        && s.chars().all(|c| ('ぁ'..='ん').contains(&c) || c == 'ー')
}
