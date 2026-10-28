//! Native toolの解釈済み引数を、既存のplayer_edit入力へ渡す。
//! 三行のexpected_textは全行を照合してから対象行へ絞る。保存・状態変更はしない。
use super::{Snapshot, editing_records, parse};
use crate::{haiku_record::HaikuLine, workshop_projection, workshop_validation};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Arguments {
    line_id: String,
    expected_text: String,
    target_fragment: String,
    replacement_text: String,
    evidence: String,
    confidence: f64,
    #[serde(default)]
    line_reference_evidence: String,
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
    let replacement = tool_lines(&args.replacement_text)?;
    if replacement.len() == 3 {
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
    }

    // 発話原文は記録として保持する。語句とevidenceの解釈はモデルに任せ、
    // 認識原文との一致や原文の再解析で拒否・上書きしない。
    ensure!(
        args.evidence.chars().count() <= 120,
        "tool_evidence_too_long"
    );
    ensure!(
        !args.replacement_text.trim().is_empty() && args.replacement_text.chars().count() <= 48,
        "invalid_tool_replacement"
    );
    ensure!(
        args.target_fragment.chars().count() <= 48,
        "tool_fragment_too_long"
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
    // Preserve the existing semantic line-reference path for player wording
    // such as 冒頭の五音. A line ID alone cannot move a retained target.
    if !args.line_reference_evidence.is_empty() {
        ensure!(
            args.line_reference_evidence.chars().count() <= 48,
            "tool_line_reference_rejected"
        );
        frame["payload"]["line_reference"] = json!({
            "found":true,"concept_id":args.line_id,
            "evidence":args.line_reference_evidence,"confidence":args.confidence});
    }
    let details = workshop_projection::details_for(&frame)?;
    let validation = workshop_validation::validate(&frame, &details)?;
    if !args.line_reference_evidence.is_empty() {
        ensure!(
            validation["step"]["analysis"]["line_reference"]["line_index"] == selected,
            "tool_line_reference_rejected"
        );
    }
    ensure!(
        validation["step"]["action"] == "stage_player_edit",
        "{}",
        validation["reason"]
            .as_str()
            .unwrap_or("tool_edit_rejected")
    );
    let mut proposal = validation["step"]["analysis"]["line_proposal"].clone();
    ensure!(proposal.is_object(), "grounded_line_proposal_required");
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

/// 対象と元句は共通分類と現在snapshotから補完する。解釈済みの語句は変更しない。
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
    Ok(())
}

/// 既存のA/B抽出が一意に成立するときだけ、生成を挟まず同じ編集検証へ渡す。
pub fn verbatim_player_edit(
    workshop: &Value,
    original: &str,
    index: usize,
) -> Result<Option<Value>> {
    // 直接編集では発話全体を根拠にする。長い発話や一意でない形は既存の限定抽出へ渡す。
    if original.chars().count() > 120 {
        return Ok(None);
    }
    let Some(verbatim) = parse::verbatim_edit(original).ok().flatten() else {
        return Ok(None);
    };
    let snapshot = Snapshot::from_view(workshop)?;
    let lines = editing_records(&snapshot);
    ensure!(
        lines.len() == 3 && index < lines.len(),
        "invalid_classified_target"
    );
    let fragment = parse::fragment_prefix(verbatim.target);
    let fragment = parse::unquote(&fragment);
    let references = parse::explicit_lines(verbatim.target);
    let whole_line = !verbatim.target_quoted
        && references.len() == 1
        && references.contains(&index)
        && fragment.is_empty();
    if !whole_line
        && (fragment.is_empty()
            || !(lines[index].surface_text.contains(fragment)
                || lines[index].reading_text.contains(fragment)))
    {
        return Ok(None);
    }
    let mut args = json!({"target_fragment":if whole_line {""} else {fragment},
        "replacement_text":verbatim.replacement,
        "evidence":original, "confidence":1.0});
    fill_classified_edit_target(workshop, index, &mut args)?;
    Ok(Some(normalize_player_edit_tool(workshop, original, &args)?))
}

#[cfg(test)]
mod tests;
