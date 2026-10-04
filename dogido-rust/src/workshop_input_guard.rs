//! Verbatim mutation evidence and paused-workshop prompts. No state or I/O.
use crate::types::{ChatMessage, Role};
use regex::Regex;
use serde_json::Value;
use std::{collections::HashMap, sync::LazyLock};

static ASSETS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("workshop_input_guard/assets.json")).expect("guard assets")
});
fn guard_regex(s: &str) -> Regex {
    Regex::new(&s.replace(r"\s", r"[\s\x1c-\x1f]")).expect("canonical guard regex")
}
static PATTERNS: LazyLock<HashMap<String, Regex>> = LazyLock::new(|| {
    let mut patterns = HashMap::new();
    for k in ["report", "conditional", "uncertain"] {
        patterns.insert(
            k.into(),
            guard_regex(ASSETS["patterns"][k].as_str().unwrap()),
        );
    }
    for (k, v) in ASSETS["patterns"]["contradictions"].as_object().unwrap() {
        patterns.insert(k.clone(), guard_regex(v.as_str().unwrap()));
    }
    for (k, v) in [
        (
            "ack",
            r"\A(?:うん|はい|ええよ|いいよ|そうしよう|おけ|ok|OK)[。！!、\s]*\z",
        ),
        (
            "disavow",
            r"(?:とは|って|という意味|ということ|わけ|つもり).{0,20}(?:ない|なく|ません|へん)",
        ),
        (
            "no_resume",
            r"続け(?:ない|ません|ん|へん|るな)|戻(?:らない|りません|らん|らへん|るな)|再開(?:しない|しません|せん|せえへん|するな)",
        ),
        (
            "no_close",
            r"(?:終わり|おわり)に(?:しない|しません|せん|せえへん)|(?:終わ|おわ)(?:らない|りません|らん|らへん)|やめ(?:ない|ません|へん|るな)",
        ),
        (
            "no_edit",
            r"(?:変え|直さ|置き換え|変更し|修正し|使わ|採用し)(?:ない|なく|ん|へん)|(?:変える|直す|置き換える|変更する|修正する|使う)な|(?:に|へ)(?:は)?(?:しない|しなく|するな|せん|せえへん)",
        ),
        (
            "edit_question",
            r"(?:変え|直さ|置き換え|変更し|修正し|使わ|採用し|(?:に|へ)(?:は)?し)ない(?:かな|か|ですか|でしょうか)?[?？][。！!\s]*\z",
        ),
    ] {
        patterns.insert(k.into(), guard_regex(v));
    }
    patterns
});
use crate::compat::is_dogido_whitespace as space;
fn quoted(text: &str, evidence: &str) -> bool {
    let stripped = evidence.trim_matches(space);
    if stripped.chars().count() >= 2
        && [('「', '」'), ('『', '』'), ('"', '"'), ('\'', '\'')]
            .iter()
            .any(|(o, c)| stripped.starts_with(*o) && stripped.ends_with(*c))
    {
        return true;
    }
    let starts: Vec<_> = text.match_indices(evidence).map(|(i, _)| i).collect();
    if starts.is_empty() {
        return evidence.contains(['「', '」', '『', '』', '"', '\'']);
    }
    let chars: Vec<_> = text.chars().collect();
    for byte in starts {
        let start = text[..byte].chars().count();
        let end = start + evidence.chars().count();
        let prefix = &chars[..(start + 1).min(chars.len())];
        let mut inside = [('「', '」'), ('『', '』')].iter().any(|(o, c)| {
            let opening = prefix.iter().rposition(|v| v == o);
            let closing = prefix.iter().rposition(|v| v == c);
            opening.is_some() && opening > closing && chars[end..].contains(c)
        });
        if !inside {
            inside = ['"', '\''].iter().any(|q| {
                chars[..start].iter().filter(|v| *v == q).count() % 2 == 1
                    && chars[end..].contains(q)
            });
        }
        if !inside {
            return false;
        }
    }
    true
}
fn local_context(text: &str, evidence: &str) -> String {
    let Some(byte) = text.find(evidence) else {
        return evidence.into();
    };
    let chars: Vec<_> = text.chars().collect();
    let start = text[..byte].chars().count();
    let end = start + evidence.chars().count();
    let mut left = start.saturating_sub(24);
    let mut right = (end + 24).min(chars.len());
    for boundary in ['。', '！', '？', '!', '?'] {
        if let Some(at) = chars[left..start].iter().rposition(|c| *c == boundary) {
            left += at + 1;
        }
        if let Some(at) = chars[end..right].iter().position(|c| *c == boundary) {
            right = end + at + 1;
        }
    }
    chars[left..right].iter().collect()
}
pub fn state_change_safe(action: &str, text: &str, evidence: &str) -> bool {
    let source = format!("{text}\n{evidence}");
    if source.trim_matches(space).is_empty()
        || evidence.contains(['?', '？'])
        || quoted(text, evidence)
    {
        return false;
    }
    let local = local_context(text, evidence);
    // Scope this additional negation check from the selected evidence onward;
    // an earlier refusal must not veto a later explicit change of mind.
    let from_evidence = local
        .find(evidence)
        .map(|i| &local[i..])
        .unwrap_or(evidence);
    if action == "close_workshop" && PATTERNS["no_close"].is_match(from_evidence) {
        return false;
    }
    if local.contains(['?', '？'])
        || ["report", "conditional", "uncertain"]
            .iter()
            .any(|k| PATTERNS[*k].is_match(&local))
    {
        return false;
    }
    PATTERNS
        .get(action)
        .is_none_or(|r| !r.is_match(source.trim_matches(space)))
}
pub(crate) fn edit_negated(text: &str) -> bool {
    let outside =
        crate::reaction_leaf::sanitize::re(r"「[^「」]*」|『[^『』]*』").replace_all(text, "候補");
    PATTERNS["no_edit"].is_match(&outside)
}
/// Questions may introduce a candidate, but a refusal or reported instruction
/// must not replace the player's current candidate.
pub fn discussion_idea_safe(text: &str) -> bool {
    (!edit_negated(text) || PATTERNS["edit_question"].is_match(text))
        && !quoted(text, text)
        && !PATTERNS["report"].is_match(text)
        && !PATTERNS["disavow"].is_match(text)
}
pub fn combat_safe(action: &str, text: &str, evidence: &str) -> bool {
    if matches!(action, "unrelated" | "uncertain") {
        return true;
    }
    if (action == "resume_workshop" && PATTERNS["ack"].is_match(text))
        || PATTERNS["disavow"].is_match(text)
        || (matches!(action, "resume_workshop" | "workshop_input")
            && PATTERNS["no_resume"].is_match(text))
        || (action == "close_workshop" && PATTERNS["no_close"].is_match(text))
    {
        return false;
    }
    action == "workshop_input"
        || state_change_safe(
            if action == "close_workshop" {
                "close_workshop"
            } else {
                "stage_player_edit"
            },
            text,
            evidence,
        )
}
pub fn combat_messages(verse: &str, text: &str) -> Vec<ChatMessage> {
    let verse = verse.trim_matches(space);
    let text = text.trim_matches(space);
    let p = &ASSETS["prompt"]["parts"];
    vec![
        ChatMessage {
            role: Role::System,
            content: ASSETS["prompt"]["system"].as_str().unwrap().into(),
        },
        ChatMessage {
            role: Role::User,
            content: format!(
                "{}{}{}{}{}",
                p[0].as_str().unwrap(),
                if verse.is_empty() {
                    "（句なし）"
                } else {
                    verse
                },
                p[1].as_str().unwrap(),
                if text.is_empty() {
                    "（聞き取れなかった）"
                } else {
                    text
                },
                p[2].as_str().unwrap()
            ),
        },
    ]
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_evidence_and_paused_input_guards_match() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("workshop_input_guard/fixtures.json")).unwrap();
        for c in cases {
            let text = c["text"].as_str().unwrap();
            let evidence = c["evidence"].as_str().unwrap();
            for (action, expected) in c["state"].as_object().unwrap() {
                assert_eq!(
                    state_change_safe(action, text, evidence),
                    expected.as_bool().unwrap()
                        && !(action == "close_workshop"
                            && ["終わりにしない", "終わらない", "終わらん"].iter().any(
                                |word| text
                                    .find(evidence)
                                    .map(|i| text[i..].contains(word))
                                    .unwrap_or(false)
                                    || evidence.contains(word)
                            )),
                    "state {action}: {c}"
                );
            }
            for (action, expected) in c["combat"].as_object().unwrap() {
                assert_eq!(
                    combat_safe(action, text, evidence),
                    expected.as_bool().unwrap(),
                    "combat {action}: {c}"
                );
            }
        }
    }
    #[test]
    fn full_paused_classification_messages_match_canonical() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("workshop_input_guard/prompts.json")).unwrap();
        for c in cases {
            assert_eq!(
                serde_json::to_value(combat_messages(
                    c["verse"].as_str().unwrap(),
                    c["text"].as_str().unwrap()
                ))
                .unwrap(),
                c["messages"]
            );
        }
    }
}
