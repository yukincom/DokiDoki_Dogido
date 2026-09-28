//! Transport parsing for automatic haiku and domain-validated workshop revisions.
//!
//! Mirrors `DogidoLLM.generate_structured_json`, `_extract_json_object`, and
//! `_extract_grounding_prefix`. `accepted` means an object was received, not
//! that its lines, verdicts, or evidence passed the downstream domain checks.
//! No model retries, evidence completion, or schema repair happen here.
//!
//! serde_json deliberately keeps its standard JSON limits: non-finite numbers,
//! unpaired Unicode surrogates, and excessively deep values are not accepted.
//! Python's JSON decoder is more permissive for those non-domain inputs.
use crate::types::GeneratedText;
use anyhow::{Result, ensure};
use serde_json::{Map, Value};

const STATUS: &str = "__dogido_status";
const KINDS: &[&str] = &[
    "haiku_draft",
    "haiku_workshop_revision",
    "haiku_line_grounding",
    "haiku_line_regeneration",
    "haiku_irony",
    "haiku_scene",
];

/// Parse one completed provider response without invoking another generation.
/// Invalid/truncated output returns the caller's fallback with a transport
/// status. Unsupported kinds and non-object fallbacks are programming errors.
pub fn parse(kind: &str, generated: &GeneratedText, fallback: &Value) -> Result<Value> {
    ensure!(
        KINDS.contains(&kind),
        "unsupported haiku response kind: {kind}"
    );
    let fallback = fallback
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("haiku fallback must be a JSON object"))?;
    tracing::warn!(
        event = "llm_completion",
        kind,
        finish_reason = generated.finish_reason.as_deref().unwrap_or("unknown"),
        completion_tokens = ?generated.completion_tokens,
        prompt_tokens = ?generated.prompt_tokens,
    );
    let grounding = kind == "haiku_line_grounding";
    let mut payload = extract_object(&generated.text, !grounding);
    if grounding && payload.is_none() {
        payload = grounding_prefix(&generated.text);
        if payload.is_some() {
            tracing::warn!(
                event = "llm_structured",
                kind,
                result = "complete_fields_recovered"
            );
        }
    }
    if let Some(mut object) = payload {
        object.insert(STATUS.into(), "accepted".into());
        tracing::warn!(
            event = "llm_structured",
            kind,
            result = "accepted",
            validation = "haiku_domain"
        );
        return Ok(Value::Object(object));
    }
    let status = if matches!(
        generated.finish_reason.as_deref(),
        Some("length" | "max_tokens" | "MAX_TOKENS")
    ) {
        "output_truncated"
    } else {
        "invalid_json"
    };
    let mut object = fallback.clone();
    object.insert(STATUS.into(), status.into());
    tracing::warn!(
        event = "llm_structured",
        kind,
        result = "fallback",
        reason = status
    );
    Ok(Value::Object(object))
}

pub(crate) fn extract_object(text: &str, allow_nested: bool) -> Option<Map<String, Value>> {
    let normalized = strip_code_fence(text);
    if let Ok(Value::Object(object)) = serde_json::from_str(&normalized) {
        return Some(object);
    }
    if allow_nested {
        for (index, _) in normalized.match_indices('{') {
            if let Some((Value::Object(object), _)) = raw_value(&normalized[index..]) {
                return Some(object);
            }
        }
    }
    None
}

/// Only completed outer members are eligible; never scan inside assessments.
/// Check the key for repetition before decoding its value, so an unfinished
/// second `verdicts` cannot make the first verdicts look authoritative.
fn grounding_prefix(text: &str) -> Option<Map<String, Value>> {
    let normalized = strip_code_fence(text);
    let normalized = if normalized.starts_with("```") {
        normalized
            .split_once('\n')
            .map_or(normalized.as_str(), |(_, rest)| trim_start(rest))
    } else {
        &normalized
    };
    let mut rest = normalized.strip_prefix('{')?;
    let mut fields = Map::new();
    while !rest.is_empty() {
        rest = trim_start(rest);
        let Some((key, consumed)) = raw_value(rest) else {
            break;
        };
        let key = key.as_str()?;
        if fields.contains_key(key) {
            return None;
        }
        rest = trim_start(&rest[consumed..]);
        rest = trim_start(rest.strip_prefix(':')?);
        let Some((value, consumed)) = raw_value(rest) else {
            break;
        };
        fields.insert(key.into(), value);
        rest = trim_start(&rest[consumed..]);
        let Some(next) = rest.strip_prefix(',') else {
            break;
        };
        rest = next;
    }
    (fields.get("verdicts").is_some_and(Value::is_object)
        && fields.get("assessments").is_some_and(Value::is_array))
    .then_some(fields)
}

