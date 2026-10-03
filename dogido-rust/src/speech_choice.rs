//! Speaking and intentional silence share one wire contract in ordinary dialogue.
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
    let reply: Reply = serde_json::from_str(raw.trim()).map_err(|_| "invalid_contract")?;
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
