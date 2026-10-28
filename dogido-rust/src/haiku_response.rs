//! 自動川柳・修正案のモデル返答からJSON objectを取り出す入口。
//! 音数確認は自動発句だけ。川柳相談の修正案は表記を保持し、対象・元句・版へ適用する。
//! 抽出できなければ、呼び手のfallbackにinvalid_json/output_truncatedを付けて返す。
//! acceptedはobjectを取得できたという意味。各consumerが自分の実行データへ変換する。
//! 生成再試行は呼び手が所有し、ここでは欠けた根拠の補完やschemaの修復を行わない。
//! 非有限数・対にならないUnicode surrogate・過度な深さはserde_jsonの制限に従って棄却する。
use crate::{
    text_format::{extract_object, strip_code_fence},
    types::GeneratedText,
};
use anyhow::{Result, ensure};
use serde_json::Value;

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
    let payload = extract_object(&strip_code_fence(&generated.text));
    if let Some(Value::Object(mut object)) = payload {
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
