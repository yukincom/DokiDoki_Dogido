//! 一句の相談で許可する一手、実測、固定応答。正本の書換えや保存はしない。
pub(crate) mod inspection_tool;
pub(crate) mod tools;
use crate::haiku::meter::count_japanese_sounds;
use crate::haiku_record::HaikuLine;
use serde_json::{Value, json};

pub fn allowed_actions(
    phase: &str,
    pending: bool,
    conversation_candidate: bool,
) -> Vec<&'static str> {
    if phase == "after_validation" {
        return vec!["respond", "explain", "ask", "compare", "show_current"];
    }
    let mut actions = vec![
        "respond",
        "explain",
        "ask",
        "show_current",
        "stage_player_edit",
    ];
    if !pending {
        actions.push("propose_revision");
    }
    if phase == "decide" {
        actions.extend(["inspect", "unrelated", "close_workshop"]);
        if pending {
            actions.extend(["accept_pending", "reject_pending"]);
        } else if conversation_candidate {
            actions.push("stage_conversation_candidate");
        }
    }
    if pending {
        actions.push("compare");
    }
    actions
}

fn meter_result(row: &mut Value, count: Option<i64>, target: i64) {
    row["mora_count"] = json!(count);
    row["target_mora"] = target.into();
    row["meter_delta"] = json!(count.map(|c| c - target));
    row["meter_exact"] = json!(count.map(|c| c == target));
    row["meter_status"] = match count {
        None => "unavailable",
        Some(c) if c > target => "too_long",
        Some(c) if c < target => "too_short",
        Some(_) => "exact",
    }
    .into();
}

/// 生成時に確定した読み・行出典だけを検査する。現在世界の観測で上書きしない。
pub fn inspect(lines: &[HaikuLine], checks: &[String]) -> Value {
    let requested: Vec<_> = ["reading", "meter", "source"]
        .into_iter()
        .filter(|c| checks.iter().any(|v| v == c))
        .collect();
    let mut rows = Vec::new();
    let mut codes = Vec::new();
    for (index, target) in [5, 7, 5].into_iter().enumerate() {
        let record = lines.iter().find(|l| l.line_index == index);
        let reading = record.map_or("", |l| l.reading_text.as_str());
        let mut row = json!({"line_index":index,"canonical_name":(["上五","中七","下五"][index])});
        if requested.contains(&"reading") {
            row["surface_text"] = record.map_or(reading, |l| l.surface_text.as_str()).into();
            row["reading_text"] = if reading.is_empty() {
                Value::Null
            } else {
                reading.into()
            };
            row["reading_status"] = if reading.is_empty() {
                "unavailable"
            } else {
                "known"
            }
            .into();
            if reading.is_empty() {
                codes.push(format!("reading_unavailable_line_{index}"));
            }
        }
        if requested.contains(&"meter") {
            let count = (!reading.is_empty()).then(|| count_japanese_sounds(reading) as i64);
            meter_result(&mut row, count, target);
            if count.is_none() {
                codes.push(format!("meter_unavailable_line_{index}"));
            } else if count != Some(target) {
                codes.push(format!("meter_mismatch_line_{index}"));
            }
        }
        if requested.contains(&"source") {
            let ids = record
                .map(|l| l.source_atom_ids.clone())
                .unwrap_or_default();
            let texts: Vec<_> = record
                .into_iter()
                .flat_map(|l| &l.source_atoms)
                .filter_map(|s| s.get("text").and_then(Value::as_str))
                .filter(|s| !s.is_empty())
                .take(3)
                .map(|s| s.chars().take(240).collect::<String>())
                .collect();
            row["source_status"] = if ids.is_empty() {
                "unavailable"
            } else {
                "recorded"
            }
            .into();
            if ids.is_empty() {
                codes.push(format!("source_unavailable_line_{index}"));
            }
            row["source_atom_ids"] = json!(ids);
            row["source_texts"] = json!(texts);
        }
        rows.push(row);
    }
    // checksの順序は呼出側の要求順を維持する。
    let mut ordered = Vec::new();
    for c in checks {
        if requested.contains(&c.as_str()) && !ordered.contains(c) {
            ordered.push(c.clone());
        }
    }
    json!({"kind":"inspection","status":"completed","verse_kind":"canonical",
        "checks":ordered,"lines":rows,"validation_codes":codes})
}

