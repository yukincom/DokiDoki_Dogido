use super::{has_kanji, space};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::HashMap, sync::LazyLock};

static PREFERRED: LazyLock<HashMap<String, String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("preferred.json")).expect("checked TTS preferred readings")
});
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Token {
    pub surface: String,
    pub goshu: Option<String>,
    pub pos1: Option<String>,
    pub kana: Option<String>,
    pub pron: Option<String>,
}
/// Full dictionary reading also used for neutral poem normalization. This does
/// not apply TTS residual replacements or change words without kanji.
pub fn neutral(tokens: &[Token]) -> String {
    tokens
        .iter()
        .map(|token| {
            if !has_kanji(&token.surface) {
                return token.surface.clone();
            }
            let reading = token_reading(token);
            if reading.is_empty() {
                token.surface.clone()
            } else {
                reading
            }
        })
        .collect()
}
fn token_reading(token: &Token) -> String {
    if let Some(preferred) = PREFERRED.get(&token.surface) {
        return preferred.clone();
    }
    token
        .kana
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(token.pron.as_deref())
        .unwrap_or("")
        .trim_matches(space)
        .chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect()
}
/// The optional dictionary owns segmentation. Never reconstruct spaces it omitted.
pub fn apply(tokens: &[Token]) -> String {
    tokens
        .iter()
        .map(|token| {
            if !has_kanji(&token.surface) {
                return token.surface.clone();
            }
            if let Some(preferred) = PREFERRED.get(&token.surface) {
                return preferred.clone();
            }
            let reading = token
                .kana
                .as_deref()
                .filter(|s| !s.is_empty())
                .or(token.pron.as_deref())
                .unwrap_or("")
                .trim_matches(space);
            if !matches!(token.goshu.as_deref(), Some("和" | "混"))
                || matches!(
                    token.pos1.as_deref(),
                    Some("助詞" | "助動詞" | "補助記号" | "記号" | "空白")
                )
                || reading.is_empty()
            {
                return token.surface.clone();
            }
            reading
                .chars()
                .map(|c| {
                    if ('ァ'..='ヶ').contains(&c) {
                        char::from_u32(c as u32 - 0x60).unwrap()
                    } else {
                        c
                    }
                })
                .collect()
        })
        .collect()
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Ok,
    Unavailable,
    ParseError,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Response {
    schema_version: u32,
    request_id: String,
    status: Status,
    tokens: Vec<Token>,
}
/// Only explicit SDK failure becomes None. Malformed/mismatched IPC is an error.
pub fn decode(frame: Value, request_id: &str) -> Result<Option<String>> {
    Ok(decode_tokens(frame, request_id)?.map(|tokens| apply(&tokens)))
}
pub fn decode_tokens(frame: Value, request_id: &str) -> Result<Option<Vec<Token>>> {
    if let Some(rows) = frame["tokens"].as_array() {
        ensure!(
            rows.iter()
                .all(|r| r.as_object().is_some_and(|r| r.len() == 5
                    && ["surface", "goshu", "pos1", "kana", "pron"]
                        .iter()
                        .all(|k| r.contains_key(*k)))),
            "incomplete unidic token frame"
        );
    }
    let response: Response = serde_json::from_value(frame)?;
    ensure!(
        response.schema_version == 1 && response.request_id == request_id,
        "unidic response identity mismatch"
    );
    match response.status {
        Status::Ok => Ok(Some(response.tokens)),
        Status::Unavailable | Status::ParseError => {
            ensure!(
                response.tokens.is_empty(),
                "failed unidic response contains tokens"
            );
            tracing::info!(event="tts_unidic_fallback",status=?response.status);
            Ok(None)
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn canonical_token_selection_and_conversion_match() {
        let cases: Vec<Value> = serde_json::from_str(include_str!("token-fixtures.json")).unwrap();
        for c in cases {
            let tokens: Vec<Token> = serde_json::from_value(c["tokens"].clone()).unwrap();
            assert_eq!(apply(&tokens), c["expected"], "{c}");
        }
    }
    #[test]
    fn failed_sdk_is_separate_from_protocol_failure_and_empty_success() {
        for status in ["unavailable", "parse_error"] {
            assert_eq!(
                decode(
                    json!({"schema_version":1,"request_id":"r","status":status,"tokens":[]}),
                    "r"
                )
                .unwrap(),
                None
            );
        }
        assert_eq!(
            decode(
                json!({"schema_version":1,"request_id":"r","status":"ok","tokens":[]}),
                "r"
            )
            .unwrap(),
            Some(String::new())
        );
        for frame in [
            json!({"error":"bad frame"}),
            json!({"schema_version":1,"request_id":"old","status":"ok","tokens":[]}),
            json!({"schema_version":1,"request_id":"r","status":"failed","tokens":[]}),
            json!({"schema_version":1,"request_id":"r","status":"unavailable","tokens":[{"surface":"猫","kana":null,"pron":null,"goshu":null,"pos1":null}]}),
            json!({"schema_version":1,"request_id":"r","status":"ok","tokens":[{"surface":9}]}),
        ] {
            assert!(decode(frame, "r").is_err());
        }
    }
}
