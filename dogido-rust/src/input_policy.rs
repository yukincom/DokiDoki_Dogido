//! player_input/guardrails.pyの純粋な読取り用分類。保存・操作の採否は決めない。
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::LazyLock;

static POLICY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("input-policy.json")).expect("checked input policy")
});
fn words(key: &str) -> impl Iterator<Item = &'static str> {
    POLICY[key]
        .as_array()
        .expect("checked input vocabulary")
        .iter()
        .map(|v| v.as_str().expect("checked input word"))
}
fn has(text: &str, key: &str) -> bool {
    words(key).any(|word| text.contains(word))
}
fn fold(text: &str) -> String {
    text.chars()
        .map(|c| {
            if ('ァ'..='ヶ').contains(&c) {
                char::from_u32(c as u32 - 0x60).unwrap()
            } else {
                c
            }
        })
        .collect()
}
fn space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
fn compile(pattern: &str) -> Regex {
    // Pythonのstr/reに含まれるC0空白を維持する。クラス内部の置換も集合の和。
    Regex::new(&pattern.replace(r"\s", r"[\s\u001c-\u001f]")).expect("checked input regex")
}
fn patterns(key: &str) -> Vec<Regex> {
    words(key).map(compile).collect()
}
static SHORT_INVENTORY: LazyLock<Regex> = LazyLock::new(|| {
    // 真偽値だけを使う検索なので、非対応の先読みは同じ末尾境界を消費して検査する。
    let pattern = POLICY["short_inventory"]
        .as_str()
        .unwrap()
        .replace(r"(?=$|[?？!！。、,])", r"(?:$|[?？!！。、,])");
    compile(&pattern)
});
static DIRECTION_STRIP: LazyLock<Regex> =
    LazyLock::new(|| compile(POLICY["direction_strip"].as_str().unwrap()));
