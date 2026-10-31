//! Native toolの解釈済み引数を、既存のplayer_edit入力へ渡す。
//! 三行のexpected_textは全行を照合してから対象行へ絞る。保存・状態変更はしない。
use super::{Snapshot, editing_records, parse};
use crate::{haiku_record::HaikuLine, workshop_projection, workshop_response};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
struct Arguments {
    line_id: String,
    expected_text: String,
    #[serde(default)]
    target_fragment: String,
    replacement_text: String,
    #[serde(default)]
    evidence: Value,
    #[serde(default)]
    confidence: Value,
    #[serde(default)]
    line_reference_evidence: String,
}

// 行名は本文とは別のメタデータ。モデルが見出し形式で返しても、
// 既知の対象に対応する先頭のラベルだけを外す。本文中の語句は再解釈しない。
fn replacement_body(text: &str, index: usize) -> &str {
    let body = parse::unquote(text);
    for label in [
        ["上の句", "中の句", "下の句"][index],
        ["line_1", "line_2", "line_3"][index],
    ] {
        if let Some(rest) = body.strip_prefix(label) {
            if let Some(rest) = rest.trim_start().strip_prefix([':', '：', '=']) {
                let rest = rest.trim();
                if !rest.is_empty() {
                    return rest;
                }
            }
        }
    }
    text
}

fn matches_line(text: &str, line: &HaikuLine) -> bool {
    text == line.reading_text || text == line.surface_text
}

fn tool_lines(expected: &str) -> Result<Vec<String>> {
    let expected = expected.trim();
    if !expected.starts_with("line_") {
        return Ok(parse::verse_lines(expected));
    }
    // Accept the actual Gemma response's ordered labels, commas and final 。.
    // Never search out three plausible lines from surrounding prose or reorder IDs.
    let parts: Vec<_> = expected
        .strip_suffix('。')
        .unwrap_or(expected)
        .split([',', '、', '\n'])
        .collect();
    ensure!(parts.len() == 3, "invalid_expected_verse");
    parts
        .iter()
        .enumerate()
        .map(|(i, part)| {
            let prefix = format!("line_{}=", i + 1);
            Ok(part
                .trim()
                .strip_prefix(&prefix)
                .context("invalid_expected_verse")?
                .trim()
                .to_owned())
        })
        .collect()
}

