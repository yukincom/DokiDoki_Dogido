//! Pure acceptance of a workshop model step. No dictionary, state mutation or I/O.
mod analysis;
mod contract;
#[cfg(test)]
mod tests;
use crate::{reaction_leaf::sanitize, workshop_input_guard};
use anyhow::{Context, Result, ensure};
use regex::Regex;
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("assets.json")).expect("canonical workshop validation assets")
});
static PATTERNS: LazyLock<HashMap<String, Regex>> = LazyLock::new(|| {
    let mut patterns = HashMap::new();
    let compile = |s: &str| {
        Regex::new(&s.replace(r"\s", r"[\s\x1c-\x1f]")).expect("canonical workshop regex")
    };
    for key in [
        "edit_quote",
        "edit_negative",
        "edit_positive",
        "repair_quote",
        "repair_negative",
        "repair_positive",
        "followup_negative",
        "resume_negative",
        "ack_negative",
        "pending_accept",
        "pending_reject",
    ] {
        patterns.insert(key.to_owned(), compile(text(&ASSETS[key])));
    }
    for (action, p) in ASSETS["positive"].as_object().unwrap() {
        patterns.insert(action.clone(), compile(text(p)));
    }
    for pair in array(&ASSETS["explicit_lines"]) {
        patterns.insert(format!("line_{}", pair[0]), compile(text(&pair[1])));
    }
    patterns
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
fn truth(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
fn cut(v: &Value, n: usize) -> String {
    sanitize::strip(text(v)).chars().take(n).collect()
}
fn compact(s: &str) -> String {
    s.chars()
        .filter(|c| {
            !c.is_whitespace()
                && !('\u{1c}'..='\u{1f}').contains(c)
                && !"、。！？!?・,:：『』「」".contains(*c)
        })
        .collect::<String>()
        .to_lowercase()
}
fn has_markers(s: &str, v: &Value) -> bool {
    array(v).iter().any(|x| s.contains(text(x)))
}
fn explicit_lines(s: &str) -> BTreeSet<usize> {
    (0..3)
        .filter(|i| PATTERNS[&format!("line_{i}")].is_match(s))
        .collect()
}
fn explicit(action: &str, source: &str, evidence: &str) -> bool {
    if !workshop_input_guard::state_change_safe("stage_player_edit", source, evidence) {
        return false;
    }
    let key = if action == "stage_player_edit" {
        "edit"
    } else {
        "repair"
    };
    let outside = PATTERNS[&format!("{key}_quote")]
        .replace_all(source, if key == "edit" { "候補" } else { "対象" });
    !PATTERNS[&format!("{key}_negative")].is_match(&outside)
        && PATTERNS[&format!("{key}_positive")].is_match(if key == "edit" {
            &outside
        } else {
            evidence
        })
}
fn positive(action: &str, evidence: &str) -> bool {
    let pending = match action {
        "accept_pending" => Some("pending_accept"),
        "reject_pending" => Some("pending_reject"),
        _ => None,
    };
    if let Some(key) = pending
        && !evidence.contains(['?', '？'])
        && PATTERNS[key]
            .find(sanitize::strip(evidence))
            .is_some_and(|m| m.start() == 0 && m.end() == sanitize::strip(evidence).len())
    {
        return true;
    }
    PATTERNS.get(action).is_some_and(|p| p.is_match(evidence))
}
fn reject(reason: &str) -> Value {
    json!({"contract_errors":[],"step":null,"reason":reason})
}

/// Reuses the exact projection used for this attempt's prompt. The helper's
/// former request limit is retained even though validation performs no exchange.
pub(super) fn validate(frame: &Value, details: &Value) -> Result<Value> {
    ensure!(
        serde_json::to_vec(frame)?.len() < 1_000_000,
        "workshop validation request too large"
    );
    ensure!(details.is_object(), "workshop validation details object");
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
    let source = frame["text"].as_str().context("workshop raw text")?;
    ensure!(frame["workshop"].is_object(), "workshop snapshot object");
    let raw = frame.get("payload").context("workshop payload missing")?;
    if raw.is_object() {
        ensure!(
            !raw["action"].is_object() && !raw["action"].is_array(),
            "unhashable workshop action"
        );
    }
    let action = text(&raw["action"]);
    if raw.is_object() && strings(&ASSETS["followups"]).contains(action) {
        if !strings(&details["allowed_actions"]).contains(action) {
            return Ok(reject("action_not_allowed"));
        }
        let safe = raw["evidence"].as_str().is_some_and(|e| {
            workshop_input_guard::state_change_safe(
                if matches!(action, "confirm_close" | "decline_resume") {
                    "close_workshop"
                } else {
                    "stage_player_edit"
                },
                source,
                e,
            )
        }) && !(matches!(
            action,
            "confirm_close" | "acknowledge_meaning" | "resume_workshop" | "decline_resume"
        ) && PATTERNS["followup_negative"].is_match(source))
            && !(action == "resume_workshop" && PATTERNS["resume_negative"].is_match(source))
            && !(action == "acknowledge_meaning" && PATTERNS["ack_negative"].is_match(source));
        return Ok(if safe {
            json!({"contract_errors":[],"step":raw,"reason":"accepted"})
        } else {
            reject("unsafe_followup_evidence")
        });
    }
    let mut payload = raw.clone();
    if let Some(object) = raw.as_object() {
        payload = ASSETS["inactive"].clone();
        payload.as_object_mut().unwrap().extend(object.clone());
    }
    let mut details = details.clone();
    if matches!(
        action,
        "stage_player_edit"
            | "propose_revision"
            | "accept_pending"
            | "reject_pending"
            | "close_workshop"
    ) {
        details["player_text"] = details["original_player_text"].clone();
    }
    let errors = contract::errors(&payload, &details);
    if !errors.is_empty() {
        return Ok(json!({"contract_errors":errors,"step":null,"reason":"schema_contract_error"}));
    }
    let mut outcome = finalize(&payload, &details);
    if let Some(step) = outcome["step"].as_object() {
        let action = text(&step["action"]);
        let evidence = text(&step["evidence"]);
        if action == "stage_player_edit" && !explicit(action, source, evidence) {
            outcome = reject("player_edit_intent_not_explicit");
        } else if action == "stage_conversation_candidate"
            && !truth(&frame["workshop"]["conversation_candidate"])
        {
            outcome = reject("conversation_candidate_missing");
        } else if action == "propose_revision" {
            if step["confidence"].as_f64().unwrap_or(0.) < 0.85
                || !explicit(action, source, evidence)
            {
                outcome = reject("repair_intent_not_explicit");
            } else {
                let explicit = explicit_lines(source);
                let targets: BTreeSet<usize> = array(&step["analysis"]["findings"])
                    .iter()
                    .filter_map(|f| f["line_index"].as_u64().map(|n| n as usize))
                    .collect();
                if !explicit.is_empty() && !targets.is_subset(&explicit) {
                    outcome = reject("repair_target_conflict");
                }
            }
        }
    }
    Ok(outcome)
}

fn finalize(payload: &Value, d: &Value) -> Value {
    let action = sanitize::strip(text(&payload["action"]));
    if !strings(&ASSETS["actions"]).contains(action)
        || !strings(&d["allowed_actions"]).contains(action)
    {
        return reject("action_not_allowed");
    }
    if action == "defer_to_legacy" {
        return reject("deferred");
    }
    let purpose = sanitize::strip(text(&payload["purpose"]));
    if !strings(&ASSETS["purposes"]).contains(purpose) {
        return reject("purpose_not_allowed");
    }
    if let Some(required) = ASSETS["mutation_purposes"][action].as_str()
        && purpose != required
    {
        return reject("action_purpose_mismatch");
    }
    let confidence = payload["confidence"].as_f64().unwrap_or(0.);
    let threshold = if matches!(
        action,
        "accept_pending"
            | "reject_pending"
            | "close_workshop"
            | "stage_player_edit"
            | "stage_conversation_candidate"
    ) {
        0.85
    } else {
        0.72
    };
    if !(0.0..=1.0).contains(&confidence) || confidence < threshold {
        return reject("low_confidence");
    }
    let player = sanitize::strip(text(&d["player_text"]));
    let original = sanitize::strip(if truth(&d["original_player_text"]) {
        text(&d["original_player_text"])
    } else {
        player
    });
    let evidence = cut(&payload["evidence"], 120);
    if compact(&evidence).chars().count() < 2 || !compact(player).contains(&compact(&evidence)) {
        return reject("ungrounded_evidence");
    }
    if strings(&ASSETS["contradiction_actions"]).contains(action)
        && !workshop_input_guard::state_change_safe(action, original, &evidence)
    {
        return reject("unsafe_state_change_evidence");
    }
    if ASSETS["mutation_purposes"].get(action).is_some()
        && !compact(original).contains(&compact(&evidence))
    {
        return reject("mutation_evidence_not_in_original");
    }
    if ASSETS["positive"].get(action).is_some() && !positive(action, &evidence) {
        return reject("state_change_intent_not_explicit");
    }
    let close = payload["close_after_action"].as_bool().unwrap_or(false);
    let close_evidence = cut(&payload["close_evidence"], 120);
    if close {
        if !matches!(action, "accept_pending" | "reject_pending") {
            return reject("close_after_action_not_allowed");
        }
        if compact(&close_evidence).chars().count() < 2
            || !compact(player).contains(&compact(&close_evidence))
        {
            return reject("ungrounded_close_evidence");
        }
        if !workshop_input_guard::state_change_safe("close_workshop", original, &close_evidence) {
            return reject("unsafe_close_evidence");
        }
        if !compact(original).contains(&compact(&close_evidence)) {
            return reject("close_evidence_not_in_original");
        }
        if !positive("close_workshop", &close_evidence) {
            return reject("close_intent_not_explicit");
        }
    } else if !close_evidence.is_empty() {
        return reject("close_evidence_without_action");
    }
    let checks: Vec<_> = array(&payload["checks"])
        .iter()
        .filter_map(Value::as_str)
        .map(sanitize::strip)
        .filter(|s| !s.is_empty())
        .collect();
    if checks.len() != checks.iter().collect::<BTreeSet<_>>().len()
        || checks
            .iter()
            .any(|s| !matches!(*s, "reading" | "meter" | "source"))
    {
        return reject("invalid_checks");
    }
    if action == "inspect" && checks.is_empty() {
        return reject("inspection_check_required");
    }
    if action != "inspect" && !checks.is_empty() {
        return reject("checks_without_inspection");
    }
    let speech = sanitize::clean(text(&payload["speech"]));
    if strings(&ASSETS["direct"]).contains(action) {
        if speech.is_empty()
            || speech.contains('\n')
            || speech.chars().count() > 120
            || sanitize::usability_reason(&speech, &json!({})).is_some()
        {
            return reject("invalid_speech");
        }
        if has_markers(&speech, &ASSETS["unsaved"]) {
            return reject("false_persistence_claim");
        }
        let observation = &d["tool_observation"];
        if !(observation["kind"] == "revision_validation" && observation["status"] == "proposed")
            && has_markers(&speech, &ASSETS["unfinished"])
        {
            return reject("false_revision_claim");
        }
        let completed =
            if observation["kind"] == "inspection" && observation["status"] == "completed" {
                strings(&observation["checks"])
            } else {
                BTreeSet::new()
            };
        for (check, markers) in ASSETS["inspection_claims"].as_object().unwrap() {
            if !completed.contains(check.as_str()) && has_markers(&speech, markers) {
                return reject(&format!("unverified_{check}_claim"));
            }
        }
        if action != "ask"
            && ASSETS["inspection_requests"]
                .as_object()
                .unwrap()
                .iter()
                .any(|(check, markers)| {
                    !completed.contains(check.as_str()) && has_markers(player, markers)
                })
        {
            return reject("requested_inspection_not_completed");
        }
    } else if !speech.is_empty() {
        return reject("speech_not_allowed_for_action");
    }
    let analysis = analysis::finalize(payload, d, action, confidence, player);
    if action == "propose_revision"
        && array(&analysis["findings"]).is_empty()
        && !truth(&d["workshop_context"]["last_findings"])
    {
        return reject("repair_target_required");
    }
    if action == "stage_player_edit" {
        let p = &analysis["line_proposal"];
        if p.is_null() {
            return reject("grounded_line_proposal_required");
        }
        let replacement = compact(text(&p["replacement_text"]));
        let pe = compact(text(&p["evidence"]));
        let raw = compact(original);
        if replacement.is_empty()
            || pe.is_empty()
            || !raw.contains(&replacement)
            || !raw.contains(&pe)
        {
            return reject("player_edit_not_in_original");
        }
    }
    if action == "compare" && !truth(&d["pending_verse"]) {
        return reject("pending_required");
    }
    json!({"contract_errors":[],"reason":"accepted","step":{"action":action,"purpose":purpose,"confidence":confidence,"evidence":evidence,"speech":speech,"checks":checks,"close_after_action":close,"close_evidence":close_evidence,"analysis":analysis}})
}
