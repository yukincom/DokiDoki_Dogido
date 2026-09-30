//! 一句の相談で許可する一手、実測、固定応答。正本の書換えや保存はしない。
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
        actions.extend(["inspect", "unrelated"]);
        if pending {
            actions.extend(["accept_pending", "reject_pending"]);
        } else {
            actions.push("close_workshop");
            if conversation_candidate {
                actions.push("stage_conversation_candidate");
            }
        }
    }
    if pending {
        actions.push("compare");
    }
    actions
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
        let mut row = json!({"line_index":index});
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
            row["mora_count"] = json!(count);
            row["target_mora"] = target.into();
            row["meter_delta"] = json!(count.map(|c| c - target));
            row["meter_exact"] = json!(count.map(|c| c == target));
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
    // checksの順序は呼出側の順序を維持（Python inspect_workshopと同じ）。
    let mut ordered = Vec::new();
    for c in checks {
        if requested.contains(&c.as_str()) && !ordered.contains(c) {
            ordered.push(c.clone());
        }
    }
    json!({"kind":"inspection","status":"completed","verse_kind":"canonical",
        "checks":ordered,"lines":rows,"validation_codes":codes})
}

pub fn fallback(observation: Option<&Value>) -> String {
    let Some(o) = observation else {
        return "ごめん、今の返事をうまくまとめられんかったわ。".into();
    };
    let rows = o["lines"].as_array().map(Vec::as_slice).unwrap_or(&[]);
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

/// 原文が代表形に完全一致するときだけ、モデルを使わず一手を確定する。
pub fn fixed_action(text: &str) -> Option<&'static str> {
    if fixed_praise(text) {
        return Some("close_workshop");
    }
    match text.trim().trim_end_matches(['。', '！', '!']) {
        "終了"
        | "終わり"
        | "川柳は終了"
        | "川柳終わり"
        | "句はここまで"
        | "終了でいいよ"
        | "終了でお願いします" => Some("close_workshop"),
        "今の句"
        | "今の川柳"
        | "もう一度読んで"
        | "直った？"
        | "直った?"
        | "修正できた？"
        | "修正できた?" => Some("show_current"),
        _ => None,
    }
}

/// 旧workshopの明確な称賛だけを同期で確定する。疑問・否定・引用は全体一致から外れる。
pub fn fixed_praise(text: &str) -> bool {
    let mut text = text.trim().trim_end_matches(['。', '！', '!']);
    for prefix in ["うん", "ほんまに", "めっちゃ", "なかなか", "すごく"] {
        if let Some(rest) = text.strip_prefix(prefix) {
            text = rest.trim_start_matches(['、', ',', ' ']);
            break;
        }
    }
    for subject in ["これ", "この句", "その句", "句"] {
        if let Some(rest) = text.strip_prefix(subject) {
            text = rest
                .strip_prefix(['は', 'が'])
                .unwrap_or(rest)
                .trim_start_matches(['、', ',', ' ']);
            break;
        }
    }
    [
        "いい句",
        "良い句",
        "ええ句",
        "いい",
        "ええ",
        "うまい",
        "上手",
        "好き",
        "気に入った",
        "そのままでいい",
        "そのままでええ",
    ]
    .iter()
    .any(|base| {
        text.strip_prefix(base).is_some_and(|tail| {
            [
                "",
                "やな",
                "やね",
                "やん",
                "やで",
                "やわ",
                "や",
                "だね",
                "ですね",
                "だ",
                "です",
                "な",
                "ね",
                "よ",
                "わ",
                "と思う",
                "って思う",
                "だと思う",
                "だって思う",
                "やと思う",
                "やって思う",
                "とおもう",
                "っておもう",
                "だとおもう",
                "だっておもう",
                "やとおもう",
                "やっておもう",
            ]
            .contains(&tail)
        })
    })
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
    fn fixed_close_does_not_consume_questions_or_quotations() {
        assert_eq!(fixed_action("終了でいいよ。"), Some("close_workshop"));
        for text in [
            "終了？",
            "「終了」",
            "終了しない",
            "終了と言った",
            "終了でいいよ？",
        ] {
            assert_eq!(fixed_action(text), None);
        }
    }
    #[test]
    fn fixed_praise_closes_only_unqualified_whole_utterances() {
        for text in [
            "いい句だね",
            "ええ句やな。",
            "うん、この句は好き！",
            "ほんまに上手",
            "そのままでいい",
            "良い句だと思う",
        ] {
            assert!(fixed_praise(text), "{text}");
            assert_eq!(fixed_action(text), Some("close_workshop"));
        }
        for text in [
            "いい句だね？",
            "『いい句だね』",
            "いい句ではない",
            "いい句だねと言った",
            "いい句だけど下五を直して",
            "いい句なら終わろう",
            "好きじゃない",
        ] {
            assert!(!fixed_praise(text), "{text}");
            assert_eq!(fixed_action(text), None);
        }
    }
    #[test]
    fn one_inspection_per_turn_and_no_unimplemented_mutations() {
        assert!(!allowed_actions("after_inspection", false, false).contains(&"inspect"));
        for a in ["accept_pending", "reject_pending"] {
            assert!(!allowed_actions("decide", false, false).contains(&a));
        }
    }
}
