//! 現在発話にある知識質問だけを抽出する。DB・モデル・状態変更は呼ばない。
use icu_normalizer::ComposingNormalizer;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Query {
    pub domain: String,
    pub subject: String,
    pub intent: String,
    pub evidence: String,
}

#[derive(Deserialize)]
struct Grammar {
    question_cues: Vec<String>,
    generation_requests: Vec<String>,
    japanese: HashSet<String>,
    poetry: HashSet<String>,
    minecraft_exact: HashSet<String>,
    minecraft_context: Vec<String>,
    generic: HashSet<String>,
    special: Vec<String>,
    patterns: Vec<String>,
    prefixes: Vec<String>,
    casefold: HashMap<char, String>,
    unassigned_ranges: Vec<(u32, u32)>,
    decimal_class: String,
    word_class: String,
}
static GRAMMAR: LazyLock<Grammar> = LazyLock::new(|| {
    serde_json::from_str(include_str!("query-grammar.json")).expect("checked knowledge grammar")
});

// 資料照会の大文字小文字・空白の許容範囲を固定する。I/iにİ/ıを、空白にC0区切りを含める。
pub(super) fn compile(pattern: &str, full: bool) -> Regex {
    let pattern = pattern
        .replace("Minecraft", "M[iİı]necraft")
        .replace("Edition", "Ed[iİı]t[iİı]on")
        .replace("ID", "[Iİı]D")
        .replace("id", "[iİı]d")
        .replace("a-z", "a-zİı")
        .replace(r"\s", r"[\s\u001c-\u001f]")
        .replace(r"\d", &GRAMMAR.decimal_class);
    let pattern = if full {
        format!(r"(?i)\A(?:{pattern})\z")
    } else {
        format!("(?i){pattern}")
    };
    Regex::new(&pattern).expect("checked knowledge pattern")
}
static SPECIAL: LazyLock<Vec<Regex>> =
    LazyLock::new(|| GRAMMAR.special.iter().map(|s| compile(s, true)).collect());
static PATTERNS: LazyLock<Vec<Regex>> =
    LazyLock::new(|| GRAMMAR.patterns.iter().map(|s| compile(s, true)).collect());
static PREFIXES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    GRAMMAR
        .prefixes
        .iter()
        .map(|s| compile(&format!("^{s}"), false))
        .collect()
});
static ADDRESS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(?:ねえ|なあ|ドギド)[、, ]*").unwrap());
static SUFFIX: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?:について|に関して|のこと)$").unwrap());
static MINECRAFT: LazyLock<Regex> = LazyLock::new(|| {
    compile(
        r"(?:\A|[^a-z0-9_./:-])Minecraft(?:\z|[^a-z0-9_./:-])",
        false,
    )
});
static RESOURCE: LazyLock<Regex> = LazyLock::new(|| {
    compile(
        r"(?P<namespace>[a-z0-9_.-]+):[a-z0-9_.-][a-z0-9_./-]*",
        true,
    )
});
static WORD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"\A{}\z", GRAMMAR.word_class)).unwrap());