/// 呼び手が渡す現在の句・未採用案に、stage_player_editの対象と元句を照合する。
/// 置換語と旧語はモデルの解釈を保持し、原文との文字列一致や再抽出で変更しない。
/// 現在句と原文はコード側の値を使う。返り値は行差分を組み立てる入力であり、
/// 採用・保存は行わない。保存時の元句一致確認は既存Pendingの担当。
pub fn normalize_player_edit_tool(
    workshop: &Value,
    original: &str,
    arguments: &Value,
) -> Result<Value> {
    ensure!(
        serde_json::to_vec(arguments)?.len() < 16_384,
        "tool_arguments_too_large"
    );
    let mut args: Arguments =
        serde_json::from_value(arguments.clone()).context("invalid_tool_arguments")?;
    let selected = ["line_1", "line_2", "line_3"]
        .iter()
        .position(|id| *id == args.line_id)
        .context("invalid_line_id")?;
    let snapshot = Snapshot::from_view(workshop)?;
    let working = editing_records(&snapshot);
    ensure!(working.len() == 3, "invalid_line_records");
    for (i, line) in working.iter().enumerate() {
        ensure!(
            line.line_index == i && line.line_id == format!("line_{}", i + 1),
            "invalid_line_records"
        );
    }
    let expected = tool_lines(&args.expected_text)?;
    ensure!(
        match expected.as_slice() {
            [one] => matches_line(one, &working[selected]),
            [_, _, _] => expected
                .iter()
                .zip(working)
                .all(|(text, line)| matches_line(text, line)),
            _ => false,
        },
        "stale_tool_edit"
    );

    // A full replacement verse must differ in exactly the declared line. Keep
    // only that line; never silently drop additional model changes.
    let single_body = replacement_body(&args.replacement_text, selected);
    let labelled_whole = args
        .replacement_text
        .trim()
        .starts_with("line_")
        .then(|| tool_lines(&args.replacement_text).ok())
        .flatten();
    let replacement = if let Some(lines) = labelled_whole {
        lines
    } else if !single_body.contains(['\n', '\r']) && single_body != args.replacement_text {
        vec![single_body.to_owned()]
    } else if args.replacement_text.trim().starts_with("line_") {
        tool_lines(&args.replacement_text)?
    } else {
        args.replacement_text
            .lines()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>()
    };
    if replacement.len() == 3 {
        let replacement: Vec<_> = replacement
            .iter()
            .enumerate()
            .map(|(i, text)| replacement_body(text, i).to_owned())
            .collect();
        let changed: Vec<_> = replacement
            .iter()
            .zip(working)
            .enumerate()
            .filter_map(|(i, (text, line))| (!matches_line(text, line)).then_some(i))
            .collect();
        ensure!(
            changed.as_slice() == [selected],
            "tool_replacement_target_conflict"
        );
        ensure!(
            args.target_fragment.is_empty()
                || working[selected]
                    .reading_text
                    .contains(&args.target_fragment)
                || working[selected]
                    .surface_text
                    .contains(&args.target_fragment),
            "target_conflict"
        );
        args.replacement_text = replacement[selected].clone();
        args.target_fragment = working[selected].surface_text.clone();
    } else {
        ensure!(replacement.len() == 1, "invalid_replacement_verse");
        args.replacement_text = replacement_body(&replacement[0], selected).to_owned();
    }

    // 発話原文は記録として保持する。語句とevidenceの解釈はモデルに任せ、
    // 認識原文との一致や原文の再解析で拒否・上書きしない。
    ensure!(
        !args.replacement_text.trim().is_empty(),
        "invalid_tool_replacement"
    );
    let mut frame = json!({
        "op":"player_edit", "workshop":workshop, "text":original,
        "phase":"decide", "allowed_actions":["stage_player_edit"],
        "payload":{
            "action":"stage_player_edit", "purpose":"improve_wording", "speech":"",
            "confidence":args.confidence, "evidence":args.evidence,
            "line_proposal":{"found":true,"target_fragment":args.target_fragment,
                "replacement_text":args.replacement_text,"evidence":args.evidence,
                "confidence":args.confidence}
        }
    });
    // 行概念は解釈済みの番号を使う。根拠文字列との一致で行を変えない。
    if !args.line_reference_evidence.is_empty() {
        frame["payload"]["line_reference"] = json!({
            "found":true,"concept_id":args.line_id,
            "evidence":args.line_reference_evidence,"confidence":args.confidence});
    }
    let details = workshop_projection::details_for(&frame)?;
    let decoded = workshop_response::decode(&frame, &details)?;
    if !args.line_reference_evidence.is_empty() {
        ensure!(
            decoded["step"]["analysis"]["line_reference"]["line_index"] == selected,
            "tool_line_reference_rejected"
        );
    }
    ensure!(
        decoded["step"]["action"] == "stage_player_edit",
        "{}",
        decoded["reason"].as_str().unwrap_or("tool_edit_rejected")
    );
    let mut proposal = decoded["step"]["analysis"]["line_proposal"].clone();
    ensure!(proposal.is_object(), "line_proposal_required");
    ensure!(
        proposal["line_index"].is_null()
            || proposal["line_index"].as_u64() == Some(selected as u64),
        "target_conflict"
    );
    proposal["line_index"] = selected.into();
    frame["proposal"] = proposal;
    // Keep the normalized expected line in the diagnostic record. The frame's
    // snapshot remains the full CAS basis; Engine computes only changed lines.
    frame["expected_text"] = working[selected].reading_text.clone().into();
    Ok(frame)
}

/// 対象と元句は共通分類と現在snapshotから補完する。LLMの変更後の一行は変更しない。
/// 具体変更は既存Engineの一行置換へ渡し、旧語の部分一致による置換を行わない。
pub fn fill_classified_edit_target(workshop: &Value, index: usize, args: &mut Value) -> Result<()> {
    let snapshot = Snapshot::from_view(workshop)?;
    let lines = editing_records(&snapshot);
    ensure!(
        lines.len() == 3 && index < lines.len(),
        "invalid_classified_target"
    );
    let args = args.as_object_mut().context("invalid_tool_arguments")?;
    args.insert("line_id".into(), lines[index].line_id.clone().into());
    args.insert(
        "expected_text".into(),
        lines[index].surface_text.clone().into(),
    );
    args.remove("line_reference_evidence");
    // replacement_textは完成した一行。旧形式の旧語が付いていても部分置換へ戻さない。
    args.insert("target_fragment".into(), String::new().into());
    Ok(())
}

#[cfg(test)]
mod tests;
