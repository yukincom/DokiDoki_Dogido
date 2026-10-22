//! 一句の相談で許可する一手と、保存された読み・資料の参照を扱う。
//! 音数や句の品質は判定せず、正本の書換え・保存は呼出元が担当する。
pub(crate) mod inspection_tool;
pub(crate) mod tools;
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

/// 保存された読み・行資料だけを返す。句の合否を判定せず、現在世界の観測で上書きしない。
pub fn inspect(lines: &[HaikuLine], checks: &[String]) -> Value {
    let requested: Vec<_> = ["reading", "source"]
        .into_iter()
        .filter(|c| checks.iter().any(|v| v == c))
        .collect();
    let mut rows = Vec::new();
    let mut codes = Vec::new();
    for index in 0..3 {
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

/// 指定語の辞書読みを返す。未解決なら読みを未確定のまま保つ。
/// 行番号は参照先の表示用であり、音数目標や合否は付けない。
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
    if let Some(index) = line_index.filter(|i| *i < 3) {
        row["line_index"] = index.into();
        row["canonical_name"] = ["上五", "中七", "下五"][index].into();
    }
    if checks.iter().any(|c| c == "source") {
        row["source_status"] = "unavailable".into();
        codes.push("source_not_recorded_for_text");
    }
    let requested: Vec<_> = checks
        .iter()
        .filter(|check| matches!(check.as_str(), "reading" | "source"))
        .collect();
    json!({"kind":"inspection","status":"completed","verse_kind":"text",
        "checks":requested,"lines":[row],"validation_codes":codes})
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
        return format!(
            "「{source}」は今回の確認では分からんかったわ。その言葉の相談は続けられるで。"
        );
    }
    let has = |s: &str| {
        o["checks"]
            .as_array()
            .is_some_and(|c| c.contains(&json!(s)))
    };
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
        let result = inspect(&[], &["source".into(), "reading".into()]);
        assert_eq!(result["lines"][0]["mora_count"], Value::Null);
        assert_eq!(result["lines"][1]["source_status"], "unavailable");
        assert_eq!(result["validation_codes"].as_array().unwrap().len(), 6);
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
    fn references_never_emit_mora_counts_targets_or_quality_results() {
        for text in ["ひ", "ひのあかり", "ひのあかりがながくつづく"] {
            let result = inspect_text(text, &["reading".into(), "meter".into()], None, Some(0));
            assert_eq!(result["lines"][0]["reading_text"], text);
            assert_eq!(result["checks"], json!(["reading"]));
            for key in [
                "mora_count",
                "meter_delta",
                "meter_status",
                "target_mora",
                "meter_exact",
            ] {
                assert!(result["lines"][0].get(key).is_none(), "{key}");
            }
            assert_eq!(result["validation_codes"], json!([]));
        }
        let old = json!({"verse_kind":"text","checks":["meter"],
            "lines":[{"surface_text":"ひのあかり","mora_count":5}]});
        assert!(!fallback(Some(&old)).contains("5音"));
        let old = json!({"checks":["meter"],"lines":[{"mora_count":5},{"mora_count":7},{"mora_count":5}]});
        assert!(!fallback(Some(&old)).contains("音数"));
    }

    #[test]
    fn dictionary_lookup_preserves_known_and_unknown_readings_without_quality_checks() {
        let checks = vec!["reading".into()];
        let known = inspect_text("白い骨", &checks, Some("シロイホネ"), None);
        assert_eq!(known["lines"][0]["reading_text"], "しろいほね");
        assert_eq!(known["verse_kind"], "text");
        for source in ["白い骨", "未登録語Abc"] {
            let unknown = inspect_text(source, &checks, Some(source), Some(2));
            assert_eq!(unknown["lines"][0]["reading_status"], "unavailable");
            assert!(unknown["lines"][0]["reading_text"].is_null());
            assert!(unknown["lines"][0].get("mora_count").is_none());
        }
    }
}
