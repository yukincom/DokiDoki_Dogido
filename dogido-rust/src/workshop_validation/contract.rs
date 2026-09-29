//! The one strict Pydantic contract used here, including its error ordering.
use super::*;

fn field_path(prefix: &str, key: &str) -> String {
    if prefix.is_empty() {
        key.into()
    } else {
        format!("{prefix}.{key}")
    }
}
fn check(schema: &Value, value: &Value, path: &str, errors: &mut Vec<String>) {
    if let Some(reference) = schema["$ref"].as_str() {
        return check(
            &ASSETS["schema"]["$defs"][reference.rsplit('/').next().unwrap()],
            value,
            path,
            errors,
        );
    }
    let error = |errors: &mut Vec<String>, reason: &str| {
        errors.push(format!(
            "{}:{reason}",
            if path.is_empty() { "$" } else { path }
        ))
    };
    if let Some(variants) = schema["enum"].as_array() {
        if !variants.contains(value) {
            error(errors, "literal_error");
        }
        return;
    }
    if let Some(variants) = schema["anyOf"].as_array() {
        if variants.iter().any(|v| v["type"] == "null") {
            if !value.is_null() && value.as_i64().is_none() && value.as_u64().is_none() {
                error(errors, "int_type");
            }
        } else if let Some(number) = value.as_f64() {
            if number < schema["ge"].as_f64().unwrap() {
                error(errors, "greater_than_equal");
            } else if number > schema["le"].as_f64().unwrap() {
                error(errors, "less_than_equal");
            }
        } else {
            errors.push(format!("{path}.float:float_type"));
            errors.push(format!("{path}.int:int_type"));
        }
        return;
    }
    match text(&schema["type"]) {
        "object" => {
            let Some(object) = value.as_object() else {
                error(errors, "model_type");
                return;
            };
            let properties = schema["properties"].as_object().unwrap();
            for (key, child) in properties {
                let loc = field_path(path, key);
                if let Some(value) = object.get(key) {
                    check(child, value, &loc, errors);
                } else if array(&schema["required"]).iter().any(|v| v == key) {
                    errors.push(format!("{loc}:missing"));
                }
            }
            for key in object.keys() {
                if !properties.contains_key(key) {
                    errors.push(format!("{}:extra_forbidden", field_path(path, key)));
                }
            }
        }
        "string" => {
            if !value.is_string() {
                error(errors, "string_type");
            }
        }
        "boolean" => {
            if !value.is_boolean() {
                error(errors, "bool_type");
            }
        }
        "array" => {
            let Some(values) = value.as_array() else {
                error(errors, "list_type");
                return;
            };
            if schema["maxItems"]
                .as_u64()
                .is_some_and(|max| values.len() > max as usize)
            {
                error(errors, "too_long");
                return;
            }
            for (index, value) in values.iter().enumerate() {
                check(
                    &schema["items"],
                    value,
                    &field_path(path, &index.to_string()),
                    errors,
                );
            }
        }
        other => panic!("unexpected pinned workshop schema type {other}"),
    }
}
pub(super) fn errors(payload: &Value, details: &Value) -> Vec<String> {
    let mut errors = Vec::new();
    check(&ASSETS["schema"], payload, "", &mut errors);
    if !errors.is_empty() {
        if errors.len() > 8 {
            errors.truncate(8);
            errors.push("additional_errors".into());
        }
        return errors;
    }
    let action = text(&payload["action"]);
    let purpose = text(&payload["purpose"]);
    let allowed = strings(&details["allowed_actions"]);
    if !allowed.is_empty() && !allowed.contains(action) {
        errors.push("action:not_allowed".into());
    }
    let allowed = strings(&details["allowed_purposes"]);
    if !allowed.is_empty() && !allowed.contains(purpose) {
        errors.push("purpose:not_allowed".into());
    }
    if let Some(required) = ASSETS["mutation_purposes"][action].as_str()
        && purpose != required
    {
        errors.push("purpose:action_mismatch".into());
    }
    let checks = array(&payload["checks"]);
    let unique = strings(&payload["checks"]);
    if checks.len() != unique.len() {
        errors.push("checks:duplicate".into());
    }
    if !unique.is_subset(&strings(&details["allowed_checks"])) {
        errors.push("checks:not_allowed".into());
    }
    if action == "inspect" && checks.is_empty() {
        errors.push("checks:inspection_requires_check".into());
    }
    if action != "inspect" && !checks.is_empty() {
        errors.push("checks:only_for_inspection".into());
    }
    let direct = strings(&ASSETS["direct"]).contains(action);
    let speech = sanitize::strip(text(&payload["speech"]));
    if direct && speech.is_empty() {
        errors.push("speech:required".into());
    }
    if !direct && !speech.is_empty() {
        errors.push("speech:not_allowed".into());
    }
    let close = payload["close_after_action"].as_bool().unwrap();
    let close_evidence = text(&payload["close_evidence"]);
    if close && !matches!(action, "accept_pending" | "reject_pending") {
        errors.push("close_after_action:not_allowed".into());
    }
    if !close && !sanitize::strip(close_evidence).is_empty() {
        errors.push("close_evidence:unexpected".into());
    }
    let evidence = text(&payload["evidence"]);
    let player = text(&details["player_text"]);
    if action != "defer_to_legacy"
        && (sanitize::strip(evidence).chars().count() < 2 || !player.contains(evidence))
    {
        errors.push("evidence:not_exact".into());
    }
    if close
        && (sanitize::strip(close_evidence).chars().count() < 2 || !player.contains(close_evidence))
    {
        errors.push("close_evidence:not_exact".into());
    }
    let original = if truth(&details["original_player_text"]) {
        text(&details["original_player_text"])
    } else {
        player
    };
    if ASSETS["mutation_purposes"].get(action).is_some() && !original.contains(evidence) {
        errors.push("evidence:not_in_original".into());
    }
    if close && !original.contains(close_evidence) {
        errors.push("close_evidence:not_in_original".into());
    }
    let problems = strings(&details["allowed_problem_types"]);
    for (index, row) in array(&payload["findings"]).iter().enumerate() {
        if !row["line_index"].is_null() && !row["line_index"].as_u64().is_some_and(|n| n < 3) {
            errors.push(format!("findings.{index}.line_index:not_allowed"));
        }
        if !problems.is_empty() && !problems.contains(text(&row["problem"])) {
            errors.push(format!("findings.{index}.problem:not_allowed"));
        }
    }
    errors.truncate(12);
    errors
}