fn raw_value(text: &str) -> Option<(Value, usize)> {
    // Python raw_decode ends literals/numbers at their valid prefix, even if
    // the following text is not a JSON delimiter (e.g. `trueXXX` or `1e`).
    // StreamDeserializer checks that delimiter, so handle these tokens first.
    for (token, value) in [
        ("true", Value::Bool(true)),
        ("false", Value::Bool(false)),
        ("null", Value::Null),
    ] {
        if text.starts_with(token) {
            return Some((value, token.len()));
        }
    }
    if let Some(end) = number_end(text.as_bytes()) {
        return Some((serde_json::from_str(&text[..end]).ok()?, end));
    }
    let mut decoder = serde_json::Deserializer::from_str(text).into_iter::<Value>();
    let value = decoder.next()?.ok()?;
    Some((value, decoder.byte_offset()))
}

fn number_end(bytes: &[u8]) -> Option<usize> {
    let mut end = usize::from(bytes.first() == Some(&b'-'));
    match bytes.get(end)? {
        b'0' => end += 1,
        b'1'..=b'9' => {
            end += 1;
            while bytes.get(end).is_some_and(u8::is_ascii_digit) {
                end += 1;
            }
        }
        _ => return None,
    }
    if bytes.get(end) == Some(&b'.') && bytes.get(end + 1).is_some_and(u8::is_ascii_digit) {
        end += 2;
        while bytes.get(end).is_some_and(u8::is_ascii_digit) {
            end += 1;
        }
    }
    if matches!(bytes.get(end), Some(b'e' | b'E')) {
        let mut next = end + 1;
        if matches!(bytes.get(next), Some(b'+' | b'-')) {
            next += 1;
        }
        if bytes.get(next).is_some_and(u8::is_ascii_digit) {
            end = next + 1;
            while bytes.get(end).is_some_and(u8::is_ascii_digit) {
                end += 1;
            }
        }
    }
    Some(end)
}

fn is_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{001c}'..='\u{001f}')
}

fn trim_start(text: &str) -> &str {
    text.trim_start_matches(is_space)
}

