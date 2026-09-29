//! Player-authored whole poems and explicit resaves. No model may invent a
//! payload or promote a pending edit through this path.
use crate::{
    events::GameEvent,
    haiku_record::{Emission, HaikuLine, MemoryStore, SAVE_LOCK, append_line, locked_file},
    workshop_edit,
};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Input {
    Invalid,
    Player { text: String },
    Revision { text: String, source: String },
    SaveLast,
}

/// Keep the explicit legacy archive form (including a single unsegmented line),
/// but never silently truncate a fourth line. Natural requests need three lines.
pub fn payload(raw: &str) -> Option<String> {
    let text = raw.replace('　', " ");
    let text = text.trim();
    if text.is_empty() {
        return None;
    }
    let separator = ['\n', '/', '／', '|', '｜']
        .into_iter()
        .find(|c| text.contains(*c));
    let pieces = match separator {
        Some(c) => text
            .split(c)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>(),
        None => vec![text],
    };
    if pieces.is_empty() || pieces.len() > 3 {
        return None;
    }
    Some(if separator.is_some() {
        pieces.join("\n")
    } else {
        text.split_whitespace().collect::<Vec<_>>().join(" ")
    })
}

pub fn surfaces(text: &str) -> Vec<&str> {
    let lines = text
        .lines()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>();
    if lines.len() == 1 {
        let pieces = text.split_whitespace().collect::<Vec<_>>();
        if pieces.len() == 3 {
            return pieces;
        }
    }
    lines
}

pub fn parse(raw: &str, workshop_open: bool) -> Option<Input> {
    let text = raw.trim();
    for prefix in [
        "直し:",
        "直し：",
        "川柳直し:",
        "川柳直し：",
        "句直し:",
        "句直し：",
    ] {
        if let Some(p) = text.strip_prefix(prefix) {
            return Some(payload(p).map_or(Input::Invalid, |text| Input::Revision {
                text,
                source: "formal".into(),
            }));
        }
    }
    for prefix in ["川柳保存:", "川柳保存：", "川柳:", "川柳："] {
        if let Some(p) = text.strip_prefix(prefix) {
            return Some(payload(p).map_or(Input::Invalid, |text| Input::Player { text }));
        }
    }
    if workshop_open {
        for prefix in [
            "こう直して:",
            "こう直して：",
            "こう直す:",
            "こう直す：",
            "直して:",
            "直して：",
            "直しは:",
            "直しは：",
            "この方がええ:",
            "この方がええ：",
            "このほうがいい:",
            "このほうがいい：",
            "こう直して",
            "こう直す",
            "直しは",
        ] {
            if let Some(p) = text.strip_prefix(prefix) {
                // Without punctuation, require a boundary before the supplied
                // poem. "こう直してと言った" is reported speech, not a save.
                if !prefix.ends_with([':', '：']) && !p.starts_with([' ', '　', '\n', '、']) {
                    continue;
                }
                let p = p.trim_start_matches([' ', '　', '\n', '、']);
                let p = payload(p)?;
                return (surfaces(&p).len() == 3).then_some(Input::Revision {
                    text: p,
                    source: "conversational".into(),
                });
            }
        }
    }
    let text = text.trim_end_matches(['。', '！', '!']);
    for prefix in [
        "今の川柳",
        "いまの川柳",
        "さっきの川柳",
        "今の句",
        "いまの句",
        "さっきの句",
    ] {
        if let Some(rest) = text.strip_prefix(prefix) {
            let rest = rest.trim_start_matches(['、', ',', ' ']);
            if [
                "保存",
                "保存して",
                "を保存",
                "を保存して",
                "を保存してね",
                "を保存してな",
                "を保存してほしい",
                "を保存してください",
                "を保存して下さい",
                "保存してね",
                "保存してください",
            ]
            .contains(&rest)
            {
                return Some(Input::SaveLast);
            }
        }
    }
    None
}

pub fn validate_lines(text: &str, source: &str, lines: &[HaikuLine]) -> Result<()> {
    if lines.is_empty() {
        return Ok(());
    } // Legacy archive keeps unresolved text.
    let expected = surfaces(text);
    ensure!(
        lines.len() == 3 && expected.len() == 3,
        "invalid_whole_verse_lines"
    );
    for (i, l) in lines.iter().enumerate() {
        ensure!(
            crate::haiku_record::LinePosition::ALL[i].matches(l)
                && l.surface_text == expected[i]
                && l.provenance == source
                && !l.reading_text.is_empty()
                && l.reading_text
                    .chars()
                    .all(|c| ('ぁ'..='ゖ').contains(&c) || c == 'ー')
                && l.source_atom_ids.is_empty()
                && l.source_atoms.is_empty(),
            "whole_verse_line_mismatch"
        );
    }
    Ok(())
}

