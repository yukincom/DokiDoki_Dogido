//! フォームの読み編集を受け取り、不変な入力と静的カタログを一度検証する。
//! 検証済みの値は書き換えず保存へ渡す。最新の保存版照合と追記はMemoryStoreが担当する。
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
/// 形式検証済みの編集。外部から生成・書換えできず、未検証入力を保存へ渡せない。
#[derive(Debug)]
pub struct ValidatedEdit(Edit);
impl ValidatedEdit {
    pub(crate) fn input(&self) -> &Edit {
        &self.0
    }
}

impl Edit {
    /// 入力を消費し、表記・読み・任意カタログIDを検証する。保存ファイルは読まない。
    pub fn validate(self) -> Result<ValidatedEdit> {
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
        Ok(ValidatedEdit(self))
    }
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Remove {
    pub surface: String,
    pub expected_id: String,
}
/// 形式検証済みの取消。expected_idの現在性は保存ロック取得後に照合する。
#[derive(Debug)]
pub struct ValidatedRemove(Remove);
impl ValidatedRemove {
    pub(crate) fn input(&self) -> &Remove {
        &self.0
    }
}
impl Remove {
    /// 表記の形式を検証して値を固定する。現在の登録IDはここでは読み込まない。
    pub fn validate(self) -> Result<ValidatedRemove> {
        validate_surface(&self.surface)?;
        Ok(ValidatedRemove(self))
    }
}
fn validate_surface(surface: &str) -> Result<()> {
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
