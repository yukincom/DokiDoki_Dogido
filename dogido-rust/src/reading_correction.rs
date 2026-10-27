//! 明示された読み訂正をJSONLへ追記し、現在有効な読みを返す。
//! フォーム入力の形式はcatalog_editorが検証済み。ここでは保存ロックと最新の版照合を所有する。
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
    /// 検証済みの編集を受け、ロック取得・最新記録読込・版照合の順で追記する。
    /// 入力は不変でも他プロセスが保存版を変え得るため、expected_idはこの時点で照合する。
    pub fn edit_catalog_reading(
        &self,
        edit: &crate::catalog_editor::ValidatedEdit,
        at: DateTime<Utc>,
    ) -> Result<Value> {
        let edit = edit.input();
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let mut file = locked_file(&self.corrections_path())?;
        let rows = self.reading_corrections()?;
        let current = rows.iter().find(|row| row["surface"] == edit.surface);
        ensure!(
            current.and_then(|row| row["id"].as_str()) == edit.expected_id.as_deref(),
            "reading_changed"
        );
        let row = json!({"id":format!("corr_{}", uuid::Uuid::new_v4()),
            "created_at":at.to_rfc3339(),"surface":edit.surface,"reading":edit.reading,
            "wrong_reading":edit.wrong_reading,"forbidden_readings":edit.wrong_reading.iter().collect::<Vec<_>>(),
            "source":edit.entry_id,"session_id":null,"origin":"catalog_form"});
        append_line(&mut file, &row)?;
        Ok(row)
    }

    /// 検証済みの取消を受け、最新の登録IDと一致した場合だけ取消行を追記する。原記録は残す。
    pub fn remove_catalog_reading(
        &self,
        edit: &crate::catalog_editor::ValidatedRemove,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let edit = edit.input();
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
    fn form() -> crate::catalog_editor::Edit {
        crate::catalog_editor::Edit {
            surface: "草地".into(), reading: "くさち".into(),
            entry_id: Some("biome:meadow".into()), wrong_reading: Some("そうち".into()),
            expected_id: None,
        }
    }

    #[test]
    fn catalog_invalid_input_never_creates_storage() {
        let root = std::env::temp_dir().join(format!("dogido-catalog-invalid-{}", uuid::Uuid::new_v4()));
        let store = MemoryStore::new(&root);
        let mut cases = Vec::new();
        for surface in ["".into(), " 草地".into(), "草\n地".into(), "あ".repeat(81)] {
            cases.push((crate::catalog_editor::Edit { surface, ..form() }, "invalid_surface"));
        }
        for reading in ["".into(), "クサチ".into(), "あ".repeat(81)] {
            cases.push((crate::catalog_editor::Edit { reading, ..form() }, "invalid_reading"));
        }
        for wrong_reading in ["くさち", "ソウチ"] {
            cases.push((crate::catalog_editor::Edit { wrong_reading: Some(wrong_reading.into()), ..form() }, "invalid_wrong_reading"));
        }
        cases.push((crate::catalog_editor::Edit { entry_id: Some("biome:forest".into()), ..form() }, "catalog_entry_mismatch"));
        for (edit, reason) in cases {
            let result = edit.validate().and_then(|edit| store.edit_catalog_reading(&edit, Utc::now()));
            assert_eq!(result.unwrap_err().to_string(), reason);
        }
        let result = crate::catalog_editor::Remove {surface: "".into(), expected_id: "old".into()}
            .validate().and_then(|edit| store.remove_catalog_reading(&edit, Utc::now()));
        assert_eq!(result.unwrap_err().to_string(), "invalid_surface");
        assert!(!root.exists());
    }

    #[test]
    fn catalog_prevalidated_edits_still_compare_latest_saved_version() {
        let root = std::env::temp_dir().join(format!("dogido-catalog-cas-{}", uuid::Uuid::new_v4()));
        let store = MemoryStore::new(&root);
        let first = form().validate().unwrap();
        let second = crate::catalog_editor::Edit {reading: "くさっぱら".into(), ..form()}.validate().unwrap();
        let saved = store.edit_catalog_reading(&first, Utc::now()).unwrap();
        let bytes = std::fs::read(store.corrections_path()).unwrap();
        // どちらも保存前に検証済みでも、後続の保存は先行更新を検出する。
        assert_eq!(MemoryStore::new(&root).edit_catalog_reading(&second, Utc::now()).unwrap_err().to_string(), "reading_changed");
        assert_eq!(std::fs::read(store.corrections_path()).unwrap(), bytes);
        assert_eq!(store.reading_corrections().unwrap(), vec![saved]);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn catalog_prevalidated_removal_rechecks_version_and_preserves_audit() {
        let root = std::env::temp_dir().join(format!("dogido-catalog-remove-{}", uuid::Uuid::new_v4()));
        let store = MemoryStore::new(&root);
        let first = store.edit_catalog_reading(&form().validate().unwrap(), Utc::now()).unwrap();
        let id = first["id"].as_str().unwrap().to_owned();
        let remove = crate::catalog_editor::Remove {surface: "草地".into(), expected_id: id.clone()}.validate().unwrap();
        let edit = crate::catalog_editor::Edit {expected_id: Some(id), reading: "くさっぱら".into(), ..form()}.validate().unwrap();
        let second = MemoryStore::new(&root).edit_catalog_reading(&edit, Utc::now()).unwrap();
        let bytes = std::fs::read(store.corrections_path()).unwrap();
        assert_eq!(store.remove_catalog_reading(&remove, Utc::now()).unwrap_err().to_string(), "reading_changed");
        assert_eq!(std::fs::read(store.corrections_path()).unwrap(), bytes);
        let remove = crate::catalog_editor::Remove {surface: "草地".into(), expected_id: second["id"].as_str().unwrap().into()}.validate().unwrap();
        store.remove_catalog_reading(&remove, Utc::now()).unwrap();
        assert!(store.reading_corrections().unwrap().is_empty());
        let audit = std::fs::read_to_string(store.corrections_path()).unwrap();
        let rows: Vec<Value> = audit.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0], first);
        assert_eq!(rows[1], second);
        assert_eq!(rows[2]["previous_id"], second["id"]);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn kana_shorthand_requires_known_biome() {
        let mut c = Correction {
            surface: "そうち".into(),
            reading: "くさち".into(),
            wrong_reading: Some("そうち".into()),
            explicit: true,
        };
        assert_eq!(
            c.resolve_biome(Some("minecraft:meadow")).as_deref(),
            Some("biome:meadow")
        );
        assert_eq!(c.surface, "草地");
        assert!(!c.needs_surface());
        let mut c = Correction {
            surface: "そうち".into(),
            reading: "くさち".into(),
            wrong_reading: Some("そうち".into()),
            explicit: true,
        };
        assert_eq!(c.resolve_biome(Some("unknown")), None);
        assert!(c.needs_surface());
    }
    #[test]
    fn append_reload_and_latest_wins_without_touching_verse() {
        let root = std::env::temp_dir().join(format!("dogido-reading-{}", uuid::Uuid::new_v4()));
        let store = MemoryStore::new(&root);
        assert!(store.reading_corrections().unwrap().is_empty());
        assert!(!root.exists());
        let c = Correction {
            surface: "草地".into(),
            reading: "くさち".into(),
            wrong_reading: None,
            explicit: true,
        };
        store
            .save_reading_correction(&c, Some("biome:meadow"), Utc::now(), "ses_a")
            .unwrap();
        let c = Correction {
            surface: "草地".into(),
            reading: "くさぢ".into(),
            wrong_reading: None,
            explicit: true,
        };
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
