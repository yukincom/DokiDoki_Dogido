//! The narrow reaction-leaf guards, kept separate from normal player-chat policy.
use super::prompts::{pystr, truth};
use regex::Regex;
use serde_json::Value;
use std::{
    collections::HashMap,
    sync::{LazyLock, Mutex},
};

pub(crate) fn re(pattern: &'static str) -> Regex {
    static CACHE: LazyLock<Mutex<HashMap<&'static str, Regex>>> =
        LazyLock::new(|| Mutex::new(HashMap::new()));
    CACHE
        .lock()
        .unwrap()
        .entry(pattern)
        .or_insert_with(|| Regex::new(pattern).expect("reaction regex"))
        .clone()
}
use crate::compat::is_python_whitespace as whitespace;
pub(crate) fn strip(s: &str) -> &str {
    s.trim_matches(whitespace)
}
pub(crate) fn compact(s: &str) -> String {
    s.chars().filter(|c| !whitespace(*c)).collect()
}
fn quotes(s: &str) -> String {
    let s = strip(s);
    for (a, b) in [('「', '」'), ('『', '』'), ('"', '"'), ('\'', '\'')] {
        if s.chars().count() >= 2 && s.starts_with(a) && s.ends_with(b) {
            return strip(&s[a.len_utf8()..s.len() - b.len_utf8()]).into();
        }
    }
    s.into()
}
fn japanese(c: char) -> bool {
    ('\u{3040}'..='\u{309f}').contains(&c)
        || ('\u{30a0}'..='\u{30ff}').contains(&c)
        || ('\u{4e00}'..='\u{9fff}').contains(&c)
        || digit(c)
        || "。、！？!?,，．…ー〜「」（）()・：:; 　".contains(c)
}
fn digit(c: char) -> bool {
    static RANGES: LazyLock<Vec<(u32, u32)>> = LazyLock::new(|| {
        serde_json::from_str(include_str!("digit-ranges.json")).expect("Python digit table")
    });
    RANGES.iter().any(|(a, b)| (*a..=*b).contains(&(c as u32)))
}
fn forward(s: &str) -> bool {
    let c: Vec<_> = s.chars().filter(|c| !whitespace(*c)).collect();
    !c.is_empty()
        && c.iter().filter(|c| japanese(**c)).count() as f64 / c.len() as f64 >= 0.7
        && c.iter()
            .any(|c| ('\u{3040}'..='\u{309f}').contains(c) || ('\u{4e00}'..='\u{9fff}').contains(c))
}
pub(crate) fn clean(raw: &str) -> String {
    let s = re(r"(?s)<think>.*?</think>").replace_all(raw, "");
    let s = s.replace("<|im_end|>", "").replace("<|endoftext|>", "");
    let s =
        re(r"(?is)^here'?s a thinking process:.*?(?:final answer:|answer:|返答:|出力:|セリフ:)")
            .replace(strip(&s), "")
            .into_owned();
    let lines: Vec<_> = s
        .split([
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ])
        .map(strip)
        .filter(|l| !l.is_empty())
        .collect();
    let mut candidates = vec![];
    for line in &lines {
        let line =
            re(r"(?i)^(Final answer|Answer|返答|出力|セリフ|ドギド)\s*[:：]\s*").replace(line, "");
        let line = strip(&line);
        if line.is_empty()
            || [
                "Here's a thinking process",
                "Here is a thinking process",
                "Let's think",
            ]
            .iter()
            .any(|p| line.starts_with(p))
            || re(r"^\d+\.\s+\*\*").is_match(line)
            || re(r"^[-*]\s+\*\*").is_match(line)
            || re(r"(?i)^(Role|Persona|Analyze User Input)\s*[:：]").is_match(line)
        {
            continue;
        }
        candidates.push(quotes(line));
    }
    candidates
        .iter()
        .rev()
        .find(|s| forward(s))
        .or(candidates.last())
        .cloned()
        .unwrap_or_else(|| lines.first().map_or_else(String::new, |s| quotes(s)))
}
fn stripped_ascii(text: &str, d: &Value) -> String {
    let mut s = re(r"[A-Za-z]+")
        .replace_all(text, |c: &regex::Captures<'_>| {
            if c[0].eq_ignore_ascii_case("ok") || c[0].eq_ignore_ascii_case("ng") {
                String::new()
            } else {
                c[0].to_owned()
            }
        })
        .into_owned();
    if let Some(player) = d["player_name"]
        .as_str()
        .map(strip)
        .filter(|s| !s.is_empty())
    {
        s = s.replace(player, "");
    }
    s
}
pub(super) fn usable(text: &str, d: &Value) -> bool {
    usability_reason(text, d).is_none()
}
/// Shared surface contract, including the original reason ordering.
pub(crate) fn usability_reason(text: &str, d: &Value) -> Option<&'static str> {
    if text.is_empty() {
        return Some("empty_output");
    }
    if text.chars().count() < 4 {
        return Some("too_short");
    }
    let normalized = stripped_ascii(text, d);
    if re(r"[A-Za-z]{2,}").is_match(&normalized) {
        return Some("non_japanese_explanation");
    }
    let chars: Vec<_> = text.chars().collect();
    if chars
        .windows(4)
        .any(|w| w[0] != '\n' && w.iter().all(|c| *c == w[0]))
    {
        return Some("broken_repetition");
    }
    if re(r"(?i)^(?:ドギド|user|assistant|例\s*\d*|本番)\s*[:：]").is_match(text) {
        return Some("meta_role_label");
    }
    let c: Vec<_> = normalized.chars().filter(|c| !whitespace(*c)).collect();
    if c.is_empty() {
        return Some("empty_output");
    }
    if c.iter().filter(|c| japanese(**c)).count() as f64 / (c.len() as f64) < 0.85 {
        return Some("non_japanese_explanation");
    }
    let h = c
        .iter()
        .filter(|c| ('\u{3040}'..='\u{309f}').contains(*c))
        .count();
    let k = c
        .iter()
        .filter(|c| ('\u{4e00}'..='\u{9fff}').contains(*c))
        .count();
    if h == 0 {
        return Some("missing_hiragana");
    }
    if h + k < 3 {
        return Some("too_little_japanese");
    }
    None
}

