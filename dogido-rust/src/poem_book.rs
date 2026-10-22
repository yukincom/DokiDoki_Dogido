//! 保存句の出所と最新採用版を読み出す。句正本・版・表記を照合し、未確定読みは空のまま返す。
//! 保存・編集は行わず、句集と相談室へ同じ行オブジェクトを渡す。
use crate::haiku_record::{Emission, HaikuLine, MemoryStore, PreparedEmission, ResolvedLines};
use crate::workshop_edit::{editing_text, reading, surface};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Clone)]
pub struct SavedPoem {
    pub key: String,
    pub root: PathBuf,
    pub original: Emission,
    pub lines: Vec<HaikuLine>,
    pub revision_id: Option<String>,
    pub revisions: Vec<Value>,
}
impl SavedPoem {
    pub fn public(&self) -> Value {
        json!({"key":self.key,"id":self.original.entry_id(),"created_at":self.original.created_at,
            "text":surface(&self.lines),"original_text":surface(&self.original.prepared.lines),
            "revision_id":self.revision_id,"interpretation":self.original.prepared.interpretation})
    }
    pub fn lines_at(&self, revision: Option<&str>) -> Result<Vec<HaikuLine>> {
        let Some(id) = revision else {
            return Ok(self.original.prepared.lines.clone());
        };
        let row = self
            .revisions
            .iter()
            .find(|r| r["id"] == id)
            .context("saved revision missing")?;
        let lines: Vec<HaikuLine> = serde_json::from_value(row["lines"].clone())?;
        ResolvedLines::try_from(lines.as_slice())?;
        ensure!(
            editing_text(&lines) == row["revised_text"],
            "revision reading mismatch"
        );
        Ok(lines)
    }
}

pub fn directories(root: &Path) -> Result<Vec<PathBuf>> {
    let mut dirs = vec![root.to_owned()];
    // 親のリンク経由でも別の保管庫を列挙しない。子リンクと同じ扱い。
    if std::fs::symlink_metadata(root.join("sessions")).is_ok_and(|m| m.file_type().is_symlink()) {
        return Ok(dirs);
    }
    match std::fs::read_dir(root.join("sessions")) {
        Ok(entries) => {
            for entry in entries {
                let entry = entry?;
                if entry.file_type()?.is_dir() {
                    dirs.push(entry.path());
                }
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e.into()),
    }
    dirs[1..].sort();
    Ok(dirs)
}

pub fn load(root: &Path) -> Result<BTreeMap<String, SavedPoem>> {
    let mut poems = BTreeMap::new();
    for dir in directories(root)? {
        let store = MemoryStore::new(&dir);
        let revisions = store.local_poem_rows("haiku_revisions.jsonl")?;
        for r in store.local_poem_rows("haiku_entries.jsonl")? {
            if r["kind"] != "agent_haiku" || !r["lines"].is_array() {
                continue;
            }
            let prepared: PreparedEmission = serde_json::from_value(json!({
                "text":r["text"],"surface_text":r["surface_text"],"reading_text":r["reading_text"],
                "lines":r["lines"],"materials":r["materials_snapshot"],"interpretation":r["interpretation"],
                "preface":r["preface"],"biome":r["world"]["biome"],"structure":r["world"]["structure"],
                "time_phase":r["world"]["time_phase"],"dimension":r["world"]["dimension"],
                "event_sequence":r["trigger"]["event_sequence"],"route":r["trigger"]["route"]
            }))?;
            let at = chrono::DateTime::parse_from_rfc3339(
                r["created_at"].as_str().context("missing timestamp")?,
            )?
            .to_utc();
            let original = prepared.complete(at)?;
            ensure!(
                r["id"] == original.entry_id(),
                "saved poem identity mismatch"
            );
            let local: Vec<_> = revisions
                .iter()
                .filter(|v| v["haiku_id"] == r["id"])
                .cloned()
                .collect();
            let revision_id = local
                .last()
                .and_then(|v| v["id"].as_str())
                .map(str::to_owned);
            let source = if dir == root {
                "root".to_owned()
            } else {
                dir.file_name().unwrap().to_string_lossy().into_owned()
            };
            let key = format!("{source}:{}", original.entry_id());
            let mut poem = SavedPoem {
                key: key.clone(),
                root: dir.clone(),
                lines: vec![],
                original,
                revision_id,
                revisions: local,
            };
            poem.lines = poem.lines_at(poem.revision_id.as_deref())?;
            poems.insert(key, poem);
        }
    }
    Ok(poems)
}

/// The normal poem-book API keeps original metadata and projects the adopted text.
pub fn rows(root: &Path) -> Result<Vec<Value>> {
    let mut result = vec![];
    for dir in directories(root)? {
        let store = MemoryStore::new(dir);
        let revisions = store.local_poem_rows("haiku_revisions.jsonl")?;
        for mut row in store.local_poem_rows("haiku_entries.jsonl")? {
            if let Some(revision) = revisions
                .iter()
                .rev()
                .find(|r| r["haiku_id"].is_string() && r["haiku_id"] == row["id"])
                && let Some(text) = revision["revised_surface_text"]
                    .as_str()
                    .or_else(|| revision["revised_text"].as_str())
            {
                row["original_text"] = row["text"].clone();
                row["text"] = text.into();
                row["surface_text"] = text.into();
                // 表記を含む編集本文を確定読みに昇格させない。読みは保存行から組み立てる。
                row["reading_text"] = if revision["lines"].is_array() {
                    reading(&serde_json::from_value::<Vec<HaikuLine>>(
                        revision["lines"].clone(),
                    )?)
                    .into()
                } else {
                    Value::Null
                };
                row["lines"] = revision["lines"].clone();
                row["revision_id"] = revision["id"].clone();
                row["revised_at"] = revision["created_at"].clone();
            }
            if !result.contains(&row) {
                result.push(row);
            }
        }
    }
    Ok(result)
}
