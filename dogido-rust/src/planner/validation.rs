use super::{Action, Details, Evidence, Plan, clean, repair, string};
use serde::Deserialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Payload {
    action: Action,
    focus: String,
    entity_query: String,
    evidence: Vec<Evidence>,
    confidence: f64,
    #[serde(default)]
    repair: Option<repair::RepairPayload>,
}

fn payload(value: &Value) -> Result<Payload, Vec<String>> {
    let p: Payload =
        serde_json::from_value(value.clone()).map_err(|_| vec!["payload:invalid_shape".into()])?;
    let mut errors = Vec::new();
    if p.focus.is_empty() {
        errors.push("focus:string_too_short".into());
    }
    if !(1..=3).contains(&p.evidence.len()) {
        errors.push("evidence:length_out_of_range".into());
    }
    for (i, e) in p.evidence.iter().enumerate() {
        for (key, text) in [("turn_id", &e.turn_id), ("quote", &e.quote)] {
            if text.is_empty() {
                errors.push(format!("evidence.{i}.{key}:string_too_short"));
            }
        }
    }
    if !(0.0..=1.0).contains(&p.confidence) {
        errors.push("confidence:out_of_range".into());
    }
    if let Some(r) = &p.repair {
        for (name, value, min, max) in [
            ("target_turn_id", &r.target_turn_id, 1, 180),
            ("target_quote", &r.target_quote, 1, 160),
            ("signal_quote", &r.signal_quote, 0, 160),
            ("replacement_quote", &r.replacement_quote, 0, 160),
        ] {
            if !(min..=max).contains(&value.chars().count()) {
                errors.push(format!("repair.{name}:length_out_of_range"));
            }
        }
    }
    if errors.is_empty()
        && (p.action.is_entity() == p.entity_query.is_empty()
            || p.action.is_repair() != p.repair.is_some())
    {
        errors.push("$:value_error".into());
    }
    if errors.is_empty() {
        Ok(p)
    } else {
        Err(errors)
    }
}

fn turns(details: &Details) -> HashMap<&str, &Value> {
    details
        .history
        .iter()
        .chain(std::iter::once(&details.current))
        .filter(|r| !string(r, "turn_id").is_empty())
        .map(|r| (string(r, "turn_id"), r))
        .collect()
}

/// JSON外形と発話内根拠。信頼度の採用閾値は後段で判定し、低信頼だけでは再生成しない。
pub fn contract_errors(value: &Value, details: &Details) -> Vec<String> {
    let p = match payload(value) {
        Ok(p) => p,
        Err(errors) => return errors,
    };
    let mut errors = Vec::new();
    if !details.allowed_actions.is_empty() && !details.allowed_actions.contains(&p.action) {
        errors.push("action:not_allowed".into());
    }
    let turns = turns(details);
    let mut seen = HashSet::new();
    for (index, row) in p.evidence.iter().enumerate() {
        if !seen.insert(row.turn_id.as_str()) {
            errors.push(format!("evidence.{index}.turn_id:duplicate"));
        }
        match turns.get(row.turn_id.as_str()) {
            None => errors.push(format!("evidence.{index}.turn_id:unknown")),
            Some(source) if !string(source, "text").contains(&row.quote) => {
                errors.push(format!("evidence.{index}.quote:not_exact"))
            }
            _ => {}
        }
    }
    if !seen.contains("current") {
        errors.push("evidence:current_required".into());
    }
    if !p.entity_query.is_empty() && !p.evidence.iter().any(|e| e.quote.contains(&p.entity_query)) {
        errors.push("entity_query:not_in_evidence".into());
    }
    if p.action == Action::CorrectPreviousReply
        && !p.evidence.iter().any(|e| {
            turns
                .get(e.turn_id.as_str())
                .is_some_and(|r| string(r, "role") == "assistant")
        })
    {
        errors.push("evidence:assistant_required".into());
    }
    if p.action.is_repair() {
        match p
            .repair
            .as_ref()
            .and_then(|r| repair::parse(p.action, r, details))
        {
            None => errors.push("repair:ungrounded".into()),
            Some(r)
                if !p.evidence.iter().any(|e| {
                    e.turn_id == r.target_turn_id && e.quote.contains(&r.target_quote)
                }) =>
            {
                errors.push("repair:target_evidence_required".into())
            }
            _ => {}
        }
    }
    let hints = &details.routing_hints;
    if (hints["inventory_question"] == true || hints["sound_question"] == true)
        && p.action != Action::AnswerObservation
    {
        errors.push("action:routing_hint_requires_answer_observation".into());
    }
    if hints["presence_question"] == true && p.action != Action::CheckEntityPresence {
        errors.push("action:routing_hint_requires_presence_check".into());
    }
    if hints["plain_presence_report"] == true
        && matches!(
            p.action,
            Action::CheckEntityPresence | Action::IdentifyEntity
        )
    {
        errors.push("action:routing_hint_preserves_player_report".into());
    }
    errors
}