static READING: LazyLock<Vec<Regex>> = LazyLock::new(|| patterns("reading_matches"));
static READING_SEARCH: LazyLock<Vec<Regex>> = LazyLock::new(|| patterns("reading_searches"));
static EXPLICIT_READING: LazyLock<Vec<Regex>> = LazyLock::new(|| patterns("explicit_matches"));

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReadingCorrection {
    pub surface: String,
    pub reading: String,
    pub wrong_reading: Option<String>,
    pub explicit: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Projection {
    pub wants_quiet: bool,
    pub should_block_ambient: bool,
    pub asks_hostile_count: bool,
    pub asks_hostile_direction: bool,
    pub asks_dragon_direction: bool,
    pub asks_save_last_haiku: bool,
    pub asks_inventory: bool,
    pub asks_about_sound: bool,
    pub player_haiku_text: Option<String>,
    pub revised_haiku_text: Option<String>,
    pub reading_correction: Option<ReadingCorrection>,
    pub is_explicit_reading_correction: bool,
}
fn payload(raw: &str, prefix_key: &str) -> Option<String> {
    let text = raw.trim_matches(space);
    let prefix = words(prefix_key).find(|prefix| text.starts_with(prefix))?;
    let replaced = text[prefix.len()..].replace('　', " ");
    let text = replaced.trim_matches(space);
    if text.is_empty() {
        return None;
    }
    let mut pieces = Vec::new();
    for separator in ['\n', '/', '／', '|', '｜'] {
        if text.contains(separator) {
            pieces = text
                .split(separator)
                .map(|p| p.trim_matches(space))
                .filter(|p| !p.is_empty())
                .map(str::to_owned)
                .collect();
            break;
        }
    }
    if pieces.is_empty() {
        pieces.push(
            text.split(space)
                .filter(|p| !p.is_empty())
                .collect::<Vec<_>>()
                .join(" "),
        );
    }
    Some(pieces.into_iter().take(3).collect::<Vec<_>>().join("\n"))
}
fn reading(text: &str, explicit: bool) -> Option<ReadingCorrection> {
    if text.is_empty() {
        return None;
    }
    for (index, regex) in READING.iter().enumerate() {
        let Some(capture) = regex.captures(text) else {
            continue;
        };
        let surface = capture[1].trim_matches(space);
        let reading = capture[2].trim_matches(space);
        if surface.is_empty() || reading.is_empty() || surface == reading {
            continue;
        }
        if index > 0 && surface.chars().count() > 20 {
            continue;
        }
        if index == 2
            && (!READING_SEARCH[1].is_match(surface)
                || reading.chars().count() > 16
                || READING_SEARCH[2].is_match(surface)
                || READING_SEARCH[3].is_match(reading))
        {
            continue;
        }
        return Some(ReadingCorrection {
            surface: surface.into(),
            reading: reading.into(),
            wrong_reading: None,
            explicit,
        });
    }
    let folded = fold(text);
    if let Some(capture) = READING_SEARCH[0].captures(&folded) {
        let (wrong, right) = (&capture[1], &capture[2]);
        if wrong != right {
            return Some(ReadingCorrection {
                surface: wrong.into(),
                reading: right.into(),
                wrong_reading: Some(wrong.into()),
                explicit: true,
            });
        }
    }
    None
}

/// `normalized`の再正規化はせず、句本文と読みだけは改行・引用を保持した`raw`から読む。
/// slash commandの早期returnと保存可否は呼出側が既存の規則で判断する。
/// 永続化用reading_correction::parseの強いguardをこの投影で置換しない。
pub fn classify(raw: &str, normalized: &str) -> Projection {
    let text = fold(normalized);
    let has_dragon = words("DRAGON_KEYWORDS").any(|word| text.contains(&fold(word)));
    let has_direction = has(&text, "DIRECTION_QUERY_KEYWORDS");
    let compact_direction = DIRECTION_STRIP.replace_all(&text, "");
    let asks_hostile_direction = !text.is_empty()
        && !has_dragon
        && has_direction
        && (has(&text, "HOSTILE_DIRECTION_REFERENTS")
            || ["どっち", "どこ", "どのへん", "どの辺", "方向", "方角"]
                .contains(&compact_direction.as_ref()));
    let possession = has(&text, "POSSESSION_HINT_KEYWORDS");
    let asks_inventory = !text.is_empty()
        && (has(&text, "INVENTORY_TOPIC_KEYWORDS")
            || (possession && has(&text, "INVENTORY_ITEM_HINT_KEYWORDS"))
            || SHORT_INVENTORY.is_match(&text)
            || (possession
                && ["何", "なに", "どんな"]
                    .iter()
                    .any(|word| text.contains(word))));
    let compact_sound = text.replace([' ', '　'], "");
    let asks_about_sound = !text.is_empty()
        && (has(&text, "_MUSIC_TOPIC_MARKERS")
            || (has(&text, "_SOUND_TOPIC_MARKERS")
                && (has(&text, "_SOUND_QUERY_SHAPES")
                    || (compact_sound.chars().count() <= 5
                        && has(&compact_sound, "_SOUND_TOPIC_MARKERS")))));
    let raw_text = raw.trim_matches(space);
    let explicit = !raw_text.is_empty() && EXPLICIT_READING.iter().any(|r| r.is_match(raw_text));
    Projection {
        wants_quiet: !text.is_empty() && has(&text, "HUSH_KEYWORDS"),
        should_block_ambient: !normalized.is_empty(),
        asks_hostile_count: !text.is_empty()
            && has(&text, "HOSTILE_COUNT_QUERY_KEYWORDS")
            && (has(&text, "HOSTILE_QUERY_KEYWORDS")
                || text.contains("残り")
                || text.contains("あと")),
        asks_hostile_direction,
        asks_dragon_direction: !text.is_empty() && has_dragon && has_direction,
        asks_save_last_haiku: !text.is_empty()
            && (has(&text, "SAVE_LAST_HAIKU_KEYWORDS")
                || (text.contains("保存")
                    && (text.contains("今の") || text.contains("さっきの"))
                    && (text.contains("句") || text.contains("川柳")))),
        asks_inventory,
        asks_about_sound,
        player_haiku_text: payload(raw, "HAIKU_SAVE_PREFIXES"),
        revised_haiku_text: payload(raw, "HAIKU_REVISE_PREFIXES"),
        reading_correction: reading(raw_text, explicit),
        is_explicit_reading_correction: explicit,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[derive(Deserialize)]
    struct Case {
        raw: String,
        normalized: String,
        expected: Projection,
    }
    #[test]
    fn pure_projection_matches_python_guardrails() {
        let cases: Vec<Case> =
            serde_json::from_str(include_str!("../fixtures/input-policy.json")).unwrap();
        for case in cases {
            assert_eq!(
                classify(&case.raw, &case.normalized),
                case.expected,
                "raw={:?}, normalized={:?}",
                case.raw,
                case.normalized
            );
        }
    }
    #[test]
    fn raw_multiline_and_interpreted_surface_stay_separate() {
        let value = classify("直し: 一行\n二行\n三行\n四行", "石炭ある？");
        assert_eq!(
            value.revised_haiku_text.as_deref(),
            Some("一行\n二行\n三行")
        );
        assert!(value.asks_inventory);
        let value = classify("「草地はくさち」", "草地はくさち");
        assert!(value.reading_correction.is_none());
    }
}
