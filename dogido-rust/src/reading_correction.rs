//! Explicit catalog pronunciation corrections. No model, verse edit or lesson.
use crate::haiku_record::{MemoryStore, SAVE_LOCK, append_line, locked_file};
use anyhow::{Result, ensure};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs::File,
    io::{BufRead, BufReader},
    path::PathBuf,
};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Correction {
    pub surface: String,
    pub reading: String,
    pub wrong_reading: Option<String>,
    pub explicit: bool,
}

fn kana(text: &str) -> bool {
    !text.is_empty()
        && text
            .chars()
            .all(|c| ('ぁ'..='ん').contains(&c) || c == 'ー')
}
fn kanji(text: &str) -> bool {
    text.chars().any(|c| ('一'..='鿿').contains(&c))
}
fn value(surface: &str, reading: &str, explicit: bool) -> Option<Correction> {
    let (surface, reading) = (surface.trim(), reading.trim());
    if ["ではない", "じゃない", "ではなく", "じゃなく"]
        .iter()
        .any(|s| reading.ends_with(s))
    {
        return None;
    }
    if surface.is_empty()
        || surface == reading
        || !kana(reading)
        || surface.contains(['\n', '\r', '「', '」', '『', '』', '"', '？', '?'])
    {
        return None;
    }
    Some(Correction {
        surface: surface.into(),
        reading: reading.into(),
        wrong_reading: None,
        explicit,
    })
}

/// Raw STT/typed input only. Reject embedded quotations and explicit negation.
/// Natural editing remains workshop's; a model cannot create this operation.
pub fn parse(raw: &str) -> Option<Correction> {
    let text = raw.trim();
    if let Some(rest) = text
        .strip_prefix("読み")
        .or_else(|| text.strip_prefix("よみ"))
    {
        let rest = rest
            .trim_start()
            .trim_start_matches([':', '：'])
            .trim_start();
        if let Some((surface, reading)) = rest.split_once(['=', '＝', ':', '：']) {
            return value(surface, reading, true);
        }
    }
    for marker in ["の読みは", "のよみは"] {
        if let Some((surface, reading)) = text.split_once(marker) {
            if surface.chars().count() > 20 {
                return None;
            }
            return value(surface, reading.trim(), true);
        }
    }
    if let Some((surface, reading)) = text.split_once('は') {
        let reading = reading.trim();
        if kanji(surface)
            && surface.chars().count() <= 20
            && reading.chars().count() <= 16
            && !surface.contains(['、', '。', '！', '!'])
            && ![
                "にして",
                "として",
                "どう",
                "でした",
                "ですか",
                "なら",
                "けど",
                "から",
                "ので",
                "ほう",
                "まま",
                "ってこと",
                "ということ",
            ]
            .iter()
            .any(|s| reading.contains(s))
        {
            return value(surface, reading, false);
        }
    }
    // Legacy kana correction, but require a whole utterance (not a quote inside
    // another request). Biome resolution is separate and needs current context.
    let folded: String = text
        .chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect();
    for marker in ["じゃなくて", "ではなく", "やなくて", "じゃなく"] {
        if let Some((wrong, right)) = folded.split_once(marker)
            && kana(wrong)
            && wrong.chars().count() >= 2
            && right.trim().chars().count() >= 2
        {
            let mut c = value(wrong, right, true)?;
            c.wrong_reading = Some(wrong.into());
            return Some(c);
        }
    }
    None
}

pub fn grounded_in_workshop(c: &Correction, workshop: &Value) -> bool {
    let m = &workshop["materials"];
    ["biome_ja", "structure_ja", "held_item"]
        .iter()
        .any(|k| m[k].as_str() == Some(&c.surface))
        || m["catalog_sources"]
            .as_array()
            .is_some_and(|rows| rows.iter().any(|r| r["label"] == c.surface))
        || m["source_atoms"].as_array().is_some_and(|rows| {
            rows.iter()
                .any(|r| r["kind"] == "catalog_label" && r["text"] == c.surface)
        })
}

pub fn for_input(raw: &str, workshop: &Value) -> Option<Correction> {
    let c = parse(raw)?;
    (c.explicit || !workshop.is_object() || grounded_in_workshop(&c, workshop)).then_some(c)
}

impl Correction {
    /// Preserve the old biome shorthand only when its target is actually known.
    /// An explicit named surface is never replaced with the current biome.
    pub fn resolve_biome(&mut self, biome: Option<&str>) -> Option<String> {
        let biome = biome?.trim();
        let biome = biome.strip_prefix("minecraft:").unwrap_or(biome);
        if biome.is_empty() {
            return None;
        }
        if kana(&self.surface) && self.wrong_reading.is_some() {
            let label = crate::environment::ambient::biome_label(biome);
            if label == biome {
                return None;
            }
            self.surface = label;
        }
        Some(format!("biome:{biome}"))
    }
    pub fn needs_surface(&self) -> bool {
        self.wrong_reading.is_some() && kana(&self.surface)
    }
}