pub fn parse_model_plan(value: &Value, details: &Details) -> Option<Plan> {
    if !contract_errors(value, details).is_empty() {
        return None;
    }
    let p = payload(value).ok()?;
    let threshold = if p.action == Action::CorrectPreviousReply || p.action.is_repair() {
        0.82
    } else if p.action.is_entity() {
        0.78
    } else {
        0.62
    };
    if p.confidence < threshold {
        return None;
    }
    let focus = clean(&p.focus, 80);
    let entity_query = clean(&p.entity_query, 120);
    if focus.is_empty() {
        return None;
    }
    let turns = turns(details);
    let mut seen = HashSet::new();
    let mut evidence = Vec::new();
    for row in p.evidence {
        let turn_id = clean(&row.turn_id, 180);
        let quote = clean(&row.quote, 160);
        if turn_id.is_empty()
            || !seen.insert(turn_id.clone())
            || quote.is_empty()
            || !string(turns.get(turn_id.as_str())?, "text").contains(&quote)
        {
            return None;
        }
        evidence.push(Evidence { turn_id, quote });
    }
    if !seen.contains("current") || !details.allowed_actions.contains(&p.action) {
        return None;
    }
    if p.action.is_entity()
        && (entity_query.is_empty() || !evidence.iter().any(|e| e.quote.contains(&entity_query)))
    {
        return None;
    }
    if !p.action.is_entity() && !entity_query.is_empty() {
        return None;
    }
    if p.action == Action::CorrectPreviousReply
        && !evidence.iter().any(|e| {
            turns
                .get(e.turn_id.as_str())
                .is_some_and(|r| string(r, "role") == "assistant")
        })
    {
        return None;
    }
    let repair = p
        .repair
        .as_ref()
        .and_then(|r| repair::parse(p.action, r, details));
    if let Some(r) = &repair
        && !evidence
            .iter()
            .any(|e| e.turn_id == r.target_turn_id && e.quote.contains(&r.target_quote))
    {
        return None;
    }
    Some(Plan {
        action: p.action,
        focus,
        entity_query,
        evidence,
        confidence: p.confidence,
        source: "model".into(),
        status: "accepted".into(),
        repair,
        presence_challenged: false,
    })
}

/// 前置き・コードフェンス後の最初のJSON objectをRustで読む。
/// 川柳検査には使わない（川柳は途中の内側objectを拾ってはいけない）。
pub fn extract_object(text: &str) -> Option<Value> {
    let trimmed = text.trim_matches(super::is_space);
    if let Ok(value) = serde_json::from_str::<Value>(trimmed)
        && value.is_object()
    {
        return Some(value);
    }
    for (index, _) in trimmed.match_indices('{') {
        if let Some(Ok(value)) = serde_json::Deserializer::from_str(&trimmed[index..])
            .into_iter::<Value>()
            .next()
            && value.is_object()
        {
            return Some(value);
        }
    }
    None
}