/// モデルが指定した語・候補の読みを確認する。辞書が解決できなければ
/// 読みも音数も未確定として返し、漢字を一文字一音として数えない。
pub fn inspect_text(
    source: &str,
    checks: &[String],
    dictionary_reading: Option<&str>,
    line_index: Option<usize>,
) -> Value {
    let reading = crate::haiku::lexical::hiragana(dictionary_reading.unwrap_or(source))
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect::<String>();
    let known = !reading.is_empty()
        && reading
            .chars()
            .all(|c| ('ぁ'..='ゖ').contains(&c) || c == 'ー');
    let mut row = json!({"surface_text":source});
    let mut codes = Vec::new();
    if checks.iter().any(|c| c == "reading") {
        row["reading_text"] = if known { json!(reading) } else { Value::Null };
        row["reading_status"] = if known { "known" } else { "unavailable" }.into();
        if !known {
            codes.push("reading_unavailable");
        }
    }
    if checks.iter().any(|c| c == "meter") {
        let count = known.then(|| count_japanese_sounds(&reading) as i64);
        row["mora_count"] = json!(count);
        if let Some(index) = line_index.filter(|i| *i < 3) {
            row["line_index"] = index.into();
            row["canonical_name"] = ["上五", "中七", "下五"][index].into();
            meter_result(&mut row, count, [5, 7, 5][index]);
        }
        if !known {
            codes.push("meter_unavailable");
        }
    }
    if checks.iter().any(|c| c == "source") {
        row["source_status"] = "unavailable".into();
        codes.push("source_not_recorded_for_text");
    }
    json!({"kind":"inspection","status":"completed","verse_kind":"text",
        "checks":checks,"lines":[row],"validation_codes":codes})
}

pub fn fallback(observation: Option<&Value>) -> String {
    let Some(o) = observation else {
        return "ごめん、今の返事をうまくまとめられんかったわ。".into();
    };
    let rows = o["lines"].as_array().map(Vec::as_slice).unwrap_or(&[]);
    if o["verse_kind"] == "text" {
        let row = &o["lines"][0];
        let source = row["surface_text"].as_str().unwrap_or("");
        if let Some(reading) = row["reading_text"].as_str() {
            return format!("「{source}」の読みは「{reading}」やで。");
        }
        if let Some(count) = row["mora_count"].as_u64() {
            return format!("「{source}」は{count}音やで。");
        }
        return format!(
            "「{source}」は今回の確認では分からんかったわ。その言葉の相談は続けられるで。"
        );
    }
    let has = |s: &str| {
        o["checks"]
            .as_array()
            .is_some_and(|c| c.contains(&json!(s)))
    };
    if has("meter") {
        let counts: Vec<_> = rows
            .iter()
            .filter_map(|r| r["mora_count"].as_i64())
            .map(|n| n.to_string())
            .collect();
        if counts.len() == 3 {
            return format!(
                "音数は上から{}やで。どの行を一緒に見よか？",
                counts.join("・")
            );
        }
    }
    if has("source") {
        let n = rows
            .iter()
            .filter(|r| r["source_status"] == "recorded")
            .count();
        return match n {
            3 => "三行とも出典の記録があるで。どの行を一緒に見よか？".into(),
            0 => "三行には出典の記録が見つからんかったわ。元の句はそのままや。".into(),
            _ => format!("出典の記録があるのは三行中{n}行やで。どの行を一緒に見よか？"),
        };
    }
    if has("reading") {
        return if rows.iter().any(|r| r["reading_status"] != "known") {
            "読みを確定できん行があったわ。元の句はそのままや。"
        } else {
            "今の三行の読みは確認できたで。どの言葉を一緒に見よか？"
        }
        .into();
    }
    "検査結果は確認できたで。どこを一緒に見よか？".into()
}