pub(crate) use crate::compat::is_python_whitespace as space;
/// 同梱のUnicode文字集合にある文字・数値かを返す。単語境界用の集合から_だけを除く。
pub(crate) fn alphanumeric(c: char) -> bool {
    c != '_' && WORD.is_match(c.encode_utf8(&mut [0; 4]))
}
pub(crate) fn nfkc(text: &str) -> String {
    // 照合規則をUnicode更新で変えないため、同梱の文字表で未割当の文字はそのまま保持。
    // 未割当文字の結合クラスは0なので、ここで区切っても前後の合成・順序は同じ。
    let nfkc = ComposingNormalizer::new_nfkc();
    let mut result = String::new();
    let mut start = 0;
    for (index, c) in text.char_indices() {
        let point = c as u32;
        let range = GRAMMAR
            .unassigned_ranges
            .partition_point(|(_, end)| *end < point);
        if GRAMMAR
            .unassigned_ranges
            .get(range)
            .is_some_and(|(begin, _)| *begin <= point)
        {
            result.push_str(&nfkc.normalize(&text[start..index]));
            result.push(c);
            start = index + c.len_utf8();
        }
    }
    result.push_str(&nfkc.normalize(&text[start..]));
    result
}
pub(crate) fn normalize(text: &str) -> String {
    nfkc(text)
        .replace(['～', '〜'], "~")
        .trim_matches(space)
        .into()
}
pub(crate) fn fold(text: &str) -> String {
    let mut out = String::new();
    for c in text.chars() {
        if let Some(mapped) = GRAMMAR.casefold.get(&c) {
            out.push_str(mapped);
        } else {
            out.push(c);
        }
    }
    out
}
fn compact(text: &str) -> String {
    fold(&normalize(text))
        .chars()
        .filter(|c| !space(*c))
        .collect()
}
fn any(text: &str, values: &[&str]) -> bool {
    values.iter().any(|s| text.contains(s))
}
fn unwrap_quotes(mut text: &str) -> &str {
    loop {
        let pair = match text.chars().next() {
            Some('「') => ('「', '」'),
            Some('『') => ('『', '』'),
            Some('"') => ('"', '"'),
            Some('\'') => ('\'', '\''),
            _ => return text,
        };
        if text.chars().count() < 2 || !text.ends_with(pair.1) {
            return text;
        }
        text = text[pair.0.len_utf8()..text.len() - pair.1.len_utf8()].trim_matches(space);
    }
}
fn strip_subject(text: &str) -> String {
    const TRIM: &str = " \t\r\n、,。.!！:：\"'";
    let normalized = normalize(text);
    let mut subject = unwrap_quotes(normalized.trim_matches(|c| TRIM.contains(c))).to_owned();
    subject = ADDRESS.replace(&subject, "").into_owned();
    for prefix in PREFIXES.iter() {
        subject = prefix.replace(&subject, "").into_owned();
    }
    let grammar = unwrap_quotes(subject.trim_matches(|c| TRIM.contains(c)));
    if grammar.starts_with(['~', '〜']) {
        return grammar.into();
    }
    subject = SUFFIX.replace(&subject, "").into_owned();
    unwrap_quotes(subject.trim_matches(|c| TRIM.contains(c) || "?？".contains(c))).into()
}
fn subject(text: &str) -> Option<String> {
    for (index, pattern) in SPECIAL.iter().enumerate() {
        let Some(captures) = pattern.captures(text) else {
            continue;
        };
        let raw = captures.name("subject")?.as_str();
        match index {
            0 | 2 => return Some(raw.into()),
            1 if captures["context"].contains(raw) => return Some(raw.into()),
            3 => {
                let candidate = strip_subject(raw);
                let key = compact(&candidate);
                if GRAMMAR.japanese.contains(&key) || GRAMMAR.poetry.contains(&key) {
                    return Some(candidate);
                }
            }
            _ => {}
        }
    }
    for pattern in PATTERNS.iter() {
        if let Some(captures) = pattern.captures(text) {
            let candidate = strip_subject(captures.name("subject")?.as_str());
            if !candidate.is_empty() {
                return Some(candidate);
            }
        }
    }
    None
}
fn identifier(text: &str) -> bool {
    // ID/idの語境界は同梱の文字・数値・_の集合で調べる。結合記号まで含む標準\wとは範囲が異なる。
    for (index, word) in text.match_indices("ID").chain(text.match_indices("id")) {
        let before = text[..index].chars().next_back();
        let after = text[index + word.len()..].chars().next();
        let boundary = |c: Option<char>| c.is_none_or(|c| !WORD.is_match(&c.to_string()));
        if (before == Some('の') || boundary(before))
            && (matches!(after, Some('は' | 'を')) || boundary(after))
        {
            return true;
        }
    }
    false
}
fn intent(text: &str) -> &'static str {
    if any(&compact(text), &["何年生", "何年で", "配当学年", "学年"]) {
        "grade"
    } else if text.contains("読み") {
        "reading"
    } else if any(text, &["どう変わ", "変更点", "どう変更"]) {
        "change"
    } else if any(text, &["識別子", "名前空間"]) || identifier(text) {
        "identifier"
    } else if any(text, &["耐久値", "最大スタック", "特徴"]) {
        "properties"
    } else if text.contains("分類") {
        "classification"
    } else if any(
        text,
        &["ルール", "規則", "決まり", "形式", "作り方", "接続"],
    ) {
        "rules"
    } else {
        "definition"
    }
}
fn minecraft_context(text: &str) -> bool {
    let normalized = normalize(text);
    let key = compact(&normalized);
    GRAMMAR
        .minecraft_context
        .iter()
        .any(|s| key.contains(&compact(s)))
        || any(&normalized, &["マインクラフト", "マイクラ"])
        || MINECRAFT.is_match(&normalized)
}
fn cjk(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(|c| matches!(c, '\u{3400}'..='\u{4dbf}' | '\u{4e00}'..='\u{9fff}' | '\u{f900}'..='\u{faff}')) && chars.next().is_none()
}
fn domain(subject: &str, text: &str, intent: &str) -> Option<&'static str> {
    let key = compact(subject);
    if GRAMMAR.poetry.contains(&key) {
        return Some("poetry");
    }
    if GRAMMAR.japanese.contains(&key) {
        return Some("japanese_language");
    }
    if ["国語で", "国語の", "日本語で", "日本語の"]
        .iter()
        .any(|s| text.starts_with(s))
    {
        return Some("japanese_language");
    }
    if [
        "世界の詩で",
        "世界の詩の",
        "世界の詩形で",
        "世界の詩形の",
        "詩形で",
        "詩形の",
    ]
    .iter()
    .any(|s| text.starts_with(s))
    {
        return Some("poetry");
    }
    if matches!(intent, "grade" | "reading") && cjk(subject) {
        return Some("japanese_language");
    }
    if matches!(intent, "grade" | "definition")
        && subject.len() == 1
        && subject.as_bytes()[0].is_ascii_digit()
        && (text.starts_with("漢字の") || (text.contains("数字の") && text.contains("漢字")))
    {
        return Some("japanese_language");
    }
    if subject.starts_with(['～', '〜', '~']) {
        return Some("japanese_language");
    }
    let resource = fold(&normalize(subject));
    if minecraft_context(text)
        || GRAMMAR.minecraft_exact.contains(&key)
        || RESOURCE
            .captures(&resource)
            .is_some_and(|c| &c["namespace"] == "minecraft")
    {
        return Some("minecraft");
    }
    None
}

