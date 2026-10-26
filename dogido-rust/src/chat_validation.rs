//! 通常対話の出力を、本文または発話なしへ整える。再生成や内容の正誤判定はしない。
//! JSON末尾と単純な型違いだけを補完し、空・文字化け・反復は既存の共通検査へ渡す。
//! 履歴と実再生の確定はdialogue側が担当し、不成立の本文は返さない。
use serde_json::Value;
use std::sync::LazyLock;

#[derive(Debug, Clone)]
pub struct Outcome {
    pub text: String,
    pub action: &'static str,
    pub status: &'static str,
    pub reason: Option<&'static str>,
}

/// 一回の生成結果を受け取る。Noneは通信等で本文を受け取れなかった場合。
pub fn finish(raw: Option<&str>) -> Outcome {
    let choice = raw.map(normal_choice);
    let (text, action, status, reason) = match choice {
        Some(Ok(crate::speech_choice::Choice::Speak(text))) => (text, "speak", "accepted", None),
        Some(Ok(crate::speech_choice::Choice::Silent)) => (String::new(), "silent", "silent", None),
        Some(Err(reason)) => (String::new(), "silent", "rejected", Some(reason)),
        None => (
            String::new(),
            "silent",
            "generation_error",
            Some("generation_error"),
        ),
    };
    Outcome {
        text,
        action,
        status,
        reason,
    }
}

fn normal_choice(raw: &str) -> Result<crate::speech_choice::Choice, &'static str> {
    let raw = crate::text_format::strip_code_fence(raw);
    let payload: Value = match serde_json::from_str(&raw) {
        Ok(payload) => payload,
        Err(error) if error.is_eof() => complete_json(&raw).ok_or("invalid_contract")?,
        Err(_) => return Err("invalid_contract"),
    };
    let payload = payload.as_object().ok_or("invalid_contract")?;
    let speech = match payload.get("speech") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(text)) => text.trim().to_owned(),
        Some(Value::Number(number)) => number.to_string(),
        Some(_) => return Err("invalid_contract"),
    };
    let action = match payload.get("action") {
        None => "speak",
        Some(Value::String(action)) => action.trim(),
        Some(_) => return Err("invalid_contract"),
    };
    match action {
        "silent" if speech.is_empty() => Ok(crate::speech_choice::Choice::Silent),
        "speak" if !crate::speech_choice::broken_output(&speech) => {
            Ok(crate::speech_choice::Choice::Speak(speech))
        }
        "speak" => Err(if speech.is_empty() {
            "empty_output"
        } else {
            "broken_output"
        }),
        _ => Err("invalid_contract"),
    }
}

/// EOFで欠けた閉じ記号だけを足す。文字列の続きを推測したり内側の別JSONを拾ったりしない。
pub(crate) fn complete_json(raw: &str) -> Option<Value> {
    let mut result = raw.trim().to_owned();
    if !result.starts_with('{') {
        return None;
    }
    let mut closing = Vec::new();
    let mut quoted = false;
    let mut escaped = false;
    for c in result.chars() {
        if quoted {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == '"' {
                quoted = false;
            }
        } else {
            match c {
                '"' => quoted = true,
                '{' => closing.push('}'),
                '[' => closing.push(']'),
                '}' | ']' if closing.pop() != Some(c) => return None,
                _ => {}
            }
        }
    }
    if quoted {
        // 単独の末尾バックスラッシュは未完のエスケープなので、確定した本文までを使う。
        if escaped {
            result.pop();
        }
        result.push('"');
    }
    result.extend(closing.into_iter().rev());
    serde_json::from_str(&result).ok()
}

// カタログ材料用の既知名抽出。発話の採否や、現在の在否検査には使わない。
static LABELS: LazyLock<Vec<String>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("chat_validation/labels.json"))
        .expect("catalog speech labels")
});
pub(crate) fn mentioned(text: &str) -> Vec<String> {
    let mut covered = vec![false; text.len()];
    let mut found = vec![];
    for label in LABELS.iter() {
        let mut start = 0;
        while let Some(offset) = text[start..].find(label) {
            let begin = start + offset;
            let end = begin + label.len();
            if !covered[begin..end].iter().any(|v| *v) {
                covered[begin..end].fill(true);
                found.push(label.clone());
            }
            start = begin + text[begin..].chars().next().unwrap().len_utf8();
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn incomplete_json_and_scalar_speech_are_completed_without_new_words() {
        for (raw, text) in [
            (r#"{"action":"speak","speech":"そや"#, "そや"),
            (r#"{"action":"speak","speech":"そや""#, "そや"),
            (r#"{"action":"speak","speech":123}"#, "123"),
            (r#"{"speech":"うん"}"#, "うん"),
            (
                "```json\n{\"action\":\"speak\",\"speech\":\"そやな。\"}\n```",
                "そやな。",
            ),
            (
                r#"{"action":"speak","speech":"引用は\"そや\""#,
                "引用は\"そや\"",
            ),
        ] {
            let result = finish(Some(raw));
            assert_eq!(result.text, text, "{raw}: {result:?}");
            assert_eq!(result.status, "accepted");
        }
    }
    #[test]
    fn ordinary_text_is_not_checked_for_language_dialect_names_or_observation_agreement() {
        for text in [
            "うん",
            "OK",
            "Gemma",
            "空が青いね。",
            "土の匂いがするわ。",
            "そこのエンダーマン、元気やな。",
            "家に帰る前に冒険しよか。",
            "ドギド: うれしいわ。",
        ] {
            let raw = json!({"action":"speak","speech":text}).to_string();
            let result = finish(Some(&raw));
            assert_eq!(result.text, text);
            assert_eq!(result.status, "accepted");
        }
    }
    #[test]
    fn empty_or_broken_output_returns_no_candidate_or_fallback() {
        for raw in [
            "",
            r#"{"action":"speak"}"#,
            r#"{"action":"speak","speech":null}"#,
            r#"{"action":"speak","speech":""}"#,
            r#"{"action":"speak","speech":["そや"]}"#,
            r#"{"child":{"action":"speak","speech":"そや"}}"#,
            r#"{"action":"speak","speech":"同じや。同じや。同じや。"}"#,
            r#"{"action":"speak","speech":"壊れ�た"}"#,
            r#"{"action":"speak","speech":"<|im_start|>assistant"}"#,
            r#"{"action":"speak","speech":"そや",}"#,
        ] {
            let result = finish(Some(raw));
            assert!(result.text.is_empty(), "{raw}: {result:?}");
            assert_eq!(result.status, "rejected");
            assert_eq!(result.action, "silent");
        }
        assert_eq!(finish(None).status, "generation_error");
        assert_eq!(
            finish(Some(r#"{"action":"silent","speech":""}"#)).status,
            "silent"
        );
    }
}