impl MemoryStore {
    pub fn save_authored_poem(
        &self,
        operation_id: &str,
        event: &GameEvent,
        text: &str,
    ) -> Result<bool> {
        ensure!(!text.trim().is_empty(), "empty_player_poem");
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let e = serde_json::to_value(event)?;
        // A player-input turn can share its last game observation with another
        // turn. Use its stable operation ID to avoid a same-sequence collision.
        let entry = json!({"id":format!("hk_{operation_id}_player"),"created_at":e["observed_at"],
            "author":"player","kind":"player_haiku","text":text,"preface":null,"interpretation":null,
            "world":{"biome":e["world"]["biome"],"structure":e["world"]["structure"],"time_phase":e["world"]["time_phase"],"dimension":e["player"]["dimension"]},
            "trigger":{"event_sequence":e["sequence"],"route":null}});
        let mut file = locked_file(&self.entries_path())?;
        for line in BufReader::new(&file).lines() {
            if let Ok(row) = serde_json::from_str::<Value>(&line?)
                && row["id"] == entry["id"]
            {
                ensure!(row == entry, "player_poem_id_conflict");
                return Ok(false);
            }
        }
        append_line(&mut file, &entry)?;
        Ok(true)
    }

    pub fn save_whole_revision(&self, revision: &WholeRevision) -> Result<()> {
        ensure!(
            ["formal", "conversational"].contains(&revision.source.as_str()),
            "invalid_revision_source"
        );
        ensure!(!revision.text.trim().is_empty(), "empty_revision");
        validate_lines(&revision.text, &revision.source, &revision.lines)?;
        let _guard = SAVE_LOCK
            .lock()
            .map_err(|_| anyhow::anyhow!("haiku save mutex poisoned"))?;
        let mut file = locked_file(&self.root().join("long_term/haiku_revisions.jsonl"))?;
        let mut rows = vec![];
        for line in BufReader::new(&file).lines() {
            if let Ok(r) = serde_json::from_str::<Value>(&line?) {
                rows.push(r);
            }
        }
        if let Some(old) = rows.iter().find(|r| r["id"] == revision.id) {
            ensure!(*old == revision.record(), "whole_revision_id_conflict");
            return Ok(());
        }
        if let Some(parent) = &revision.parent {
            let p = rows
                .iter()
                .filter(|r| r["id"] == *parent)
                .collect::<Vec<_>>();
            ensure!(
                p.len() == 1
                    && p[0]["haiku_id"] == revision.original.entry_id()
                    && p[0]["revised_text"] == workshop_edit::reading(&revision.base),
                "parent_revision_mismatch"
            );
        } else {
            ensure!(
                revision.base == revision.original.prepared.lines,
                "parent_revision_required"
            );
        }
        append_line(&mut file, &revision.record())
    }
}

#[derive(Clone)]
pub struct WholeRevision {
    pub id: String,
    pub at: chrono::DateTime<chrono::Utc>,
    pub original: Emission,
    pub base: Vec<HaikuLine>,
    pub parent: Option<String>,
    pub text: String,
    pub source: String,
    pub lines: Vec<HaikuLine>,
}
impl WholeRevision {
    pub fn record(&self) -> Value {
        let e = &self.original.prepared;
        json!({"id":self.id,"created_at":self.at,"haiku_id":self.original.entry_id(),"source":self.source,"comment":null,
            "original_text":e.surface_text.as_deref().unwrap_or(&e.text),"original_reading_text":e.reading_text.as_deref().unwrap_or(&e.text),
            "base_text":workshop_edit::reading(&self.base),"base_surface_text":workshop_edit::surface(&self.base),"parent_revision_id":self.parent,
            "revised_text":if self.lines.is_empty(){self.text.clone()}else{workshop_edit::reading(&self.lines)},
            "revised_surface_text":if self.lines.is_empty(){self.text.clone()}else{workshop_edit::surface(&self.lines)},
            "lines":if self.lines.is_empty(){Value::Null}else{json!(self.lines)},"line_sources":null,
            "world":{"biome":e.biome,"structure":e.structure,"time_phase":e.time_phase,"dimension":e.dimension}})
    }
}
