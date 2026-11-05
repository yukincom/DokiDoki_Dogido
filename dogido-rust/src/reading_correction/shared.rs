//! 親のJSONLから公開項目だけを取り出し、子機の読取専用スナップショットを検証する。
//! 配送と原子的な置換はLAN側が担当し、Rustは存在する共有印を優先して保存を禁止する。
use super::kana;
use crate::haiku_record::MemoryStore;
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::BTreeSet, fs::File, io::Read, path::PathBuf};

pub const MAX_SHARED_READINGS_BYTES: usize = 1 << 20;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    schema_version: u64,
    host_pin: Option<String>,
    corrections: Vec<SharedCorrection>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SharedCorrection {
    id: String,
    surface: String,
    reading: String,
    wrong_reading: Option<String>,
    forbidden_readings: Vec<String>,
    source: Option<String>,
}

fn text(text: &str, limit: usize) -> bool {
    !text.is_empty()
        && text.trim() == text
        && text.chars().count() <= limit
        && !text.chars().any(char::is_control)
}

fn reading(value: &str) -> bool {
    kana(value) && value.chars().count() <= 80
}

impl SharedCorrection {
    fn validate(&self) -> Result<()> {
        ensure!(text(&self.id, 512), "invalid_reading_id");
        ensure!(text(&self.surface, 80), "invalid_surface");
        ensure!(reading(&self.reading), "invalid_reading");
        ensure!(
            self.wrong_reading
                .as_deref()
                .is_none_or(|value| reading(value) && value != self.reading),
            "invalid_wrong_reading"
        );
        let mut seen = BTreeSet::new();
        ensure!(
            self.forbidden_readings
                .iter()
                .all(|value| { reading(value) && value != &self.reading && seen.insert(value) }),
            "invalid_forbidden_readings"
        );
        ensure!(
            self.source.as_deref().is_none_or(|value| text(value, 512)),
            "invalid_reading_source"
        );
        Ok(())
    }
}

/// 配布元・子機共通の外形検査。公開項目以外を受け入れず、表記ごとの重複も拒否する。
/// host_pinは配布元出力では省略し、子機ファイルでは読込側が必須とする。
pub fn validate_reading_snapshot(value: &Value) -> Result<Vec<Value>> {
    ensure!(
        serde_json::to_vec(value)?.len() <= MAX_SHARED_READINGS_BYTES,
        "reading_snapshot_too_large"
    );
    let snapshot: Snapshot = serde_json::from_value(value.clone())?;
    ensure!(snapshot.schema_version == 1, "invalid_reading_schema");
    if value.get("host_pin").is_some() {
        ensure!(
            snapshot.host_pin.as_deref().is_some_and(|pin| {
                pin.len() == 64
                    && pin
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            }),
            "invalid_reading_host_pin"
        );
    }
    let mut seen = BTreeSet::new();
    let mut rows = Vec::with_capacity(snapshot.corrections.len());
    for row in snapshot.corrections {
        row.validate()?;
        ensure!(
            seen.insert(row.surface.clone()),
            "duplicate_reading_surface"
        );
        rows.push(serde_json::to_value(row)?);
    }
    Ok(rows)
}

/// JSONLの監査項目は外へ出さず、以前の行で省略されていた任意項目だけ補完する。
pub(super) fn project_correction(row: &Value) -> Result<Value> {
    ensure!(row.is_object(), "invalid_reading_row");
    let projected = json!({
        "id": row.get("id"),
        "surface": row.get("surface"),
        "reading": row.get("reading"),
        "wrong_reading": row.get("wrong_reading"),
        "forbidden_readings": row.get("forbidden_readings").cloned().unwrap_or_else(|| json!([])),
        "source": row.get("source"),
    });
    let correction: SharedCorrection = serde_json::from_value(projected)?;
    correction.validate()?;
    Ok(serde_json::to_value(correction)?)
}

pub(super) fn validate_removal(row: &Value) -> Result<()> {
    ensure!(
        row["surface"].as_str().is_some_and(|value| text(value, 80))
            && row["id"].as_str().is_some_and(|value| text(value, 512))
            && row["previous_id"]
                .as_str()
                .is_some_and(|value| text(value, 512)),
        "invalid_reading_removal"
    );
    Ok(())
}

impl MemoryStore {
    pub fn shared_readings_path(&self) -> PathBuf {
        self.root().join("long_term/shared_readings.json")
    }

    /// 壊れたファイル・リンク切れ・権限不明でも子機の辞書を更新可能にしない。
    pub fn readings_writable(&self) -> bool {
        matches!(std::fs::symlink_metadata(self.shared_readings_path()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound)
    }

    pub(super) fn shared_reading_corrections(&self) -> Result<Vec<Value>> {
        let file = File::open(self.shared_readings_path())?;
        let mut bytes = Vec::new();
        file.take((MAX_SHARED_READINGS_BYTES + 1) as u64)
            .read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() <= MAX_SHARED_READINGS_BYTES,
            "reading_snapshot_too_large"
        );
        let value: Value = serde_json::from_slice(&bytes)?;
        ensure!(value.get("host_pin").is_some(), "invalid_reading_host_pin");
        validate_reading_snapshot(&value)
    }

    /// 既存の親保存先だけから書き出す。誤った保存先や破損を空の配布辞書へ変換しない。
    pub fn export_readings(&self) -> Result<Value> {
        crate::poem_trash::verify_primary_volume(self.root())?;
        ensure!(self.root().metadata()?.is_dir(), "invalid_reading_root");
        ensure!(self.readings_writable(), "reading_read_only");
        let snapshot =
            json!({"schema_version":1,"corrections":self.local_reading_corrections(true)?});
        validate_reading_snapshot(&snapshot)?;
        // CLIが付ける末尾改行も1 MiBの送信上限へ含める。
        ensure!(
            serde_json::to_vec(&snapshot)?.len() < MAX_SHARED_READINGS_BYTES,
            "reading_snapshot_too_large"
        );
        Ok(snapshot)
    }
}

#[cfg(test)]
mod tests;
