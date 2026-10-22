//! 発話・表示・読み取りには、編集や保存の根拠検証を要求しない。
//! 付随情報は用途ごとに拾い、壊れた補助項目で会話本文を捨てない。
use super::*;

pub(super) fn handles(action: &str) -> bool {
    matches!(
        action,
        "respond"
            | "explain"
            | "ask"
            | "compare"
            | "show_current"
            | "inspect"
            | "unrelated"
            | "acknowledge_meaning"
            | "continue_workshop"
    )
}

pub(super) fn validate(raw: &Value, details: &Value) -> Value {
    let action = text(&raw["action"]);
    if !strings(&details["allowed_actions"]).contains(action) {
        return reject("action_not_allowed");
    }
    let speech = sanitize::strip(text(&raw["speech"]));
    if matches!(action, "respond" | "explain" | "ask" | "compare") && speech.is_empty() {
        return reject("invalid_speech");
    }
    let mut checks = Vec::new();
    let mut inspect_text = "";
    let mut inspect_line_index = None;
    if action == "inspect" {
        let Some(requested) = raw["checks"].as_array() else {
            return reject("inspection_check_required");
        };
        for check in requested {
            let Some(check) = check
                .as_str()
                .filter(|c| matches!(*c, "reading" | "source"))
            else {
                return reject("invalid_checks");
            };
            if !checks.contains(&check) {
                checks.push(check);
            }
        }
        if checks.is_empty() {
            return reject("inspection_check_required");
        }
        if let Some(target) = raw.get("inspect_text") {
            let Some(target) = target.as_str().filter(|s| s.chars().count() <= 160) else {
                return reject("invalid_inspection_text");
            };
            inspect_text = target.trim();
        }
        if let Some(index) = raw.get("inspect_line_index").filter(|v| !v.is_null()) {
            let Some(index) = index.as_u64().filter(|i| *i < 3) else {
                return reject("invalid_inspection_line_index");
            };
            inspect_line_index = Some(index);
        }
    }
    // 自己申告の確信度や引用は会話の通行証ではない。未確認値を補って
    // findingsや採用権限へ昇格させず、ログには確定できる情報だけ残す。
    let confidence = raw["confidence"]
        .as_f64()
        .filter(|c| (0.0..=1.0).contains(c))
        .unwrap_or(0.0);
    let purpose = match text(&raw["purpose"]) {
        p @ ("continue_discussion"
        | "understand_meaning"
        | "improve_wording"
        | "review_pending"
        | "evaluate_verse"
        | "show_verse"
        | "other") => p,
        _ => "continue_discussion",
    };
    let player = text(&details["player_text"]);
    let evidence = text(&raw["evidence"]);
    let evidence = if !evidence.is_empty() && player.contains(evidence) {
        evidence
    } else {
        ""
    };
    let mut analysis = analysis::finalize(raw, details, action, confidence, player);
    // 発話だけを救済したときに、原文へ結べない評価からlessonを増やさない。
    // 提案候補と行対象は、それぞれ自身の根拠検証で保持する。
    let feedback_allowed = confidence >= 0.72 && !evidence.is_empty();
    analysis["feedback_allowed"] = json!(feedback_allowed);
    if !feedback_allowed {
        analysis["findings"] = json!([]);
    }
    let mut step = json!({"action":action,"purpose":purpose,"confidence":confidence,
        "evidence":evidence,"speech":speech,"checks":checks,
        "close_after_action":false,"close_evidence":"","analysis":analysis});
    if !inspect_text.is_empty() {
        step["inspect_text"] = inspect_text.into();
        if let Some(index) = inspect_line_index {
            step["inspect_line_index"] = index.into();
        }
    }
    json!({"contract_errors":[],"reason":"accepted","step":step})
}
