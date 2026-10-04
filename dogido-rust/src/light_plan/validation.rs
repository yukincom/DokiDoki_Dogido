use super::{actions, basis};
use serde_json::Value;
use std::collections::BTreeSet;

/// action・basis_ids・confidenceの型を検査し、許可actionと今回の根拠IDへ照合する。
/// 再試行へ渡すエラーコードと順序は現行のstructured契約にそろえる。
/// 低confidenceは契約不一致ではない。採用閾値と現在観測はambientが再検査する。
pub fn contract_errors(payload: &Value, details: &Value) -> Vec<String> {
    let Some(object) = payload.as_object() else {
        return vec!["$:model_type".into()];
    };
    let mut errors = Vec::new();
    match object.get("action") {
        None => errors.push("action:missing".into()),
        Some(action)
            if !matches!(
                action.as_str(),
                Some("stay_silent" | "acknowledge_supply_gain" | "relief_after_darkness")
            ) =>
        {
            errors.push("action:literal_error".into())
        }
        _ => (),
    }
    match object.get("basis_ids") {
        None => errors.push("basis_ids:missing".into()),
        Some(Value::Array(ids)) if ids.is_empty() => errors.push("basis_ids:too_short".into()),
        Some(Value::Array(ids)) if ids.len() > 3 => errors.push("basis_ids:too_long".into()),
        Some(Value::Array(ids)) => {
            for (index, id) in ids.iter().enumerate() {
                match id.as_str() {
                    None => errors.push(format!("basis_ids.{index}:string_type")),
                    Some("") => errors.push(format!("basis_ids.{index}:string_too_short")),
                    _ => (),
                }
            }
        }
        _ => errors.push("basis_ids:list_type".into()),
    }
    match object.get("confidence") {
        None => errors.push("confidence:missing".into()),
        Some(value) => match value.as_f64() {
            None => errors.extend([
                "confidence.float:float_type".into(),
                "confidence.int:int_type".into(),
            ]),
            Some(n) if n < 0.0 => errors.push("confidence:greater_than_equal".into()),
            Some(n) if n > 1.0 => errors.push("confidence:less_than_equal".into()),
            _ => (),
        },
    }
    for key in object.keys() {
        if !matches!(key.as_str(), "action" | "basis_ids" | "confidence") {
            errors.push(format!("{key}:extra_forbidden"));
        }
    }
    if !errors.is_empty() {
        if errors.len() > 8 {
            errors.truncate(8);
            errors.push("additional_errors".into());
        }
        return errors;
    }
    let action = payload["action"].as_str().unwrap();
    let allowed = actions(details);
    if !allowed.is_empty() && !allowed.contains(action) {
        errors.push("action:not_allowed".into());
    }
    let ids = payload["basis_ids"].as_array().unwrap();
    let unique: BTreeSet<&str> = ids.iter().map(|v| v.as_str().unwrap()).collect();
    if ids.len() != unique.len() {
        errors.push("basis_ids:duplicate".into());
    }
    let known = basis(details);
    let unknown: Vec<_> = unique.difference(&known).take(3).copied().collect();
    if !unknown.is_empty() {
        errors.push(format!("basis_ids:unknown={}", unknown.join(",")));
    }
    if action == "relief_after_darkness" && !unique.contains("dark_push_recovered") {
        errors.push("basis_ids:dark_push_recovered_required".into());
    }
    if action == "acknowledge_supply_gain"
        && !["first_light_supply", "supply_before", "surroundings_light"]
            .iter()
            .any(|id| unique.contains(id))
    {
        errors.push("basis_ids:acknowledgement_reason_required".into());
    }
    errors
}
