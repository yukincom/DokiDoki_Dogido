//! 本棚の明示した三行を、同じ保存元の現在版へ追記する入力口。
//! 本文は手入力のまま保持し、元句・親版・再送IDを共通writerで再照合する。
//! LLM、読み辞書、品質検査、個人メモstate、稼働中のworkshop状態は変更しない。
use super::store::{self, Store};
use crate::{
    haiku_record::{HaikuLine, LinePosition, MemoryStore, ResolvedLines, verse::record_surfaces},
    poem_trash,
    workshop_edit::{editing_text, reading, surface},
};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};

/// idは本棚の出所付き句ID。expected系は編集開始時、linesは保存する三行。
/// requestIdは操作ごとのUUIDで、応答不明時にも同じ値・入力を再送する。
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Edit {
    pub id: String,
    pub request_id: String,
    pub expected_revision_id: Option<String>,
    pub expected_lines: Vec<String>,
    pub lines: [String; 3],
}

/// 保存行があれば未変更行をそのまま残し、変更行だけ本人の入力へ置き換える。
/// 旧text-only句は三行の表記を作るが、元記録は書き換えず読みも補作しない。
fn edited_lines(base: &Value, supplied: &[String; 3]) -> Result<Vec<HaikuLine>> {
    let previous = if base["lines"].is_null() || base["lines"].as_array().is_some_and(Vec::is_empty)
    {
        None
    } else {
        let lines: Vec<HaikuLine> = serde_json::from_value(base["lines"].clone())?;
        ResolvedLines::try_from(lines.as_slice())?;
        Some(lines)
    };
    Ok(supplied
        .iter()
        .enumerate()
        .map(|(i, text)| {
            if let Some(line) = previous
                .as_ref()
                .and_then(|lines| lines.get(i))
                .filter(|line| line.surface_text == *text)
            {
                return line.clone();
            }
            let (line_id, position, canonical_name) = LinePosition::ALL[i].metadata();
            HaikuLine {
                line_id: line_id.into(),
                line_index: i,
                position: position.into(),
                canonical_name: canonical_name.into(),
                surface_text: text.clone(),
                reading_text: String::new(),
                source_atom_ids: vec![],
                source_atoms: vec![],
                provenance: "player_explicit".into(),
            }
        })
        .collect())
}

/// 明示入力と開始版を照合し、共通writerへ追記を依頼して本棚の現在投影を返す。
/// 初回読取のロックは追記前に離し、競合はwriterの再照合で拒否する。
pub(super) fn save(store: &Store, edit: &Edit) -> Result<Value> {
    ensure!(
        edit.lines
            .iter()
            .all(|line| !line.trim().is_empty() && !line.contains(['\n', '\r'])),
        "invalid_poem_lines"
    );
    let operation = uuid::Uuid::parse_str(&edit.request_id).context("invalid_request_id")?;
    let revision_id = format!("hr_library_{}", operation.simple());
    let (root, entry_id) = poem_trash::resolve(&store.memory, &edit.id)?;
    // 追記・期限整理の途中を読まない。共通writerを呼ぶ前にこの読取lockは離す。
    let snapshot_lock = poem_trash::lock(&root)?;
    let originals = poem_trash::raw_rows(&root, "haiku_entries.jsonl")?;
    let originals: Vec<_> = originals
        .iter()
        .filter(|row| row["id"] == entry_id)
        .collect();
    ensure!(originals.len() == 1, "unknown_poem");
    let original = originals[0];
    let revisions = poem_trash::raw_rows(&root, "haiku_revisions.jsonl")?;
    drop(snapshot_lock);
    // 再送時にも最初のbaseから同じ記録を構築する。最新版をbaseへすり替えない。
    let base = if let Some(parent) = &edit.expected_revision_id {
        let rows: Vec<_> = revisions
            .iter()
            .filter(|row| row["id"] == *parent && row["haiku_id"] == entry_id)
            .collect();
        ensure!(rows.len() == 1, "saved_poem_changed");
        rows[0]
    } else {
        original
    };
    ensure!(
        record_surfaces(base) == edit.expected_lines,
        "saved_poem_changed"
    );
    ensure!(
        edit.expected_lines.as_slice() != edit.lines.as_slice(),
        "no_change"
    );
    let lines = edited_lines(base, &edit.lines)?;
    let base_text = if base["lines"]
        .as_array()
        .is_some_and(|lines| !lines.is_empty())
    {
        editing_text(&serde_json::from_value::<Vec<HaikuLine>>(
            base["lines"].clone(),
        )?)
    } else {
        edit.expected_lines.join("\n")
    };
    let original_reading = if original["lines"].is_array() {
        json!(reading(&serde_json::from_value::<Vec<HaikuLine>>(
            original["lines"].clone()
        )?))
    } else {
        original["reading_text"].clone()
    };
    let record = json!({
        "id":revision_id,"created_at":chrono::Utc::now(),"haiku_id":entry_id,
        "source":"bookshelf_explicit","comment":null,
        "original_text":record_surfaces(original).join("\n"),"original_reading_text":original_reading,
        "base_text":base_text,"base_surface_text":edit.expected_lines.join("\n"),
        "parent_revision_id":edit.expected_revision_id,
        "revised_text":editing_text(&lines),"revised_surface_text":surface(&lines),"lines":lines,
        "line_sources":null,"edit_contract":"bookshelf_current_compare_and_swap_v1",
        "world":original["world"],
    });
    MemoryStore::new(&root).save_current_revision_record(original, base, &record)?;
    saved_projection(store, &root, &edit.id, &revision_id)
}

/// 保存と返却の間に別writerが進めた版を、今回の保存結果として返さない。
/// 対象rootの句ロック内で投影と版を照合し、食い違いは入力を保持できる競合へ戻す。
pub(super) fn saved_projection(
    store: &Store,
    root: &std::path::Path,
    id: &str,
    revision_id: &str,
) -> Result<Value> {
    let _snapshot_lock = poem_trash::lock(root)?;
    let poem = store::poems(&store.memory, false)?
        .into_iter()
        .find(|poem| poem["id"] == id)
        .context("poem_deleted")?;
    ensure!(poem["current"]["id"] == revision_id, "saved_poem_changed");
    Ok(json!({"poem":poem}))
}
