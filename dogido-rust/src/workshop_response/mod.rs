//! BERT・LLMが選んだ一手を実行用のデータへ整える。
//! 意味・原文一致・自己申告confidence・句の品質を再審査しない。
//! 現在使える操作と必要な引数だけを扱い、版・取消・保存は各runtimeが所有する。
mod analysis;
mod conversation;
#[cfg(test)]
mod tests;
use crate::reaction_leaf::sanitize;
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{collections::BTreeSet, sync::LazyLock};

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("assets.json")).expect("workshop response constants")
});
fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
fn array(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
fn strings(v: &Value) -> BTreeSet<&str> {
    array(v)
        .iter()
        .filter_map(Value::as_str)
        .filter(|s| !s.is_empty())
        .collect()
}
fn cut(v: &Value, n: usize) -> String {
    sanitize::strip(text(v)).chars().take(n).collect()
}
fn reject(reason: &str) -> Value {
    json!({"contract_errors":[],"step":null,"reason":reason})
}

pub(super) fn decode(frame: &Value, details: &Value) -> Result<Value> {
    ensure!(
        serde_json::to_vec(frame)?.len() < 1_000_000,
        "workshop response request too large"
    );
    ensure!(details.is_object(), "workshop response details object");
    for key in [
        "allowed_actions",
        "allowed_purposes",
        "allowed_checks",
        "allowed_problem_types",
    ] {
        ensure!(
            details[key]
                .as_array()
                .is_some_and(|v| v.iter().all(Value::is_string)),
            "invalid workshop detail {key}"
        );
    }
    for key in ["player_text", "original_player_text", "working_reading"] {
        ensure!(details[key].is_string(), "invalid workshop detail {key}");
    }
    ensure!(
        details["workshop_context"].is_object(),
        "invalid workshop context"
    );
    ensure!(frame["text"].is_string(), "workshop raw text");
    ensure!(frame["workshop"].is_object(), "workshop snapshot object");
    let raw = frame.get("payload").context("workshop payload missing")?;
    let action = text(&raw["action"]);
    if !strings(&details["allowed_actions"]).contains(action) {
        return Ok(reject("action_not_allowed"));
    }
    if conversation::handles(action) {
        return Ok(conversation::decode(raw, details));
    }
    if !strings(&ASSETS["actions"]).contains(action) {
        return Ok(reject("action_not_allowed"));
    }
    let confidence = raw["confidence"].as_f64().unwrap_or(0.0);
    let analysis = analysis::finalize(raw, details, action, confidence);
    if action == "stage_player_edit" && analysis["line_proposal"].is_null() {
        return Ok(reject("line_proposal_required"));
    }
    if action == "stage_conversation_candidate"
        && !frame["workshop"]["conversation_candidate"].is_object()
    {
        return Ok(reject("conversation_candidate_missing"));
    }
    if matches!(action, "accept_pending" | "reject_pending") && details["pending_verse"].is_null() {
        return Ok(reject("pending_required"));
    }
    if action == "propose_revision"
        && array(&analysis["findings"])
            .iter()
            .all(|f| f["line_index"].as_u64().is_none())
        && details["workshop_context"]["discussion_target"]["line_index"]
            .as_u64()
            .is_none()
    {
        return Ok(reject("repair_target_required"));
    }
    let purpose = ASSETS["mutation_purposes"][action]
        .as_str()
        .unwrap_or("improve_wording");
    // 終了を付けるのはruntimeのBERT End処理。ここでは入力済みの操作外形だけを確認する。
    let close = raw["close_after_action"] == true;
    if close && !matches!(action, "accept_pending" | "reject_pending") {
        return Ok(reject("close_after_action_not_allowed"));
    }
    Ok(json!({"contract_errors":[],"reason":"accepted","step":{
        "action":action,"purpose":purpose,"confidence":confidence,
        "evidence":cut(&raw["evidence"],120),"speech":text(&raw["speech"]).trim(),
        "checks":[],"close_after_action":close,"close_evidence":cut(&raw["close_evidence"],120),
        "analysis":analysis}}))
}

/// 称賛の解釈はモデルの結果。evidenceは任意の診断記録であり、操作の許可証ではない。
pub(super) fn feedback(payload: &Value) -> Option<Value> {
    (payload["kind"] == "praise")
        .then(|| json!({"kind":"praise","evidence":cut(&payload["evidence"],120)}))
}