fn strip_code_fence(text: &str) -> String {
    let text = text.trim_matches(is_space);
    if !text.starts_with("```") {
        return text.into();
    }
    // Python str.splitlines also recognizes these Unicode line boundaries.
    let lines: Vec<_> = text
        .split([
            '\n', '\r', '\u{000b}', '\u{000c}', '\u{001c}', '\u{001d}', '\u{001e}', '\u{0085}',
            '\u{2028}', '\u{2029}',
        ])
        .collect();
    if lines.len() >= 2
        && lines
            .last()
            .is_some_and(|line| line.trim_matches(is_space) == "```")
    {
        return lines[1..lines.len() - 1]
            .join("\n")
            .trim_matches(is_space)
            .into();
    }
    text.into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const PREFIX: &str = r#"{"verdicts":{"0":"pass","1":"meaning_fail","2":"pass"},"assessments":[{"line_index":0,"atom_ids":[1]},{"line_index":1,"atom_ids":[]},{"line_index":2,"atom_ids":[3]}]"#;

    fn generated(text: &str, finish: Option<&str>) -> GeneratedText {
        GeneratedText {
            text: text.into(),
            finish_reason: finish.map(str::to_owned),
            completion_tokens: Some(512),
            prompt_tokens: Some(1234),
        }
    }
    fn grounding(raw: &str, finish: Option<&str>) -> Value {
        parse(
            "haiku_line_grounding",
            &generated(raw, finish),
            &json!({"assessments":[]}),
        )
        .unwrap()
    }

    #[test]
    fn all_supported_kinds_defer_content_validation_to_the_domain() {
        for kind in KINDS {
            let output = parse(
                kind,
                &generated(
                    r#"{"unexpected":true,"__dogido_status":"invalid_json"}"#,
                    None,
                ),
                &json!({}),
            )
            .unwrap();
            assert_eq!(output, json!({"unexpected":true,STATUS:"accepted"}));
        }
    }

    #[test]
    fn unsupported_kinds_and_non_object_fallback_are_errors() {
        let response = generated("{}", None);
        for kind in ["player_chat_plan", "", "haiku_draft "] {
            assert!(parse(kind, &response, &json!({})).is_err());
        }
        for value in [Value::Null, json!([]), json!(1), json!("{}")] {
            assert!(parse("haiku_draft", &response, &value).is_err());
        }
    }

    #[test]
    fn complete_report_is_accepted_even_when_provider_says_length() {
        let raw = format!(r#"{PREFIX},"failure_reasons":{{"1":"材料がない。"}}}}"#);
        let result = grounding(&raw, Some("length"));
        assert_eq!(result[STATUS], "accepted");
        assert_eq!(result["failure_reasons"]["1"], "材料がない。");
        assert_eq!(result["assessments"][1]["atom_ids"], json!([]));
    }

    #[test]
    fn explanation_truncation_keeps_only_completed_outer_fields() {
        let raw = format!(r#"{PREFIX},"failure_reasons":{{"1":"途中"#);
        for finish in [Some("length"), Some("stop"), None] {
            let result = grounding(&raw, finish);
            assert_eq!(result[STATUS], "accepted");
            assert_eq!(result["verdicts"]["1"], "meaning_fail");
            assert_eq!(result["assessments"].as_array().unwrap().len(), 3);
            assert!(result.get("failure_reasons").is_none());
            assert!(result["assessments"][1].get("meaning_retained").is_none());
        }
    }

    #[test]
    fn incomplete_evidence_never_becomes_an_inner_assessment() {
        for raw in [
            r#"{"verdicts":{"0":"pass","1":"pass","2":"pass"},"assessments":["#,
            r#"{"assessments":[{"line_index":0,"atom_ids":[1],"meaning_retained":true,"natural_japanese":true},"#,
            r#"{"verdicts":{"0":"pass"},"assessments":[{"line_index":0,"atom_ids":[1]},"#,
            r#"{"verdicts":{"0":"pass"},"assessments":[{"line_index":0,"atom_ids":["#,
        ] {
            assert_eq!(
                grounding(raw, Some("length")),
                json!({"assessments":[],STATUS:"output_truncated"})
            );
        }
    }

    #[test]
    fn duplicate_prefix_keys_reject_even_before_the_second_value() {
        for suffix in [
            r#", "verdicts":"#,
            r#", "verdicts""#,
            r#", "verdicts": {"0":"meaning_fail"}, "failure_reasons":"#,
            r#", "assessments": ["#,
            r#", "\u0076erdicts":"#,
            r#", "other":1,"other":"#,
        ] {
            assert_eq!(
                grounding(&format!("{PREFIX}{suffix}"), Some("length"))[STATUS],
                "output_truncated",
                "{suffix}"
            );
        }
    }

    #[test]
    fn missing_or_wrong_typed_complete_fields_do_not_salvage() {
        for raw in [
            r#"{"verdicts":{"0":"pass"},"failure_reasons":"#,
            r#"{"assessments":[],"failure_reasons":"#,
            r#"{"verdicts":[],"assessments":[],"failure_reasons":"#,
            r#"{"verdicts":{},"assessments":{},"failure_reasons":"#,
        ] {
            assert_eq!(grounding(raw, None)[STATUS], "invalid_json");
        }
    }

    #[test]
    fn grounding_prefix_does_not_invent_a_missing_line_or_reason() {
        let result = grounding(
            r#"{"verdicts":{"1":"pass"},"assessments":[],"failure_reasons":"#,
            Some("length"),
        );
        assert_eq!(
            result,
            json!({"verdicts":{"1":"pass"},"assessments":[],STATUS:"accepted"})
        );
    }

    #[test]
    fn complete_fences_and_open_fence_grounding_prefix_match_python() {
        for separator in ["\n", "\r\n", "\u{2028}"] {
            let raw = format!("  ```json{separator}{PREFIX}}}{separator}```  ");
            assert_eq!(grounding(&raw, None)[STATUS], "accepted");
        }
        let raw = format!("```json\n{PREFIX},\"failure_reasons\":{{\"0\":\"途");
        assert_eq!(grounding(&raw, Some("length"))[STATUS], "accepted");
        assert_eq!(
            grounding("```json\n{\"line_index\":0}", Some("length"))[STATUS],
            "output_truncated"
        );
    }

    #[test]
    fn only_non_grounding_kinds_scan_nested_objects_and_surrounding_prose() {
        for raw in [
            "前置き {\"lines\":[\"草\",\"黒\",\"影\"]} 後書き",
            "{\"broken\": [ {\"lines\":[\"草\",\"黒\",\"影\"]}",
            "[{\"lines\":[\"草\",\"黒\",\"影\"]}]",
        ] {
            for kind in [
                "haiku_draft",
                "haiku_line_regeneration",
                "haiku_irony",
                "haiku_scene",
            ] {
                let result = parse(kind, &generated(raw, None), &json!({})).unwrap();
                assert_eq!(result["lines"], json!(["草", "黒", "影"]));
                assert_eq!(result[STATUS], "accepted");
            }
            assert_eq!(grounding(raw, None)[STATUS], "invalid_json");
        }
    }

    #[test]
    fn complete_outer_object_is_preferred_to_inner_objects() {
        let result = parse(
            "haiku_draft",
            &generated(r#"{"outer":{"lines":[]}}"#, None),
            &json!({}),
        )
        .unwrap();
        assert_eq!(result, json!({"outer":{"lines":[]},STATUS:"accepted"}));
    }

    #[test]
    fn truncation_status_depends_only_on_known_finish_reasons() {
        for finish in ["length", "max_tokens", "MAX_TOKENS"] {
            assert_eq!(grounding("{", Some(finish))[STATUS], "output_truncated");
        }
        for finish in [None, Some("stop"), Some("LENGTH"), Some("unknown")] {
            assert_eq!(grounding("{", finish)[STATUS], "invalid_json");
        }
        for raw in ["", " ", "null", "true", "23", "[]", "\"text\""] {
            assert_eq!(grounding(raw, None)[STATUS], "invalid_json");
        }
    }

    #[test]
    fn fallback_is_cloned_and_status_is_overwritten() {
        let fallback = json!({"assessments":[],"nested":{"kept":true},STATUS:"old"});
        let saved = fallback.clone();
        let result = parse(
            "haiku_line_grounding",
            &generated("{", Some("length")),
            &fallback,
        )
        .unwrap();
        assert_eq!(fallback, saved);
        assert_eq!(result["nested"], fallback["nested"]);
        assert_eq!(result[STATUS], "output_truncated");
    }

    #[test]
    fn completed_json_duplicate_keys_follow_python_last_value_wins() {
        // The duplicate-key guard applies to recovery, not ordinary json.loads.
        let result = grounding(
            r#"{"verdicts":{"0":"pass"},"verdicts":{"0":"meaning_fail"},"assessments":[]}"#,
            None,
        );
        assert_eq!(result["verdicts"]["0"], "meaning_fail");
        assert_eq!(result[STATUS], "accepted");
    }

    #[test]
    fn prefix_delimiters_and_utf8_strings_are_not_repaired() {
        let prefix = r#"{"verdicts":{"0":"pass"},"assessments":[],"note":"引用\\\" と { } 日本語""#;
        assert_eq!(
            grounding(&format!("{prefix},\"failure_reasons\":"), None)["note"],
            "引用\\\" と { } 日本語"
        );
        for suffix in [",\"bad\" 0", ",42:0"] {
            assert_eq!(
                grounding(&format!("{PREFIX}{suffix}"), None)[STATUS],
                "invalid_json"
            );
        }
    }

    #[test]
    fn raw_primitive_prefixes_match_python_without_repairing_tokens() {
        for (token, value) in [
            ("trueXXX", json!(true)),
            ("falsex", json!(false)),
            ("nullx", Value::Null),
            ("1x", json!(1)),
            ("1e", json!(1)),
            ("1e+", json!(1)),
            ("01", json!(0)),
            ("0.", json!(0)),
            ("-12.5e-2x", json!(-0.125)),
        ] {
            let result = grounding(&format!("{PREFIX},\"note\":{token}"), None);
            assert_eq!(result["note"], value, "{token}");
            assert_eq!(result[STATUS], "accepted");
        }
    }
}
