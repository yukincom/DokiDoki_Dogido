//! 一枚の撮影に結び付く8×6比較結果。コード用の参照情報とモデル向けの説明を分ける。
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, VecDeque};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ComparisonStatus { NotCompared, Unchanged, Changed }
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Brightness { None, Lighter, Darker }
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ComparisonReference {
    pub capture_id: String,
    pub captured_at: String,
    pub sha256: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CellChange {
    pub row: u8,
    pub column: u8,
    pub local_change: bool,
    pub brightness: Brightness,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneComparison {
    pub status: ComparisonStatus,
    pub reference: Option<ComparisonReference>,
    /// 変化のあったマスだけ。行は上から1..6、列は左から1..8。
    pub cells: Vec<CellChange>,
}
impl SceneComparison {
    pub fn changed(&self) -> bool { self.status == ComparisonStatus::Changed }
    pub(super) fn validate(&self, captured_at: &str) -> Result<()> {
        ensure!(self.cells.len() <= 48, "chat_image_comparison_cells");
        ensure!(match self.status {
            ComparisonStatus::NotCompared => self.reference.is_none() && self.cells.is_empty(),
            ComparisonStatus::Unchanged => self.reference.is_some() && self.cells.is_empty(),
            ComparisonStatus::Changed => self.reference.is_some() && !self.cells.is_empty(),
        }, "chat_image_comparison_status");
        if let Some(reference) = &self.reference {
            let id = reference.capture_id.strip_prefix("view_")
                .and_then(|id| uuid::Uuid::parse_str(id).ok().filter(|uuid| uuid.to_string() == id));
            ensure!(id.is_some(), "chat_image_comparison_capture_id");
            ensure!(reference.sha256.len() == 64 && reference.sha256.bytes().all(|v| v.is_ascii_hexdigit()),
                "chat_image_comparison_sha256");
            let time = chrono::DateTime::parse_from_rfc3339(&reference.captured_at).ok();
            let current = chrono::DateTime::parse_from_rfc3339(captured_at).ok();
            ensure!(reference.captured_at.len() <= 64 && time.zip(current).is_some_and(|(old, now)| old < now),
                "chat_image_comparison_time");
        }
        let mut seen = BTreeSet::new();
        for cell in &self.cells {
            ensure!((1..=6).contains(&cell.row) && (1..=8).contains(&cell.column)
                && seen.insert((cell.row, cell.column)), "chat_image_comparison_cell_position");
            ensure!(cell.local_change || cell.brightness != Brightness::None, "chat_image_comparison_empty_cell");
        }
        Ok(())
    }
    /// 既存Attachmentから一度だけ使う。差分の強度・割合や内部の参照IDは説明へ混ぜない。
    pub fn description(&self) -> String {
        match self.status {
            ComparisonStatus::NotCompared => return "画像は取得したが、比較できていない".into(),
            ComparisonStatus::Unchanged => return "変化がない".into(),
            ComparisonStatus::Changed => {}
        }
        let mut lines = vec!["画像を横8×縦6に分けた位置。行は上から、列は左から数える。".to_owned()];
        // (row,column)順に探索し、斜めだけの接触は結合しない。
        let mut remaining: BTreeSet<(u8,u8)> = self.cells.iter().map(|c| (c.row,c.column)).collect();
        while let Some(start) = remaining.pop_first() {
            let mut queue = VecDeque::from([start]);
            let mut component = BTreeSet::from([start]);
            while let Some((row,column)) = queue.pop_front() {
                for next in [(row.saturating_sub(1),column),(row+1,column),(row,column.saturating_sub(1)),(row,column+1)] {
                    if remaining.remove(&next) { queue.push_back(next); component.insert(next); }
                }
            }
            lines.push(location(&component));
        }
        let lighter = self.cells.iter().any(|c| c.brightness == Brightness::Lighter);
        let darker = self.cells.iter().any(|c| c.brightness == Brightness::Darker);
        lines.push(match (lighter, darker) {
            (false,false) => "画像の明るさの変化は検出していない。",
            (true,false) => "画像の明るさに変化がある。明るくなった領域を検出した。",
            (false,true) => "画像の明るさに変化がある。暗くなった領域を検出した。",
            (true,true) => "画像の明るさに変化がある。明るくなった領域と暗くなった領域をそれぞれ検出した。",
        }.into());
        lines.join("\n")
    }
}
fn location(component: &BTreeSet<(u8,u8)>) -> String {
    let mut horizontal = 0u8;
    let mut vertical = 0u8;
    for &(row,column) in component {
        horizontal |= if column <= 3 {1} else if column <= 5 {2} else {4};
        vertical |= if row <= 2 {1} else if row <= 4 {2} else {4};
    }
    let h = match horizontal {1=>"左側",2=>"中央",4=>"右側",3=>"左側から中央",6=>"中央から右側",_=>"左右全体"};
    let v = match vertical {1=>"上部",2=>"中央部",4=>"下部",3=>"上部から中央部",6=>"中央部から下部",_=>"上部から下部"};
    let mut rows = Vec::new();
    for row in 1..=6 {
        let columns: Vec<u8> = component.iter().filter_map(|&(r,c)| (r==row).then_some(c)).collect();
        if columns.is_empty() { continue; }
        let mut spans = Vec::new();
        let mut index = 0;
        while index < columns.len() {
            let first = columns[index];
            let mut last = first;
            index += 1;
            while index < columns.len() && columns[index] == last+1 { last=columns[index]; index+=1; }
            spans.push(if first == last {first.to_string()} else {format!("{first}〜{last}")});
        }
        rows.push(format!("上から{row}行目は左から{}列目",spans.join("・")));
    }
    format!("画面の{h}、{v}にある範囲。{}。",rows.join("、"))
}
