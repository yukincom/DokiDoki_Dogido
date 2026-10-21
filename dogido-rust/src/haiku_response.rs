//! 自動川柳・修正案のモデル返答からJSON objectを取り出し、内容検査へ渡す入口。
//! 完成objectを抽出し、候補の空行・折返し補完と音数確認は生成器が行う。
//! 抽出できなければ、呼び手のfallbackにinvalid_json/output_truncatedを付けて返す。
//! acceptedはobjectを取得できたという意味。句・判定・根拠の採否は後続のドメイン検査が決める。
//! 生成再試行は呼び手が所有し、ここでは欠けた根拠の補完やschemaの修復を行わない。
//! 非有限数・対にならないUnicode surrogate・過度な深さはserde_jsonの制限に従って棄却する。
use crate::{text_format::strip_code_fence, types::GeneratedText};
use anyhow::{Result, ensure};
use serde_json::{Map, Value};

const STATUS: &str = "__dogido_status";
const KINDS: &[&str] = &[
    "haiku_draft",
    "haiku_workshop_revision",
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
    let payload = extract_object(&generated.text, true);
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

fn raw_value(text: &str) -> Option<(Value, usize)> {
    // 途中で切れた返答から値の有効な先頭部分を回収する。trueXXXならtrue、1eなら1まで。
    // StreamDeserializerは後続の区切りも要求するため、literalと数値は先に長さを確定する。
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn generated(text: &str, finish: Option<&str>) -> GeneratedText {
        GeneratedText {
            text: text.into(),
            finish_reason: finish.map(str::to_owned),
            completion_tokens: Some(12),
            prompt_tokens: Some(40),
        }
    }
    #[test]
    fn completed_json_and_embedded_objects_keep_domain_payload() {
        for kind in KINDS {
            for text in [
                r#"{"lines":["草","黒","影"]}"#,
                "前置き {\"lines\":[\"草\",\"黒\",\"影\"]} 後書き",
                "```json\n{\"lines\":[\"草\",\"黒\",\"影\"]}\n```",
            ] {
                let out = parse(kind, &generated(text, Some("length")), &json!({})).unwrap();
                assert_eq!(out[STATUS], "accepted");
                assert_eq!(out["lines"], json!(["草", "黒", "影"]));
            }
        }
    }
    #[test]
    fn incomplete_output_keeps_transport_failure_separate_from_poem_text() {
        let fallback = json!({"lines":[],"kept":true});
        for (finish, status) in [
            (Some("length"), "output_truncated"),
            (Some("stop"), "invalid_json"),
            (None, "invalid_json"),
        ] {
            let out = parse("haiku_draft", &generated("{", finish), &fallback).unwrap();
            assert_eq!(out[STATUS], status);
            assert_eq!(out["kept"], true);
        }
        assert!(fallback.get(STATUS).is_none());
    }
    #[test]
    fn unsupported_evaluation_and_nonobject_fallback_are_rejected() {
        assert!(parse("haiku_line_grounding", &generated("{}", None), &json!({})).is_err());
        assert!(parse("haiku_draft", &generated("{}", None), &json!([])).is_err());
    }
    #[test]
    fn outer_object_is_not_replaced_with_an_inner_line_object() {
        let out = parse(
            "haiku_draft",
            &generated(r#"{"outer":{"lines":[]}}"#, None),
            &json!({}),
        )
        .unwrap();
        assert!(out.get("outer").is_some());
        assert!(out.get("lines").is_none());
    }
}
