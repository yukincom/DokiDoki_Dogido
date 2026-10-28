//! 編集入力の表記上の区切りと行呼称を扱う。句本文の区切りを扱い、置換語の解釈・保存は行わない。
use crate::haiku::lexical;
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
pub(super) use crate::compat::is_dogido_whitespace as space;
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
pub(super) fn fragment_prefix(s: &str) -> String {
    RE["fragment_prefix"].replace(s, "").into_owned()
}
#[derive(Clone, Debug)]
pub(super) struct Replacement {
    pub text: String,
    pub index: Option<i64>,
    pub fragment: Option<String>,
}

pub(super) fn unquote(text: &str) -> &str {
    let text = text.trim_matches(space);
    for (open, close) in [('「', '」'), ('『', '』'), ('"', '"'), ('\'', '\'')] {
        if let Some(inner) = text.strip_prefix(open).and_then(|s| s.strip_suffix(close)) {
            return inner.trim_matches(space);
        }
    }
    text
}
