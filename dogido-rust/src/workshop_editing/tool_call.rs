//! Native tool試験の引数を、既存の原文検証とplayer_edit入力へ戻す。
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

/// 呼び手が渡す現在の句・未採用案と今回の原文に、stage_player_editの引数を照合する。
/// 元句の全行照合→原文にある引数の補完→Engine::run用のplayer_edit入力、の順に変換する。
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
    // only that line; never silently drop additional model changes. Its text
    // still has to be present in the player's current utterance below.
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

    // 操作根拠は今回の原文に必須。元句・行ID・confidence・evidenceは補完しない。
    ensure!(
        !args.evidence.is_empty()
            && args.evidence.chars().count() <= 120
            && original.contains(&args.evidence),
        "tool_evidence_not_in_original"
    );
    ensure!(
        !args.replacement_text.is_empty() && args.replacement_text.chars().count() <= 48,
        "tool_replacement_not_in_original"
    );
    ensure!(
        args.target_fragment.chars().count() <= 48,
        "tool_fragment_too_long"
    );
    // 原文に含まれるだけでは「うつろいの」の一部「うつろい」も通る。
    // 明示されたA/Bを原文から取り直し、モデルによる短縮・補作・前の案の再利用を捨てる。
    // ここでは語句の範囲だけを補完する。操作意思を語形から再判定しない。
    if let Some(verbatim) = parse::verbatim_edit(original)? {
        let fragment = parse::fragment_prefix(verbatim.target);
        let fragment = parse::unquote(&fragment);
        let literal = working
            .iter()
            .flat_map(|line| [&line.surface_text, &line.reading_text])
            .any(|line| !fragment.is_empty() && line.contains(fragment));
        let line_reference =
            args.target_fragment.is_empty() && args.line_reference_evidence == verbatim.target;
        // 明示置換の新語は原文から補完する。矢印の後の話し言葉を、既に原文にある新語へ足さない。
        if !(original.contains("->") || original.contains('→'))
            || !original.contains(&args.replacement_text)
        {
            args.replacement_text = verbatim.replacement.into();
        }
        // 現在句への対応または原文Aの短縮が確認できる場合に旧語を補完する。
        // 読点で分かれた前置きは旧語へ足さず、漢字の原文Aは後段の辞書照合へ渡す。
        if literal || line_reference || verbatim.target == args.target_fragment {
            let references = parse::explicit_lines(verbatim.target);
            if !verbatim.target_quoted
                && ((args.target_fragment.is_empty()
                && args.line_reference_evidence == verbatim.target)
                || (references.len() == 1
                    && references.contains(&selected)
                    && parse::fragment_prefix(verbatim.target).is_empty())
                // 「それ」は既存の相談対象を参照する。保持した対象とtool行が一致する時だけ
                // 省略形の処理を維持し、引用された句中語や別の行へ読み替えない。
                || (verbatim.target == "それ" && args.target_fragment.is_empty()
                    && crate::workshop_target::Target::from_view(workshop, working)
                        .is_some_and(|target| target.line_index == selected)))
            {
                args.target_fragment.clear();
            } else {
                // 漢字を含む旧語の読みは、後続Engineの既存辞書照合で確定する。
                // 「中七のくろい」の行呼称は既存の切り分けを使い、句中の語だけを渡す。
                let fragment = if verbatim.target_quoted {
                    verbatim.target.to_owned()
                } else if !args.line_reference_evidence.is_empty()
                    && let Some(fragment) = verbatim
                        .target
                        .strip_prefix(&args.line_reference_evidence)
                        .and_then(|s| s.strip_prefix('の'))
                {
                    fragment.to_owned()
                } else {
                    parse::fragment_prefix(verbatim.target)
                };
                args.target_fragment = parse::unquote(&fragment).into();
            }
        }
    } else {
        // A/Bを原文から一意に確定できない自由形では、従来どおり原文にない語を拒否する。
        ensure!(
            original.contains(&args.replacement_text),
            "tool_replacement_not_in_original"
        );
    }
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
            args.line_reference_evidence.chars().count() <= 48
                && original.contains(&args.line_reference_evidence),
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

#[cfg(test)]
mod tests;