pub fn extract(raw: &str) -> Option<Query> {
    let text = normalize(raw);
    if text.is_empty() || text.starts_with('/') || text.chars().count() > 240 {
        return None;
    }
    if GRAMMAR.generation_requests.iter().any(|s| text.contains(s)) {
        return None;
    }
    let folded = fold(&text);
    if !GRAMMAR
        .question_cues
        .iter()
        .any(|s| folded.contains(&fold(s)))
    {
        return None;
    }
    let subject = subject(&text)?;
    let key = compact(&subject);
    if GRAMMAR.generic.contains(&key) || !compact(&text).contains(&key) {
        return None;
    }
    let intent = intent(&text);
    let domain = domain(&subject, &text, intent)?;
    Some(Query {
        domain: domain.into(),
        subject,
        intent: intent.into(),
        evidence: text,
    })
}

/// 前段で整理済みの表記。明示した剣の持ち替えを知識へ奪わない。
pub fn from_normalized(text: &str) -> Option<Query> {
    if crate::assist::intent::is_explicit_select_sword_request(text) {
        None
    } else {
        extract(text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(Deserialize)]
    struct Case {
        text: String,
        query: Option<Query>,
    }
    #[test]
    fn matches_canonical_python_queries() {
        let cases: Vec<Case> =
            serde_json::from_str(include_str!("../../fixtures/knowledge-query.json")).unwrap();
        for case in cases {
            assert_eq!(extract(&case.text), case.query, "input {:?}", case.text);
        }
    }
    #[test]
    fn normalization_is_confined_to_search_and_never_changes_player_text() {
        let raw = "　Ｍｉｎｅｃｒａｆｔで石のＩＤは？　";
        let query = extract(raw).unwrap();
        assert_eq!(query.subject, "石");
        assert_eq!(query.intent, "identifier");
        assert_eq!(crate::player_text::prepare(raw).raw_text, raw);
        assert!(from_normalized("剣に持ち替えて").is_none());
        assert!(from_normalized("剣の耐久値を教えて").is_some());
        assert!(extract("https://minecraft.net/testって何？").is_none());
    }
}
