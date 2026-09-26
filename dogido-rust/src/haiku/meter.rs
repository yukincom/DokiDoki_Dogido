//! Same kana/script, 5/7/5 +/- 1 and hard-term checks as llm/haiku.py.
use serde_json::{Map, Value};
pub const TARGETS: [usize; 3] = [5, 7, 5];
/// Python str.isspace()/re \s also includes the four C0 record separators.
fn space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
pub fn count_japanese_sounds(text: &str) -> usize {
    text.chars()
        .filter(|c| !space(*c))
        .enumerate()
        .map(|(i, c)| usize::from(i == 0 || !"ゃゅょャュョぁぃぅぇぉァィゥェォゎヮ".contains(c)))
        .sum()
}
pub fn normalize_term(text: &str) -> String {
    text.chars()
        .filter(|c| !space(*c))
        .map(|c| {
            if ('\u{30a1}'..='\u{30f6}').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect()
}
/// Does not inspect soft lessons or invent forbidden vocabulary.
pub fn line_failure_reasons(
    text: &str,
    line_index: usize,
    details: &Map<String, Value>,
) -> Vec<String> {
    if line_index > 2 {
        return vec!["invalid_line_index".into()];
    }
    if text.is_empty() {
        return vec!["empty_line".into()];
    }
    let mut reasons = Vec::new();
    if text.contains(['\n', '\r']) {
        reasons.push("multiline");
    }
    if !text.chars().all(|c| {
        ('\u{3041}'..='\u{309f}').contains(&c)
            || ('\u{30a1}'..='\u{30ff}').contains(&c)
            || space(c)
            || "ー／/|".contains(c)
    }) {
        reasons.push("invalid_script");
    }
    let compact: String = text.chars().filter(|c| !space(*c)).collect();
    if [
        "あいうえお",
        "かきくけこ",
        "さしすせそ",
        "たちつてと",
        "なにぬねの",
        "はひふへほ",
        "まみむめも",
        "やゆよ",
        "らりるれろ",
        "アイウエオ",
        "カキクケコ",
        "サシスセソ",
        "タチツテト",
        "ナニヌネノ",
        "ハヒフヘホ",
        "マミムメモ",
        "ヤユヨ",
        "ラリルレロ",
    ]
    .iter()
    .any(|s| compact.contains(s))
    {
        reasons.push("gibberish_sequence");
    }
    let count = count_japanese_sounds(text);
    let target = TARGETS[line_index];
    if count < target - 1 {
        reasons.push("meter_too_short");
    } else if count > target + 1 {
        reasons.push("meter_too_long");
    }
    if let Some(terms) = details
        .get("haiku_constraints")
        .and_then(|v| v.get("forbidden_terms"))
        .and_then(Value::as_array)
    {
        let normalized = normalize_term(text);
        if terms
            .iter()
            .filter(|v| super::generation::truthy(v))
            .map(|v| {
                normalize_term(
                    v.as_str()
                        .map(str::to_owned)
                        .unwrap_or_else(|| v.to_string())
                        .as_str(),
                )
            })
            .any(|t| !t.is_empty() && normalized.contains(&t))
        {
            reasons.push("hard_forbidden_term");
        }
    }
    reasons.into_iter().map(str::to_owned).collect()
}
