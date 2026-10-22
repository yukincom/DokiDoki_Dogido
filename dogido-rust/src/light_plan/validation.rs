//! 一回の照明反応の外形と許可actionを確認する。本文の意味採点や再生成はしない。
use super::actions;
use serde_json::Value;

/// 外形・許可action・発話有無を確認する。観測の現在性はambientが配送前にも照合する。
pub fn contract_errors(payload: &Value, details: &Value) -> Vec<String> {
    let Some(object) = payload.as_object() else {
        return vec!["$:model_type".into()];
    };
    let mut errors = Vec::new();
    let action = object.get("action").and_then(Value::as_str);
    if !action.is_some_and(|a| {
        matches!(
            a,
            "stay_silent" | "acknowledge_supply_gain" | "relief_after_darkness"
        ) && actions(details).contains(a)
    }) {
        errors.push("action:not_allowed".into());
    }
    match object.get("speech").and_then(Value::as_str) {
        None => errors.push("speech:string_required".into()),
        Some(s) if action == Some("stay_silent") && !s.trim().is_empty() => {
            errors.push("speech:silent_requires_empty".into())
        }
        Some(s) if action != Some("stay_silent") && s.trim().is_empty() => {
            errors.push("speech:empty".into())
        }
        _ => (),
    }
    for key in object.keys() {
        if !matches!(key.as_str(), "action" | "speech" | "__dogido_status") {
            errors.push(format!("{key}:extra_forbidden"));
        }
    }
    errors
}
