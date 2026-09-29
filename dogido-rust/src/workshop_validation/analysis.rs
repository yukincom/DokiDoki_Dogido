//! Only the pure analysis projection called by finalize_workshop_agent_step.
//! Its evaluation and close_request are always found=false at that call site.
use super::*;
fn compact_kana(text: &str) -> String {
    text.chars()
        .map(|c| {
            if ('\u{30a1}'..='\u{30f6}').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .filter(|c| !"\n 　、。？?！!「」『』".contains(*c))
        .collect()
}
fn lines(text: &str) -> Vec<&str> {
    let text = sanitize::strip(text);
    let lines: Vec<_> = text
        .split([
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ])
        .map(sanitize::strip)
        .filter(|s| !s.is_empty())
        .collect();
    if lines.len() == 1 {
        let parts: Vec<_> = text
            .split(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
            .filter(|s| !s.is_empty())
            .collect();
        if parts.len() == 3 {
            return parts;
        }
    }
    lines
}
fn matching(verse: &[&str], fragment: &str) -> Option<usize> {
    let folded = compact_kana(fragment);
    let indices: Vec<_> = verse
        .iter()
        .enumerate()
        .filter_map(|(index, line)| {
            (!folded.is_empty() && compact_kana(line).contains(&folded)).then_some(index)
        })
        .collect();
    if indices.len() == 1 {
        Some(indices[0])
    } else {
        None
    }
}
fn reference(raw: &Value, player: &str) -> Value {
    if raw["found"] != true {
        return Value::Null;
    }
    let concept = sanitize::strip(text(&raw["concept_id"]));
    let Some(concept) = array(&ASSETS["concepts"])
        .iter()
        .find(|c| c["concept_id"] == concept)
    else {
        return Value::Null;
    };
    let confidence = raw["confidence"].as_f64().unwrap_or(0.);
    let evidence = cut(&raw["evidence"], 48);
    let ec = compact_kana(&evidence);
    if !(0.0..=1.0).contains(&confidence)
        || confidence < 0.75
        || ec.chars().count() < 2
        || !compact_kana(player).contains(&ec)
    {
        return Value::Null;
    }
    let explicit = explicit_lines(player);
    let ev = explicit_lines(&evidence);
    let index = concept["line_index"].as_u64().unwrap() as usize;
    if explicit.len() > 1
        || (ev.len() == 1 && !ev.contains(&index))
        || (!explicit.is_empty() && !explicit.contains(&index))
    {
        return Value::Null;
    }
    let mut result = concept.clone();
    result.as_object_mut().unwrap().remove("reference_examples");
    result["evidence"] = json!(evidence);
    result["confidence"] = json!(confidence);
    result
}
pub(super) fn finalize(
    payload: &Value,
    d: &Value,
    action: &str,
    confidence: f64,
    player: &str,
) -> Value {
    let intent = match action {
        "explain" => "ask_meaning",
        "show_current" => "show_current",
        "stage_player_edit" => "propose_line_edit",
        "propose_revision" => "request_repair",
        _ => "other_haiku",
    };
    let intent = if confidence >= 0.75 {
        intent
    } else {
        "soft_default"
    };
    let reference = reference(&payload["line_reference"], player);
    let verse = lines(text(&d["working_reading"]));
    let mut findings = Vec::new();
    for row in array(&payload["findings"]).iter().take(3) {
        if !row.is_object() {
            continue;
        }
        if let Some(index) = row["line_index"].as_u64() {
            if index >= 3 || index >= verse.len() as u64 {
                continue;
            }
        } else if !row["line_index"].is_null() {
            continue;
        }
        let fragment = cut(&row["fragment"], 40);
        let index = matching(&verse, &fragment);
        let problem = sanitize::strip(text(&row["problem"]));
        let confidence = row["confidence"].as_f64().unwrap_or(0.);
        if !strings(&ASSETS["problems"]).contains(problem)
            || !(0.0..=1.0).contains(&confidence)
            || confidence < 0.65
        {
            continue;
        }
        findings.push(json!({"line_index":index,"fragment":fragment,"problem":problem,"note":cut(&row["note"],120),"confidence":confidence}));
    }
    let raw = &payload["line_proposal"];
    let mut proposal = Value::Null;
    if raw["found"] == true {
        let replacement = cut(&raw["replacement_text"], 48);
        let fragment = cut(&raw["target_fragment"], 48);
        let evidence = cut(&raw["evidence"], 120);
        let confidence = raw["confidence"].as_f64().unwrap_or(0.);
        let pc = compact_kana(player);
        let rc = compact_kana(&replacement);
        let ec = compact_kana(&evidence);
        let target = matching(&verse, &fragment);
        let concept = reference["line_index"].as_u64().map(|n| n as usize);
        let conflict = target.is_some() && concept.is_some() && target != concept;
        if (0.0..=1.0).contains(&confidence)
            && confidence >= 0.75
            && !rc.is_empty()
            && !pc.is_empty()
            && pc.contains(&rc)
            && ec.chars().count() >= 2
            && pc.contains(&ec)
            && !conflict
        {
            proposal = json!({"replacement_text":replacement,"target_fragment":fragment,"line_index":target.or(concept),"evidence":evidence,"confidence":confidence});
        }
    }
    json!({"intent":intent,"confidence":confidence,"repair_requested":action=="propose_revision","findings":findings,"line_proposal":proposal,"line_reference":reference,"close_request":null,"evaluation":null})
}
