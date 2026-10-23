//! 編集入力の表記上の区切りと行呼称を扱う。原文の語句を切り出し、意味判断・保存は行わない。
use crate::haiku::lexical;
use anyhow::{Result, ensure};
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
pub(super) fn explicit_line(s: &str) -> Option<usize> {
    let xs = explicit_lines(s);
    if xs.len() == 1 {
        xs.first().copied()
    } else {
        None
    }
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

/// 明示形「AをBに変えて」「A -> B」の原文範囲。外側の対になった引用符と空白だけを除き、
/// 語句中の助詞・表記は保つ。操作の許可は呼び手の原文意思検証が担当する。
pub(super) struct VerbatimEdit<'a> {
    pub target: &'a str,
    pub replacement: &'a str,
    pub target_quoted: bool,
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

/// 引用外の導入部候補と、後続に空白・読点・引用開始があるかを返す。
/// 区切りのない形は、Engineが旧語全体の読みを現在句へ照合してから分ける。
pub(super) fn without_intro(text: &str) -> Option<(&str, bool)> {
    ["では", "じゃあ"]
        .iter()
        .find_map(|prefix| text.strip_prefix(prefix))
        .map(|s| {
            let trimmed = s.trim_start_matches(|c| space(c) || matches!(c, '、' | ','));
            (
                trimmed,
                trimmed != s || trimmed.starts_with(['「', '『', '"', '\'']),
            )
        })
        .filter(|(s, _)| !s.is_empty())
}

/// 一つの明示置換に限り、モデルの部分文字列ではなく今回の原文からA/Bを得る。
/// 引用内部の「を」は語句の一部。複数の区切りや閉じていない引用は推測で選ばない。
/// この形以外の自由な依頼はNoneで既存のモデル抽出へ戻す。
pub(super) fn verbatim_edit(input: &str) -> Result<Option<VerbatimEdit<'_>>> {
    let source = input.trim_matches(space);
    let mut quotes = Vec::new();
    let mut separators = Vec::new();
    let mut arrows = Vec::new();
    for (at, c) in source.char_indices() {
        if quotes.last() == Some(&c) {
            quotes.pop();
            continue;
        }
        if let Some(close) = match c {
            '「' => Some('」'),
            '『' => Some('』'),
            '"' => Some('"'),
            '\'' => Some('\''),
            _ => None,
        } {
            quotes.push(close);
        } else if quotes.is_empty() {
            if c == 'を' {
                separators.push(at);
            } else if c == '→' {
                arrows.push((at, c.len_utf8()));
            } else if source[at..].starts_with("->") {
                arrows.push((at, 2));
            }
        }
    }
    // 矢印の右辺全体が今回の指定語。古い案の再利用・短縮・補作を保存へ持ち込まない。
    if !arrows.is_empty() {
        if !quotes.is_empty() || arrows.len() != 1 || pattern("replacement_tail").is_match(source) {
            return Ok(None);
        }
        let (at, width) = arrows[0];
        return verbatim_pair(&source[..at], &source[at + width..]).map(Some);
    }
    let Some(tail) = pattern("replacement_tail").find(source) else {
        return Ok(None);
    };
    let source = source[..tail.start()].trim_matches(space);
    // 行を省略した依頼はモデルの既存抽出を使う。A/Bが複数なら部分実行しない。
    if separators.is_empty() && quotes.is_empty() {
        return Ok(None);
    }
    if !quotes.is_empty() || separators.len() != 1 {
        return Ok(None);
    }
    let at = separators[0];
    verbatim_pair(&source[..at], &source[at + 'を'.len_utf8()..]).map(Some)
}

fn verbatim_pair<'a>(target: &'a str, replacement: &'a str) -> Result<VerbatimEdit<'a>> {
    let raw_target = target.trim_matches(space);
    let target = unquote(raw_target);
    let replacement = unquote(replacement);
    ensure!(
        !target.is_empty() && !replacement.is_empty(),
        "empty_verbatim_edit"
    );
    ensure!(
        target.chars().count() <= 120 && replacement.chars().count() <= 48,
        "verbatim_edit_too_long"
    );
    Ok(VerbatimEdit {
        target,
        replacement,
        target_quoted: target != raw_target,
    })
}