impl MemoryStore {
    /// The form supplies an explicit target and the version it displayed. Check
    /// under the same process/file locks as the append, including cross-process writers.
    pub fn edit_catalog_reading(
        &self,
        edit: &crate::catalog_editor::Edit,
        at: DateTime<Utc>,
    ) -> Result<Value> {
        let c = edit.validate()?;
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let mut file = locked_file(&self.corrections_path())?;
        let rows = self.reading_corrections()?;
        let current = rows.iter().find(|row| row["surface"] == c.surface);
        ensure!(
            current.and_then(|row| row["id"].as_str()) == edit.expected_id.as_deref(),
            "reading_changed"
        );
        let row = json!({"id":format!("corr_{}", uuid::Uuid::new_v4()),
            "created_at":at.to_rfc3339(),"surface":c.surface,"reading":c.reading,
            "wrong_reading":c.wrong_reading,"forbidden_readings":c.wrong_reading.iter().collect::<Vec<_>>(),
            "source":edit.entry_id,"session_id":null,"origin":"catalog_form"});
        append_line(&mut file, &row)?;
        Ok(row)
    }

    /// Append a reversible removal, retaining the original record for audit.
    pub fn remove_catalog_reading(
        &self,
        edit: &crate::catalog_editor::Remove,
        at: DateTime<Utc>,
    ) -> Result<()> {
        crate::catalog_editor::validate_surface(&edit.surface)?;
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let mut file = locked_file(&self.corrections_path())?;
        let rows = self.reading_corrections()?;
        let current = rows.iter().find(|row| row["surface"] == edit.surface);
        ensure!(
            current.and_then(|row| row["id"].as_str()) == Some(edit.expected_id.as_str()),
            "reading_changed"
        );
        append_line(
            &mut file,
            &json!({"id":format!("corr_{}", uuid::Uuid::new_v4()),
            "created_at":at.to_rfc3339(),"surface":edit.surface,"operation":"remove",
            "previous_id":edit.expected_id,"origin":"catalog_form"}),
        )?;
        Ok(())
    }
    pub fn corrections_path(&self) -> PathBuf {
        self.root().join("long_term/catalog_corrections.jsonl")
    }

    pub fn save_reading_correction(
        &self,
        c: &Correction,
        source: Option<&str>,
        at: DateTime<Utc>,
        sid: &str,
    ) -> Result<Value> {
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let row = json!({"id":format!("corr_{}_{}", at.format("%Y%m%d_%H%M%S"),c.surface),
            "created_at":at.to_rfc3339(),"surface":c.surface,"reading":c.reading,
            "wrong_reading":c.wrong_reading,"forbidden_readings":c.wrong_reading.iter().collect::<Vec<_>>(),
            "source":source,"session_id":sid});
        append_line(&mut locked_file(&self.corrections_path())?, &row)?;
        Ok(row)
    }

    /// Read only. Missing storage is empty, not created. Latest correction wins.
    pub fn reading_corrections(&self) -> Result<Vec<Value>> {
        let file = match File::open(self.corrections_path()) {
            Ok(f) => f,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
            Err(e) => return Err(e.into()),
        };
        let mut latest = std::collections::BTreeMap::new();
        for line in BufReader::new(file).lines() {
            let Ok(row) = serde_json::from_str::<Value>(&line?) else {
                continue;
            };
            if row["operation"] == "remove" {
                if let Some(surface) = row["surface"].as_str() {
                    latest.remove(surface);
                }
                continue;
            }
            if let (Some(surface), Some(reading)) =
                (row["surface"].as_str(), row["reading"].as_str())
                && !surface.trim().is_empty()
                && kana(reading)
            {
                latest.insert(surface.to_owned(), row);
            }
        }
        Ok(latest.into_values().collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_and_grounded_shorthand_do_not_steal_edits() {
        assert_eq!(parse("読み: 荒野=こうや").unwrap().reading, "こうや");
        let w = json!({"materials":{"held_item":"草地"}});
        assert!(for_input("読み: 草地=くさち", &json!({})).is_some());
        assert!(for_input("草地の読みはくさち", &json!({})).is_some());
        assert!(for_input("草地はくさち", &w).is_some());
        assert!(for_input("草地はくさち", &json!({})).is_none());
        assert!(for_input("草地はくさち", &Value::Null).is_some());
        for s in [
            "おはようさん",
            "上五をさくらいろにして",
            "草地はくさちにしてはどう",
            "草地の読みはくさちではない",
            "「草地」の読みはくさち",
            "読み: 草地=くさち？",
            "『そうちじゃなくてくさち』と言った",
        ] {
            assert!(parse(s).is_none(), "{s}");
        }
    }
    #[test]
    fn kana_shorthand_requires_known_biome() {
        let mut c = parse("そうちじゃなくてくさち").unwrap();
        assert_eq!(
            c.resolve_biome(Some("minecraft:meadow")).as_deref(),
            Some("biome:meadow")
        );
        assert_eq!(c.surface, "草地");
        assert!(!c.needs_surface());
        let mut c = parse("そうちじゃなくてくさち").unwrap();
        assert_eq!(c.resolve_biome(Some("unknown")), None);
        assert!(c.needs_surface());
    }
    #[test]
    fn append_reload_and_latest_wins_without_touching_verse() {
        let root = std::env::temp_dir().join(format!("dogido-reading-{}", uuid::Uuid::new_v4()));
        let store = MemoryStore::new(&root);
        assert!(store.reading_corrections().unwrap().is_empty());
        assert!(!root.exists());
        let c = parse("草地の読みはくさち").unwrap();
        store
            .save_reading_correction(&c, Some("biome:meadow"), Utc::now(), "ses_a")
            .unwrap();
        let c = parse("草地の読みはくさぢ").unwrap();
        store
            .save_reading_correction(&c, None, Utc::now(), "ses_b")
            .unwrap();
        let rows = MemoryStore::new(&root).reading_corrections().unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["reading"], "くさぢ");
        assert!(!store.entries_path().exists());
        std::fs::remove_dir_all(root).unwrap();
    }
}