fn has(s: &str, patterns: &[&str]) -> bool {
    patterns.iter().any(|p| s.contains(p))
}
fn hostile(text: &str, d: &Value) -> bool {
    if truth(&d["has_visual_threats"]) || truth(&d["combat_active"]) {
        return true;
    }
    let mode = if truth(&d["mode"]) {
        pystr(&d["mode"])
    } else if truth(&d["character_mode"]) {
        pystr(&d["character_mode"])
    } else {
        String::new()
    }
    .to_lowercase();
    if matches!(
        mode.as_str(),
        "panic" | "suppressed_panic" | "alert" | "battle"
    ) && (truth(&d["nearby_hostile_types"]) || truth(&d["threat_summary"]) || mode != "alert")
    {
        return true;
    }
    if truth(&d["nearby_hostile_types"]) || truth(&d["nearby_mob_ids"]) {
        return true;
    }
    if ["threat_summary", "hearing_summary", "event_digest"]
        .iter()
        .any(|k| truth(&d[*k]) && has(&pystr(&d[*k]), &["視認", "敵"]))
    {
        return true;
    }
    has(
        text,
        &[
            "クリーパー",
            "ゾンビ",
            "スケルトン",
            "クモ",
            "ウィッチ",
            "エンダーマン",
            "モンスター",
        ],
    )
}
fn catalog_forbidden(d: &Value) -> Vec<String> {
    static CATALOG: LazyLock<HashMap<String, Vec<String>>> = LazyLock::new(|| {
        let mut out = HashMap::new();
        // Same build-time catalogue boundary as the existing Rust combat/ambient code.
        for value in [
            &*crate::entry_catalog::HOSTILE,
            &*crate::entry_catalog::NEUTRAL,
            &*crate::entry_catalog::PASSIVE,
        ] {
            let items = value.get("items").unwrap_or(value);
            if let Some(items) = items.as_object() {
                for (id, v) in items {
                    let patterns = v["dogido_tactics"]["forbidden_advice"]
                        .as_array()
                        .map(|a| {
                            a.iter()
                                .map(pystr)
                                .map(|s| strip(&s).to_owned())
                                .filter(|s| !s.is_empty())
                                .collect()
                        })
                        .unwrap_or_default();
                    out.insert(id.clone(), patterns);
                }
            }
        }
        out
    });
    let ids = if truth(&d["nearby_hostile_types"]) {
        &d["nearby_hostile_types"]
    } else {
        &d["nearby_mob_ids"]
    };
    let ids = match ids {
        Value::String(s) => vec![s.clone()],
        Value::Array(a) => a.iter().map(pystr).collect(),
        _ => vec![],
    };
    ids.iter()
        .flat_map(|s| {
            let first = s
                .strip_prefix("minecraft:")
                .unwrap_or(s)
                .trim()
                .to_lowercase();
            CATALOG
                .get(
                    first
                        .trim()
                        .strip_prefix("minecraft:")
                        .unwrap_or(first.trim()),
                )
                .cloned()
                .unwrap_or_default()
        })
        .collect()
}
pub(crate) fn forbidden(text: &str, d: &Value) -> bool {
    if hostile(text, d)
        && has(
            text,
            &[
                "じっと",
                "じっとして",
                "動かない",
                "動かんと",
                "動くな",
                "止まって",
                "止まれ",
                "固まれ",
                "その場で",
                "動かへん",
            ],
        )
    {
        return true;
    }
    let mut patterns: Vec<_> = d["forbidden_advice"]
        .as_array()
        .map(|a| a.iter().filter(|v| truth(v)).map(pystr).collect())
        .unwrap_or_default();
    if patterns.is_empty() {
        patterns = catalog_forbidden(d);
    }
    patterns.iter().any(|p| text.contains(p))
}
fn sep(c: char) -> bool {
    whitespace(c) || "！？!?,，．。…〜ー".contains(c)
}
pub(crate) fn repeated(text: &str) -> bool {
    let c: Vec<_> = text.chars().collect();
    // Python's (.{2,8}) separator* backreference separator* backreference.
    for start in 0..c.len() {
        for len in 2..=8 {
            if start + len > c.len() {
                break;
            }
            let unit = &c[start..start + len];
            if unit.contains(&'\n') {
                continue;
            }
            let mut middle = start + len;
            loop {
                if c.get(middle..middle + len) == Some(unit) {
                    let mut end = middle + len;
                    loop {
                        if c.get(end..end + len) == Some(unit) {
                            return true;
                        }
                        if c.get(end).is_some_and(|c| sep(*c)) {
                            end += 1;
                        } else {
                            break;
                        }
                    }
                }
                if c.get(middle).is_some_and(|c| sep(*c)) {
                    middle += 1;
                } else {
                    break;
                }
            }
        }
    }
    let tokens: Vec<_> = text.split(sep).filter(|s| !s.is_empty()).collect();
    tokens.windows(3).any(|w| w[0] == w[1] && w[1] == w[2])
}
pub(super) fn style(kind: &str, text: &str, d: &Value) -> bool {
    if forbidden(text, d) {
        return false;
    }
    if !matches!(
        kind,
        "aftermath"
            | "darkness_escape"
            | "occluded_hostile_presence"
            | "occluded_entry_no_light"
            | "dark_push_no_light"
            | "dark_push_after_breath"
            | "newly_burning_visual"
            | "daylight_water_skeleton"
    ) {
        return true;
    }
    if has(
        text,
        &[
            "だよ",
            "だよね",
            "なんだよ",
            "なんだよね",
            "なんだが",
            "なんだけど",
            "みたいだ",
            "しかたない",
            "闇が深い",
            "凍りつく",
        ],
    ) {
        return false;
    }
    let bad = match kind {
        "aftermath" => {
            has(
                text,
                &[
                    "爆発音",
                    "体力",
                    "HP",
                    "ｈｐ",
                    "次は",
                    "絶対",
                    "逃げよう",
                    "逃げたほう",
                    "回復",
                    "油断するな",
                ],
            ) || re(r"\d").is_match(text)
        }
        "darkness_escape" => {
            has(
                text,
                &[
                    "闇夜",
                    "漆黒",
                    "奈落",
                    "私",
                    "荷が重",
                    "どうすれば",
                    "仕方ない",
                    "戻って",
                    "帰って",
                    "帰れ",
                    "逃げよう",
                    "逃げて",
                    "落ち着いて",
                    "無理しなくていい",
                    "ほしくて",
                    "やめて",
                    "したほうがいい",
                    "してほしい",
                ],
            ) || !has(
                text,
                &[
                    "やで",
                    "やわ",
                    "やん",
                    "やろ",
                    "やねん",
                    "へん",
                    "せん",
                    "やから",
                    "やった",
                    "やな",
                    "やんか",
                    "やろか",
                ],
            )
        }
        "newly_burning_visual" => has(
            text,
            &[
                "やばい",
                "ほんと",
                "ほんとう",
                "助かったね",
                "めっちゃ燃えてる",
            ],
        ),
        "occluded_hostile_presence" => has(
            text,
            &[
                "きゃー",
                "ぎゃー",
                "うわあ",
                "見えた",
                "見えてる",
                "目の前",
                "逃げろ",
                "逃げよう",
                "来てる",
                "来よる",
            ],
        ),
        "daylight_water_skeleton" => has(text, &["火つけ", "火をつけ"]),
        _ => false,
    };
    !bad && !repeated(text) && !text.contains("んかやんか") && !re(r"(やわ|やん|やろ|やんか)(?:[！？!?,，．。…〜ー\s]{0,3})(やわ|やん|やろ|やんか)(?:[！？!?,，．。…〜ー\s]{0,3})(やわ|やん|やろ|やんか)").is_match(text)
}
pub(super) fn final_guard(kind: &str, text: &str, d: &Value) -> bool {
    if kind == "light_source_gain" {
        return has(
            text,
            &["クラフト", "作った", "作れた", "置いた", "設置", "拾った"],
        ) || re(r"(?:[0-9０-９]+|[一二三四五六七八九十百]+)\s*(?:本|個)").is_match(text)
            || re(r"(?:明かり|あかり|松明|ランタン).{0,4}できた").is_match(text);
    }
    if kind != "aftermath" {
        return false;
    }
    let text = compact(text);
    let outcome = d
        .get("combat_outcome")
        .map(pystr)
        .unwrap_or_else(|| "disengaged".into());
    let kill = has(
        &text,
        &[
            "倒した",
            "倒せた",
            "やっつけた",
            "仕留めた",
            "討ち取った",
            "退治した",
            "撃破した",
            "片づけた",
            "片付けた",
        ],
    );
    match outcome.as_str() {
        "hostile_defeated"
        | "explosion_death"
        | "creeper_detonated"
        | "charged_creeper_detonated" => kill,
        "disengaged" => {
            kill || has(
                &text,
                &[
                    "倒れた",
                    "死んだ",
                    "くたばった",
                    "撃破できた",
                    "退治できた",
                    "爆発した",
                    "爆散した",
                ],
            )
        }
        _ => false,
    }
}