pub fn fallback_for(observation: Option<&Value>, view: &Value, lines: &[HaikuLine]) -> String {
    if observation.is_none() && view["pending"].is_object() {
        return "未採用の案があるで。採用するか、元の句に戻すか教えてな。".into();
    }
    let reply = fallback(observation);
    let Some(target) = crate::workshop_target::Target::from_view(view, lines) else {
        return reply;
    };
    let label = target.label(lines);
    if observation.is_none() {
        return format!("{label}の話やな。{reply}");
    }
    reply
        .replace(
            "どの行を一緒に見よか？",
            &format!("{label}の話を続けよか。"),
        )
        .replace(
            "どの言葉を一緒に見よか？",
            &format!("{label}の話を続けよか。"),
        )
        .replace("どこを一緒に見よか？", &format!("{label}の話を続けよか。"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn never_invent_source_or_reading() {
        let result = inspect(&[], &["meter".into(), "source".into(), "reading".into()]);
        assert_eq!(result["lines"][0]["mora_count"], Value::Null);
        assert_eq!(result["lines"][1]["source_status"], "unavailable");
        assert_eq!(result["validation_codes"].as_array().unwrap().len(), 9);
        assert!(fallback(Some(&result)).contains("記録が見つからん"));
    }
    #[test]
    fn one_inspection_per_turn_and_no_unimplemented_mutations() {
        assert!(!allowed_actions("after_inspection", false, false).contains(&"inspect"));
        for a in ["accept_pending", "reject_pending"] {
            assert!(!allowed_actions("decide", false, false).contains(&a));
        }
    }
}

#[cfg(test)]
mod inspection_tests {
    use super::*;
    #[test]
    fn inspection_returns_exact_excess_and_shortage_without_changing_a_line() {
        let checks = vec!["meter".into()];
        for (reading, index, count, delta, status) in [
            ("しろいほねたち", 2, 7, 2, "too_long"),
            ("しろいほね", 1, 5, -2, "too_short"),
            ("しろいほね", 0, 5, 0, "exact"),
        ] {
            let result = inspect_text(reading, &checks, None, Some(index));
            let row = &result["lines"][0];
            assert_eq!(row["mora_count"], count);
            assert_eq!(row["meter_delta"], delta);
            assert_eq!(row["meter_status"], status);
            assert_eq!(row["target_mora"], [5, 7, 5][index]);
        }
        let plain = inspect_text("しろいほね", &checks, None, None);
        assert!(plain["lines"][0]["meter_status"].is_null());
    }

    #[test]
    fn a_dictionary_reading_is_measured_without_inventing_an_unknown_reading() {
        let checks = vec!["reading".into(), "meter".into()];
        let known = inspect_text("白い骨", &checks, Some("シロイホネ"), None);
        assert_eq!(known["lines"][0]["reading_text"], "しろいほね");
        assert_eq!(known["lines"][0]["mora_count"], 5);
        assert_eq!(known["verse_kind"], "text");
        for source in ["白い骨", "未登録語Abc"] {
            let unknown = inspect_text(source, &checks, Some(source), Some(2));
            assert_eq!(unknown["lines"][0]["reading_status"], "unavailable");
            assert!(unknown["lines"][0]["mora_count"].is_null());
            assert!(unknown["lines"][0]["meter_delta"].is_null());
            assert_eq!(unknown["lines"][0]["meter_status"], "unavailable");
        }
        assert_eq!(
            inspect_text("しろいほね", &checks, None, None)["lines"][0]["mora_count"],
            5
        );
    }
}
