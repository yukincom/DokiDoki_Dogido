//! Player-authored local edits. Rust owns pending/CAS, adoption and append-only revisions.
use crate::{
    haiku::meter::count_japanese_sounds,
    haiku_record::{Emission, HaikuLine, MemoryStore, SAVE_LOCK, append_line, locked_file},
};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader};

pub const CONTRACT: &str = "player_line_compare_and_swap_v1";

pub fn reading(lines: &[HaikuLine]) -> String {
    lines
        .iter()
        .map(|l| l.reading_text.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}
pub fn surface(lines: &[HaikuLine]) -> String {
    lines
        .iter()
        .map(|l| l.surface_text.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Pending {
    pub id: String,
    pub base: Vec<HaikuLine>,
    pub lines: Vec<HaikuLine>,
    pub selected_line: usize,
}
impl Pending {
    pub fn stage(
        base: &[HaikuLine],
        working: &[HaikuLine],
        lines: Vec<HaikuLine>,
        selected: usize,
    ) -> Result<Self> {
        ensure!(
            base.len() == 3 && working.len() == 3 && lines.len() == 3 && selected < 3,
            "invalid_line_records"
        );
        for i in 0..3 {
            ensure!(
                i == selected || lines[i] == working[i],
                "untargeted_line_changed"
            );
        }
        ensure!(
            lines[selected].reading_text != working[selected].reading_text,
            "no_change"
        );
        let pending = Self {
            id: format!("hr_{}", uuid::Uuid::new_v4().simple()),
            base: base.to_vec(),
            lines,
            selected_line: selected,
        };
        pending.validate(base)?;
        Ok(pending)
    }
    pub fn validate(&self, current: &[HaikuLine]) -> Result<()> {
        ensure!(
            self.base == current
                && current.len() == 3
                && self.lines.len() == 3
                && self.selected_line < 3,
            "stale_edit"
        );
        ensure!(reading(&self.base) != reading(&self.lines), "no_change");
        for (i, (old, new)) in current.iter().zip(&self.lines).enumerate() {
            ensure!(
                old.line_index == i
                    && new.line_index == i
                    && old.line_id == new.line_id
                    && old.position == new.position
                    && old.canonical_name == new.canonical_name,
                "line_identity_changed"
            );
            if old != new {
                ensure!(old.reading_text != new.reading_text, "reading_unchanged");
                ensure!(
                    !new.surface_text.trim().is_empty() && !new.surface_text.contains(['\n', '\r']),
                    "invalid_surface"
                );
                ensure!(
                    new.provenance == "player_explicit"
                        && new.source_atom_ids.is_empty()
                        && new.source_atoms.is_empty(),
                    "invented_player_source"
                );
                ensure!(
                    !new.reading_text.is_empty()
                        && new
                            .reading_text
                            .chars()
                            .all(|c| ('\u{3041}'..='\u{3096}').contains(&c) || c == 'ー'),
                    "unresolved_reading"
                );
                ensure!(
                    count_japanese_sounds(&new.reading_text) == [5, 7, 5][i],
                    "meter_not_exact"
                );
                ensure!(
                    !self
                        .lines
                        .iter()
                        .enumerate()
                        .any(|(j, l)| i != j && l.reading_text == new.reading_text),
                    "duplicate_line"
                );
            }
        }
        Ok(())
    }
    pub fn edits(&self) -> Vec<Value> {
        self.base.iter().zip(&self.lines).enumerate().filter(|(_, (a,b))| a.reading_text!=b.reading_text).map(|(i,(a,b))| json!({
            "line_index":i,"expected_text":a.reading_text,"replacement_text":b.reading_text,"provenance":"player_explicit"
        })).collect()
    }
    fn record(&self, original: &Emission, parent: Option<&str>) -> Value {
        json!({"id":self.id,"created_at":chrono::Utc::now(),"haiku_id":original.entry_id(),
            "source":"player_line_confirmed","comment":null,
            "original_text":surface(&original.prepared.lines),"original_reading_text":reading(&original.prepared.lines),
            "base_text":reading(&self.base),"base_surface_text":surface(&self.base),"parent_revision_id":parent,
            "revised_text":reading(&self.lines),"revised_surface_text":surface(&self.lines),"lines":self.lines,
            "line_sources":null,"edit_contract":CONTRACT,"edits":self.edits(),
            "world":{"biome":original.prepared.biome,"structure":original.prepared.structure,"time_phase":original.prepared.time_phase,"dimension":original.prepared.dimension}})
    }
}

impl MemoryStore {
    pub fn save_player_revision(
        &self,
        original: &Emission,
        pending: &Pending,
        parent: Option<&str>,
    ) -> Result<String> {
        pending.validate(&pending.base)?;
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let mut file = locked_file(&self.root().join("long_term/haiku_revisions.jsonl"))?;
        let rows: Vec<Value> = BufReader::new(&file)
            .lines()
            .map(|l| Ok(serde_json::from_str(&l?)?))
            .collect::<Result<_>>()?;
        let record = pending.record(original, parent);
        if let Some(existing) = rows.iter().find(|r| r["id"] == pending.id) {
            // A retry after a successful append/sync error must not append twice.
            let mut expected = record.clone();
            expected["created_at"] = existing["created_at"].clone();
            ensure!(expected == *existing, "revision_id_conflict");
            return Ok(pending.id.clone());
        }
        if let Some(parent) = parent {
            ensure!(
                rows.iter().any(|r| r["id"] == parent
                    && r["haiku_id"] == original.entry_id()
                    && r["lines"] == json!(pending.base)),
                "parent_revision_mismatch"
            );
        } else {
            ensure!(
                pending.base == original.prepared.lines,
                "missing_parent_revision"
            );
        }
        append_line(&mut file, &record)?;
        Ok(pending.id.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn lines() -> Vec<HaikuLine> {
        ["さくらのは", "くろいおのへと", "あさのいろ"]
            .iter()
            .enumerate()
            .map(|(i, t)| HaikuLine {
                line_id: format!("line_{}", i + 1),
                line_index: i,
                position: ["upper", "middle", "lower"][i].into(),
                canonical_name: ["上五", "中七", "下五"][i].into(),
                surface_text: (*t).into(),
                reading_text: (*t).into(),
                source_atom_ids: vec![format!("source:{i}")],
                source_atoms: vec![],
                provenance: "generated".into(),
            })
            .collect()
    }
    fn replace(mut lines: Vec<HaikuLine>, i: usize, text: &str) -> Vec<HaikuLine> {
        lines[i].surface_text = text.into();
        lines[i].reading_text = text.into();
        lines[i].provenance = "player_explicit".into();
        lines[i].source_atom_ids.clear();
        lines[i].source_atoms.clear();
        lines
    }
    fn emission() -> Emission {
        crate::haiku_record::PreparedEmission {
            text: reading(&lines()),
            surface_text: None,
            reading_text: None,
            lines: lines(),
            materials: Default::default(),
            interpretation: None,
            preface: None,
            biome: None,
            structure: None,
            time_phase: None,
            dimension: None,
            event_sequence: Some(1),
            route: None,
        }
        .complete(chrono::Utc::now())
        .unwrap()
    }
    #[test]
    fn one_then_two_edits_keep_canonical_base() {
        let original = lines();
        let one = Pending::stage(
            &original,
            &original,
            replace(original.clone(), 0, "さくらいろ"),
            0,
        )
        .unwrap();
        let two = Pending::stage(
            &original,
            &one.lines,
            replace(one.lines.clone(), 2, "あさひかる"),
            2,
        )
        .unwrap();
        assert_eq!(two.edits().len(), 2);
        assert_eq!(two.base, original);
        assert_eq!(two.lines[1], original[1]);
        assert!(two.validate(&one.lines).is_err());
    }
    #[test]
    fn non_target_identity_and_source_changes_are_rejected() {
        let base = lines();
        let good = replace(base.clone(), 0, "さくらいろ");
        for kind in 0..4 {
            let mut bad = good.clone();
            match kind {
                0 => bad[1].surface_text = "変更".into(),
                1 => bad[0].line_id = "line_3".into(),
                2 => bad[0].source_atom_ids.push("fake".into()),
                _ => bad[0].provenance = "generated".into(),
            }
            assert!(Pending::stage(&base, &base, bad, 0).is_err());
        }
    }
    #[test]
    fn exact_meter_unresolved_readings_duplicates_and_no_op_fail() {
        let base = lines();
        for text in ["さくら", "さくらいろの", "桜色", "あさのいろ", "さくらのは"]
        {
            assert!(
                Pending::stage(&base, &base, replace(base.clone(), 0, text), 0).is_err(),
                "{text}"
            );
        }
    }
    #[test]
    fn revision_is_idempotent_and_requires_matching_parent() {
        let dir = std::env::temp_dir().join(format!("dogido-edit-{}", uuid::Uuid::new_v4()));
        let store = MemoryStore::new(&dir);
        let original = emission();
        let base = &original.prepared.lines;
        let first = Pending::stage(base, base, replace(base.clone(), 0, "さくらいろ"), 0).unwrap();
        let id = store.save_player_revision(&original, &first, None).unwrap();
        assert_eq!(
            store.save_player_revision(&original, &first, None).unwrap(),
            id
        );
        let second = Pending::stage(
            &first.lines,
            &first.lines,
            replace(first.lines.clone(), 2, "あさひかる"),
            2,
        )
        .unwrap();
        assert!(
            store
                .save_player_revision(&original, &second, None)
                .is_err()
        );
        assert!(
            store
                .save_player_revision(&original, &second, Some("wrong"))
                .is_err()
        );
        store
            .save_player_revision(&original, &second, Some(&id))
            .unwrap();
        let data = std::fs::read_to_string(dir.join("long_term/haiku_revisions.jsonl")).unwrap();
        assert_eq!(data.lines().count(), 2);
        let row: Value = serde_json::from_str(data.lines().last().unwrap()).unwrap();
        assert_eq!(row["original_reading_text"], reading(base));
        assert_eq!(row["base_text"], reading(&first.lines));
        assert_eq!(row["lines"][0]["source_atom_ids"], json!([]));
        let mut conflict = first.clone();
        conflict.lines = replace(base.clone(), 0, "はるのいろ");
        assert!(
            store
                .save_player_revision(&original, &conflict, None)
                .is_err()
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}
