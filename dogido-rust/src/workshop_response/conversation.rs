//! 発話・表示・読み取りに必要な値を受け取り、補助情報を記録する。
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
    )
}

pub(super) fn decode(raw: &Value, details: &Value) -> Value {
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
        // 川柳相談で許可する参照はreading/sourceだけ。fixtureのmeterは撤去済み項目の
        // 拒否入力であり、音数計測や発話内容の棄却条件を復元する根拠にしない。
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
    let confidence = raw["confidence"].as_f64().unwrap_or(0.0);
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
    let evidence = cut(&raw["evidence"], 120);
    let analysis = analysis::finalize(raw, details, action, confidence);
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
