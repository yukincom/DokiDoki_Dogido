//! 通常会話の「話す／黙る」を、同じJSON契約で検証する。
//! 完結したコード囲みを除き、全体の型・発話内容を検査する。状態変更は呼出側が担当する。
use crate::text_format::strip_code_fence;
use serde::Deserialize;

#[derive(Debug, PartialEq)]
pub enum Choice {
    Speak(String),
    Silent,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Action {
    Speak,
    Silent,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reply {
    action: Action,
    speech: String,
}

pub fn parse(raw: &str) -> Result<Choice, &'static str> {
    let reply: Reply =
        serde_json::from_str(&strip_code_fence(raw)).map_err(|_| "invalid_contract")?;
    match reply.action {
        Action::Silent if reply.speech.is_empty() => Ok(Choice::Silent),
        Action::Silent => Err("invalid_contract"),
        Action::Speak => {
            let text = reply.speech.trim();
            if broken_output(text) {
                Err("broken_output")
            } else {
                Ok(Choice::Speak(text.into()))
            }
        }
    }
}
// Encoding damage and unmistakable loops only; not vocabulary, length or dialect.
pub fn broken_output(text: &str) -> bool {
    if text.is_empty()
        || text
            .chars()
            .any(|c| c == '\u{fffd}' || c.is_control() && !matches!(c, '\n' | '\r' | '\t'))
        || ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]
            .iter()
            .any(|p| text.contains(p))
    {
        return true;
    }
    let sentences: Vec<_> = text
        .split(['。', '！', '？', '\n'])
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    if sentences.len() >= 3 && sentences.iter().all(|s| *s == sentences[0]) {
        return true;
    }
    let chars: Vec<_> = text.chars().filter(|c| !c.is_whitespace()).collect();
    chars.len() >= 24
        && (1..=chars.len() / 4).any(|width| {
            chars.len().is_multiple_of(width)
                && chars
                    .iter()
                    .enumerate()
                    .all(|(i, c)| *c == chars[i % width])
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn complete_fences_preserve_speaking_and_intentional_silence() {
        for (raw, expected) in [
            (
                r#"{"action":"speak","speech":"そやな。"}"#,
                Choice::Speak("そやな。".into()),
            ),
            (r#"{"action":"silent","speech":""}"#, Choice::Silent),
        ] {
            assert_eq!(parse(raw).as_ref(), Ok(&expected));
            for tag in ["", "json"] {
                for newline in [
                    "\n", "\r\n", "\r", "\u{000b}", "\u{000c}", "\u{001c}", "\u{001d}", "\u{001e}",
                    "\u{0085}", "\u{2028}", "\u{2029}",
                ] {
                    let fenced = format!(" \n```{tag}{newline}{raw}{newline}```\n ");
                    assert_eq!(parse(&fenced).as_ref(), Ok(&expected), "{fenced:?}");
                }
            }
        }
    }

    #[test]
    fn fences_do_not_repair_partial_json_or_extract_nested_choices() {
        for body in [
            r#"{"action":"silent","speech":"""#,
            r#"{"action":"silent","speech":"","extra":1}"#,
            r#"{"action":"silent","speech":"黙るで"}"#,
            r#"{"child":{"action":"silent","speech":""}}"#,
            "ここにJSONを書くで。\n{\"action\":\"silent\",\"speech\":\"\"}",
            "{\"action\":\"silent\",\"speech\":\"\"}\nこれで終わりや。",
        ] {
            assert_eq!(
                parse(&format!("```json\n{body}\n```")),
                Err("invalid_contract")
            );
        }
        let valid = r#"{"action":"silent","speech":""}"#;
        for raw in [
            format!("```json\n{valid}"),
            format!("```\n{valid}\n``"),
            format!("```json{valid}```"),
            format!("```json\n{valid}\n```\n後書き"),
        ] {
            assert_eq!(parse(&raw), Err("invalid_contract"), "{raw}");
        }
        assert_eq!(
            parse("```json\n{\"action\":\"speak\",\"speech\":\"\"}\n```"),
            Err("broken_output")
        );
    }

    #[test]
    fn silence_is_explicit_and_never_salvaged_from_broken_json() {
        assert_eq!(
            parse(r#"{"action":"silent","speech":""}"#),
            Ok(Choice::Silent)
        );
        for raw in [
            "",
            " ",
            r#"{"action":"silent","speech":"黙るで"}"#,
            r#"{"action":"silent","speech":"","extra":1}"#,
            r#"{"child":{"action":"silent","speech":""}}"#,
            r#"{"action":"silent","speech":"""#,
        ] {
            assert!(parse(raw).is_err(), "{raw}");
        }
    }
    #[test]
    fn response_can_combine_the_player_and_the_surroundings() {
        let text = "そやな、一緒に帰ろか。あ、雨も降ってきたな。";
        assert_eq!(
            parse(&serde_json::json!({"action":"speak","speech":text}).to_string()),
            Ok(Choice::Speak(text.into()))
        );
        assert!(parse(r#"{"action":"speak","speech":""}"#).is_err());
        assert!(parse(r#"{"action":"speak","speech":"同じや。同じや。同じや。"}"#).is_err());
    }
}
