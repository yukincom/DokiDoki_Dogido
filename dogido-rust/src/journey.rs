//! 「旅の記録」の保存正本。あらすじ・冒険の日付・配送された句への参照をJSONで保存する。
//! 生の対話や評価ログは保存しない。プレイヤーの記録メモは本棚の既存state.jsonが所有する。
use anyhow::{Result, ensure};
use chrono::{DateTime, Local, Utc};
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Synopsis {
    pub text: String,
    pub turns: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Poem {
    pub id: String,
    pub text: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Record {
    pub schema_version: u32,
    pub id: String,
    pub session_id: String,
    pub player: String,
    pub date: String,
    pub started_at: DateTime<Utc>,
    pub ended_at: Option<DateTime<Utc>>,
    pub synopses: Vec<Synopsis>,
    pub poems: Vec<Poem>,
    pub omitted_turns: usize,
    pub unfinished: bool,
}
impl Record {
    pub fn new(session_id: &str, player: &str) -> Self {
        let now = Local::now();
        Self {
            schema_version: 1,
            id: format!("journey_{}", uuid::Uuid::new_v4().simple()),
            session_id: session_id.into(),
            player: player.into(),
            date: now.format("%Y-%m-%d").to_string(),
            started_at: now.to_utc(),
            ended_at: None,
            synopses: vec![],
            poems: vec![],
            omitted_turns: 0,
            unfinished: false,
        }
    }
    /// 同じ句の音声完了と表示ACKが来ても、一つのリンクとして保持する。
    pub fn poem(&mut self, id: String, text: String) {
        if !self.poems.iter().any(|p| p.id == id) {
            self.poems.push(Poem { id, text });
        }
    }
    pub fn has_content(&self) -> bool {
        !self.synopses.is_empty()
            || !self.poems.is_empty()
            || self.omitted_turns > 0
            || self.unfinished
    }
}
fn valid_id(id: &str) -> bool {
    id.starts_with("journey_")
        && id.len() <= 80
        && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}
/// あらすじの確定済みsnapshotだけを同じIDへ原子的に置き換える。既存の個人メモには触れない。
pub fn save(root: &Path, record: &Record) -> Result<()> {
    ensure!(
        record.schema_version == 1 && valid_id(&record.id),
        "invalid_journey"
    );
    if !record.has_content() {
        return Ok(());
    }
    let parent = root.join("long_term");
    fs::create_dir_all(&parent)?;
    ensure!(
        !fs::symlink_metadata(&parent)?.file_type().is_symlink(),
        "unsafe_directory"
    );
    let dir = parent.join("journeys");
    fs::create_dir_all(&dir)?;
    ensure!(
        !fs::symlink_metadata(&dir)?.file_type().is_symlink(),
        "unsafe_directory"
    );
    crate::poem_trash::atomic(
        &dir.join(format!("{}.json", record.id)),
        &serde_json::to_vec_pretty(record)?,
    )
}
/// 本棚から既存の記録だけを読む。ファイル名とIDを照合し、日付の新しい順に返す。
pub fn load(root: &Path) -> Result<Vec<Record>> {
    let dir = root.join("long_term/journeys");
    for parent in [root.join("long_term"), dir.clone()] {
        if let Ok(meta) = fs::symlink_metadata(&parent) {
            ensure!(!meta.file_type().is_symlink(), "unsafe_directory");
        }
    }
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
        Err(e) => return Err(e.into()),
    };
    let mut records = Vec::new();
    for entry in entries {
        let entry = entry?;
        if !entry.file_type()?.is_file() || entry.path().extension().is_none_or(|e| e != "json") {
            continue;
        }
        let record: Record = serde_json::from_slice(&fs::read(entry.path())?)?;
        ensure!(
            record.schema_version == 1
                && valid_id(&record.id)
                && entry.file_name() == format!("{}.json", record.id).as_str(),
            "invalid_journey"
        );
        records.push(record);
    }
    records.sort_by(|a, b| {
        b.started_at
            .cmp(&a.started_at)
            .then_with(|| a.id.cmp(&b.id))
    });
    Ok(records)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn records_round_trip_without_raw_dialogue_and_replace_only_the_same_journey() {
        let temp =
            std::env::temp_dir().join(format!("dogido-journey-test-{}", uuid::Uuid::new_v4()));
        let mut record = Record::new("s", "プレイヤー");
        record.synopses.push(Synopsis {
            text: "村を訪ね、川沿いに拠点を作る相談をした。".into(),
            turns: 10,
        });
        record.poem("session:s:haiku1".into(), "川の句".into());
        record.poem("session:s:haiku1".into(), "川の句".into());
        save(&temp, &record).unwrap();
        record.ended_at = Some(Utc::now());
        save(&temp, &record).unwrap();
        let loaded = load(&temp).unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].synopses, record.synopses);
        assert_eq!(loaded[0].poems.len(), 1);
        assert!(loaded[0].ended_at.is_some());
        assert!(load(&temp.join("absent")).unwrap().is_empty());
        record.id = "../outside".into();
        assert!(save(&temp, &record).is_err());
        fs::remove_dir_all(&temp).unwrap();
    }
}
