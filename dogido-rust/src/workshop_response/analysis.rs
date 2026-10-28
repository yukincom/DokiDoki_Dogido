//! モデルの解釈を記録へ投影する。原文一致・確信度・表現で落とさない。
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
fn reference(raw: &Value) -> Value {
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
    // 呼称の意味はモデルが返した既知の行概念を受け取る。原文の別の行名で上書きしない。
    let mut result = concept.clone();
    result.as_object_mut().unwrap().remove("reference_examples");
    result["evidence"] = json!(evidence);
    result["confidence"] = json!(confidence);
    result
}
pub(super) fn finalize(payload: &Value, d: &Value, action: &str, confidence: f64) -> Value {
    let intent = match action {
        "explain" => "ask_meaning",
        "show_current" => "show_current",
        "stage_player_edit" => "propose_line_edit",
        "propose_revision" => "request_repair",
        _ => "other_haiku",
    };
    let reference = reference(&payload["line_reference"]);
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
        let index = row["line_index"]
            .as_u64()
            .map(|n| n as usize)
            .or_else(|| matching(&verse, &fragment));
        let problem = sanitize::strip(text(&row["problem"]));
        let problem = if strings(&ASSETS["problems"]).contains(problem) {
            problem
        } else {
            "other"
        };
        let confidence = row["confidence"].as_f64().unwrap_or(0.);
        findings.push(json!({"line_index":index,"fragment":fragment,"problem":problem,"note":cut(&row["note"],120),"confidence":confidence}));
    }
    let raw = &payload["line_proposal"];
    let mut proposal = Value::Null;
    if raw["found"] == true {
        let replacement = text(&raw["replacement_text"]).trim();
        let fragment = text(&raw["target_fragment"]).trim();
        let evidence = cut(&raw["evidence"], 120);
        let confidence = raw["confidence"].as_f64().unwrap_or(0.);
        let target = matching(&verse, fragment);
        let concept = reference["line_index"].as_u64().map(|n| n as usize);
        // 行が明示されていればその解釈を保持する。実際の置換位置は編集時に照合する。
        if !replacement.is_empty() {
            proposal = json!({"replacement_text":replacement,"target_fragment":fragment,"line_index":concept.or(target),"evidence":evidence,"confidence":confidence});
        }
    }
    json!({"intent":intent,"confidence":confidence,"repair_requested":action=="propose_revision","findings":findings,"line_proposal":proposal,"line_reference":reference,"close_request":null,"evaluation":null})
}
