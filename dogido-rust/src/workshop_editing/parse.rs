use crate::{haiku::lexical, workshop_input_guard};
use regex::Regex;
use serde_json::Value;
use std::{
    collections::{BTreeSet, HashMap},
    sync::LazyLock,
};
pub(super) fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
pub(super) use crate::chat_catalog::truth;
pub(super) fn list(v: &Value) -> &[Value] {
    v.as_array().map_or(&[], Vec::as_slice)
}
pub(super) use crate::compat::is_python_whitespace as space;
pub(super) fn compact(s: &str) -> String {
    lexical::hiragana(s)
        .chars()
        .filter(|c| !"\n 　、。？?！!「」『』".contains(*c))
        .collect()
}
pub(super) fn verse_lines(s: &str) -> Vec<String> {
    let s = s.trim_matches(space);
    if s.is_empty() {
        return vec![];
    }
    let lines = s
        .split([
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ])
        .map(|s| s.trim_matches(space))
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if lines.len() == 1 {
        let parts = s
            .split(space)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>();
        if parts.len() == 3 {
            return parts;
        }
    }
    lines
}
static RULES: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("patterns.json")).unwrap());
static VALIDATOR: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../workshop_validation/assets.json")).unwrap()
});
static RE: LazyLock<HashMap<String, Regex>> = LazyLock::new(|| {
    let mut map = HashMap::new();
    for (k, v) in RULES.as_object().unwrap() {
        if let Some(s) = v.as_str() {
            map.insert(
                k.clone(),
                Regex::new(&s.replace(r"\s", r"[\s\x1c-\x1f]")).unwrap(),
            );
        }
    }
    for k in ["edit_quote", "edit_negative", "edit_positive"] {
        map.insert(k.into(), Regex::new(text(&VALIDATOR[k])).unwrap());
    }
    for (i, p) in list(&RULES["explicit_lines"]).iter().enumerate() {
        map.insert(format!("line{i}"), Regex::new(text(p)).unwrap());
    }
    map
});
pub(super) fn pattern(key: &str) -> &'static Regex {
    &RE[key]
}
pub(super) fn explicit_lines(s: &str) -> BTreeSet<usize> {
    (0..3)
        .filter(|i| RE[&format!("line{i}")].is_match(s))
        .collect()
}
pub(super) fn explicit_line(s: &str) -> Option<usize> {
    let xs = explicit_lines(s);
    if xs.len() == 1 {
        xs.first().copied()
    } else {
        None
    }
}
pub(super) fn explicit_edit(s: &str) -> bool {
    if !workshop_input_guard::state_change_safe("stage_player_edit", s, s) {
        return false;
    }
    let outside = RE["edit_quote"].replace_all(s, "候補");
    !RE["edit_negative"].is_match(&outside) && RE["edit_positive"].is_match(&outside)
}
pub(super) fn explicit_discussion(s: &str) -> bool {
    RE["explicit_discussion"].is_match(s)
}
pub(super) fn fragment_prefix(s: &str) -> String {
    RE["fragment_prefix"].replace(s, "").into_owned()
}
#[derive(Clone, Debug)]
pub(super) struct Replacement {
    pub text: String,
    pub index: Option<i64>,
    pub fragment: Option<String>,
}
fn second_matches(s: &str) -> Vec<(String, String)> {
    // Python lookahead never consumes its suffix. Match the same earliest start,
    // shortest value, then resume after the verb (not after the lookahead).
    let mut out = vec![];
    let mut from = 0;
    while from < s.len() {
        let mut found = None;
        'starts: for (start, _) in s[from..].char_indices() {
            let start = from + start;
            let positions = s[start..]
                .char_indices()
                .map(|(i, _)| start + i)
                .chain(std::iter::once(s.len()))
                .collect::<Vec<_>>();
            for &end in positions.iter().take(49).skip(1) {
                let value = &s[start..end];
                if value.chars().any(|c| "、，。！？!?\n".contains(c)) {
                    break;
                }
                let suffix = &s[end..];
                let Some(rest) = suffix
                    .strip_prefix('に')
                    .or_else(|| suffix.strip_prefix('へ'))
                else {
                    continue;
                };
                for (verb, tail) in [
                    (
                        "変えて",
                        &["ほしい", "ください", "くれる", "みて", "みよう"][..],
                    ),
                    (
                        "かえて",
                        &["ほしい", "ください", "くれる", "みて", "みよう"][..],
                    ),
                    ("してみて", &["ほしい", "ください", "みよう"][..]),
                    ("したら", &["いい", "ええ", "どう"][..]),
                ] {
                    if let Some(after) = rest.strip_prefix(verb)
                        && (after.is_empty()
                            || after.starts_with(['。', '！', '!'])
                            || tail.iter().any(|p| after.starts_with(p)))
                    {
                        let stop = end + 'に'.len_utf8() + verb.len();
                        found = Some((value.to_owned(), s[start..stop].into(), stop));
                        break 'starts;
                    }
                }
            }
        }
        if let Some((v, m, end)) = found {
            out.push((v, m));
            from = end
        } else {
            break;
        }
    }
    out
}
pub(super) fn replacement(s: &str) -> (String, Option<Replacement>) {
    let s = s.trim_matches(space);
    if s.is_empty() {
        return ("no_match".into(), None);
    }
    let mut matches = Vec::new();
    for i in 0..4 {
        if i == 1 {
            matches.extend(second_matches(s));
        } else {
            for m in RE[&format!("replacement{i}")].captures_iter(s) {
                matches.push((m["value"].into(), m[0].into()))
            }
        }
    }
    if matches.is_empty() {
        return ("no_match".into(), None);
    }
    if RE["negative"].is_match(s) || RE["report"].is_match(s) {
        return ("rejected".into(), None);
    }
    if explicit_lines(s).len() > 1 {
        return ("ambiguous".into(), None);
    }
    let mut candidates = Vec::new();
    for (value, whole) in matches {
        let mut candidate = value.trim_matches(space).to_owned();
        let quoted = RE["quoted_part"]
            .captures_iter(&candidate)
            .map(|m| m[1].to_owned())
            .collect::<Vec<_>>();
        let had_quoted = quoted.len() == 1;
        if had_quoted {
            candidate = quoted[0].trim_matches(space).into()
        }
        let had_prefix = RE["line_prefix"].is_match(&candidate);
        candidate = RE["line_prefix"]
            .replace(&candidate, "")
            .trim_matches(space)
            .into();
        for sep in [
            "じゃなくて",
            "じゃなく",
            "ではなくて",
            "ではなく",
            "より",
            "から",
        ] {
            if let Some((_, tail)) = candidate.rsplit_once(sep) {
                candidate = tail.trim_matches(space).into()
            }
        }
        if let Some(m) = RE["quoted_whole"].captures(&candidate) {
            candidate = m[1].trim_matches(space).into()
        } else if !had_prefix
            && !had_quoted
            && candidate.contains('を')
            && (whole.contains("変え") || whole.contains("かえ"))
        {
            candidate = candidate
                .rsplit_once('を')
                .unwrap()
                .1
                .trim_matches(space)
                .into()
        }
        candidate = candidate
            .trim_matches(|c| "「」『』\"' 、，:：".contains(c))
            .into();
        if let Some(c) = candidate.strip_suffix("とか") {
            candidate = c.trim_end_matches(space).into()
        }
        if !candidate.is_empty() && !candidates.contains(&candidate) {
            candidates.push(candidate)
        }
    }
    if candidates.len() != 1 {
        return ("ambiguous".into(), None);
    }
    (
        "accepted".into(),
        Some(Replacement {
            text: candidates.remove(0),
            index: explicit_line(s).map(|i| i as i64),
            fragment: None,
        }),
    )
}
